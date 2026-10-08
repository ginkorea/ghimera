"""Current query observations and historical originals have distinct ownership."""

import asyncio
import hashlib
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, model_validator

from ghimera.corpus_evidence import CorpusEvidenceBundle, CorpusEvidenceReader
from ghimera.corpus_types import BoundCorpusDocument, CorpusRecord
from ghimera.embedding_types import EncodingCall, EncodingRecoveryEvidence
from ghimera.graph_types import GraphRetainedOrigin
from ghimera.models import Document, LedgerRow, RetainedOriginal
from ghimera.query_work import QueryWork
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research_reranking import RunBoundReranker, validate_run_evidence
from ghimera.research_reranking_types import RerankDecision, ResearchRerankingConfig
from ghimera.research_reuse_config import ResearchReuseConfig

if TYPE_CHECKING:
    from ghimera.budget import RunBudget
    from ghimera.config import GhimeraConfig
    from ghimera.ledger import Ledger
    from ghimera.models import LedgerRow

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def bundle_digest(bundle: CorpusEvidenceBundle) -> str:
    return hashlib.sha256(bundle.model_dump_json().encode()).hexdigest()


class RetrievalObservation(CorpusRecord):
    query_text: Annotated[str, Field(min_length=1)]
    outcome: Literal["success", "refused", "cancelled"]
    encoding_call: EncodingCall | None
    bundle_sha256: Digest | None
    reason: Literal["snapshot_admitted", "query_refused", "query_cancelled"]
    encoding_recovery: EncodingRecoveryEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def terminal(self) -> "RetrievalObservation":
        if (
            not self.query_text.strip()
            or (self.outcome == "success") != (self.bundle_sha256 is not None)
            or (self.outcome == "success") != (self.reason == "snapshot_admitted")
            or (self.outcome == "cancelled") != (self.reason == "query_cancelled")
            or (
                self.outcome == "success"
                and (self.encoding_call is None or self.encoding_call.outcome != "success")
            )
        ):
            raise ValueError("retrieval requires a terminal query observation, not a fresh fetch")
        return self


class RetainedSourceNotice(CorpusRecord):
    document_sha256: Digest
    source_sha256: Digest
    text_sha256: Digest
    source_url: str
    source_mode: Literal["retained_snapshot"] = "retained_snapshot"
    source_age: Literal["unknown"] = "unknown"


def validate_notices(
    notices: tuple[RetainedSourceNotice, ...], documents: tuple[Document, ...]
) -> None:
    if not notices:
        return
    originals = {BoundCorpusDocument(doc).identity: doc for doc in documents}
    if len({notice.document_sha256 for notice in notices}) != len(notices):
        raise ValueError("retained source notices cannot repeat a representation")
    for notice in notices:
        doc = originals.get(notice.document_sha256)
        if doc is None or (
            notice.source_url != doc.url
            or notice.source_sha256 != doc.sha256
            or notice.text_sha256 != hashlib.sha256(doc.extracted.text.encode()).hexdigest()
        ):
            raise ValueError("retained source notices must bind the exact original representation")


class ResearchRetrievalReport(CorpusRecord):
    schema_version: Literal["ghimera.research-retrieval/1", "ghimera.research-retrieval/2"] = Field(
        alias="schema"
    )
    policy: ResearchReuseConfig
    intent: Annotated[str, Field(min_length=1)]
    observations: tuple[RetrievalObservation, ...]
    snapshots: tuple[CorpusEvidenceBundle, ...]
    reranking_policy: ResearchRerankingConfig | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    def validate_run(self, config: "GhimeraConfig", rows: tuple["LedgerRow", ...]) -> None:
        policy = config.research.reranking if config.research is not None else None
        if self.reranking_policy != policy:
            raise ValueError("retained reranking must preserve its original run policy")
        for bundle in self.snapshots:
            learned = bundle.query.reranking
            if learned is not None:
                validate_run_evidence(
                    config,
                    rows,
                    learned.request,
                    learned.scores,
                    bundle.query.reranking_run,
                    channel="retained",
                )

    @property
    def graph_originals(self) -> tuple[RetainedOriginal, ...]:
        """First successful query owns graph admission; repeated hits never redo semantics."""
        originals: dict[str, RetainedOriginal] = {}
        for bundle in self.snapshots:
            query = bundle.query
            for document in bundle.sources:
                identity = BoundCorpusDocument(document).identity
                if identity in originals:
                    continue
                originals[identity] = RetainedOriginal(
                    document=document,
                    query_text=bundle.query_text,
                    encoding_call=query.encoding_call,
                    origin=GraphRetainedOrigin(
                        schema="ghimera.graph-retained-origin/1",
                        document_sha256=identity,
                        corpus_id=query.corpus_id,
                        config_sha256=query.config_sha256,
                        generation=query.generation,
                        bundle_sha256=bundle_digest(bundle),
                        query_sha256=query.query_sha256,
                        encoding_call_sha256=hashlib.sha256(
                            query.encoding_call.model_dump_json().encode()
                        ).hexdigest(),
                    ),
                )
        return tuple(originals.values())

    @property
    def documents(self) -> tuple[Document, ...]:
        originals: dict[str, Document] = {}
        for bundle in self.snapshots:
            for source in bundle.sources:
                originals[BoundCorpusDocument(source).identity] = source
        return tuple(originals.values())

    @property
    def notices(self) -> tuple[RetainedSourceNotice, ...]:
        return tuple(
            RetainedSourceNotice(
                document_sha256=BoundCorpusDocument(doc).identity,
                source_sha256=doc.sha256,
                text_sha256=hashlib.sha256(doc.extracted.text.encode()).hexdigest(),
                source_url=doc.url,
            )
            for doc in self.documents
        )

    @model_validator(mode="after")
    def reconcile(self) -> "ResearchRetrievalReport":
        if (self.schema_version == "ghimera.research-retrieval/2") != (
            self.reranking_policy is not None
        ):
            raise ValueError("learned research requires its versioned run-policy report")
        if any(
            bundle.query.reranking is not None
            and (self.reranking_policy is None or bundle.query.reranking_run is None)
            for bundle in self.snapshots
        ):
            raise ValueError(
                "learned research snapshots require original run-bound rerank reservation/ACK proof"
            )
        policy = self.policy
        reader = policy.reader
        snapshots = {bundle_digest(bundle): bundle for bundle in self.snapshots}
        admitted = tuple(
            observation.bundle_sha256
            for observation in self.observations
            if observation.outcome == "success"
        )
        if (
            not self.intent.strip()
            or len(self.observations) > policy.max_queries
            or sum(
                len(reader.query_encoder.text_prefix) + len(item.query_text)
                for item in self.observations
            )
            > policy.max_input_chars
            or len(snapshots) != len(self.snapshots)
            or len(set(admitted)) != len(admitted)
            or set(admitted) != snapshots.keys()
            or len(self.documents) > policy.max_source_documents
            or sum(len(bundle.model_dump_json().encode()) for bundle in self.snapshots)
            > policy.max_snapshot_bytes
        ):
            raise ValueError("retrieval report must reconcile its exact bounded snapshot queries")
        for observation in self.observations:
            text, call = observation.query_text, observation.encoding_call
            encoded = reader.query_encoder.text_prefix + text
            if len(text) > reader.max_query_chars or (
                call is not None
                and (
                    call.service != reader.query_encoder
                    or call.input_chars != len(encoded)
                    or call.input_sha256 != (hashlib.sha256(encoded.encode()).hexdigest(),)
                )
            ):
                raise ValueError("retrieval observations must bind the configured query model")
            if observation.bundle_sha256 is not None:
                bundle = snapshots[observation.bundle_sha256]
                if (
                    bundle.policy != reader
                    or bundle.query_text != text
                    or bundle.query.encoding_call != call
                    or bundle.query.encoding_recovery != observation.encoding_recovery
                ):
                    raise ValueError("retained snapshot must bind its actual current query")
        return self


class RetainedResearchSession:
    """Own run-local query allowance and checkpoints; borrow the corpus reader."""

    def __init__(
        self,
        policy: ResearchReuseConfig,
        reader: CorpusEvidenceReader,
        intent: str,
        *,
        restored: ResearchRetrievalReport | None = None,
        budget: "RunBudget | None" = None,
        ledger: "Ledger | None" = None,
    ) -> None:
        self._policy = ResearchReuseConfig.model_validate(policy.model_dump())
        self._budget, self._ledger = budget, ledger
        self._reranking = (
            budget.config.research.reranking
            if budget is not None and budget.config.research is not None
            else None
        )
        if reader.reranking_policy is not None and (
            budget is None or ledger is None or self._reranking is None
        ):
            raise ValueError("learned research retrieval requires original run-bound rerank owners")
        self._reader, self._intent = reader, intent
        if reader.policy != self._policy.reader:
            raise ValueError("research reuse requires its exact configured reader")
        initial = (
            ResearchRetrievalReport.model_validate(restored.model_dump()) if restored else None
        )
        if initial is not None and (initial.policy != self._policy or initial.intent != intent):
            raise ValueError("resumed retrieval requires its original intent and policy")
        if initial is not None and budget is not None and ledger is not None:
            initial.validate_run(budget.config, ledger.snapshot())
        self._observations = list(initial.observations) if initial is not None else []
        self._snapshots = list(initial.snapshots) if initial is not None else []
        self._active = False
        _ = self.report

    @property
    def report(self) -> ResearchRetrievalReport:
        if self._active:
            raise ValueError("retrieval cannot checkpoint an active query")
        return ResearchRetrievalReport(
            schema="ghimera.research-retrieval/2"
            if self._reranking is not None
            else "ghimera.research-retrieval/1",
            policy=self._policy,
            intent=self._intent,
            observations=tuple(self._observations),
            snapshots=tuple(self._snapshots),
            reranking_policy=self._reranking,
        )

    async def query(
        self,
        text: str,
        *,
        remaining_seconds: float,
        rerank_decision: RerankDecision | None = None,
        query_work: QueryWork | None = None,
    ) -> None:
        if self._active:
            raise ValueError("one retained research session cannot overlap queries")
        if (
            query_work is None
            and any(bundle.query_text == text for bundle in self._snapshots)
            and (rerank_decision is None or rerank_decision.action == "fresh")
        ):
            return  # Includes a successful empty query; resume does not repeat it.
        invoker = None
        reader, policy = self._policy.reader, self._policy
        if not text.strip() or len(text) > reader.max_query_chars:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        if (
            remaining_seconds <= 0
            or len(self._observations) >= policy.max_queries
            or sum(
                len(reader.query_encoder.text_prefix) + len(item.query_text)
                for item in self._observations
            )
            + len(reader.query_encoder.text_prefix)
            + len(text)
            > policy.max_input_chars
        ):
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        if query_work is not None:
            reservation = query_work.prepare(
                "retained",
                text.encode(),
                ("retained-corpus", self._reader.policy.corpus_config_sha256),
                corpus=self._reader.query_binding(text),
                retained_reservation=len(self._observations) + 1,
                input_chars=len(reader.query_encoder.text_prefix) + len(text),
                rerank_operation_key=rerank_decision.operation_key
                if rerank_decision is not None
                else None,
            )
            if query_work.resuming:
                retained = (
                    CorpusEvidenceBundle.model_validate_json(query_work.ack.result_json)
                    if query_work.ack is not None and query_work.ack.result_json is not None
                    else None
                )
                self._reader.admit_query(reservation, bundle=retained)
            query_work.commit()
            rerank_decision = query_work.decision(rerank_decision)
        if self._reader.reranking_policy is not None:
            if self._budget is None or self._ledger is None or rerank_decision is None:
                raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
            invoker = RunBoundReranker(
                self._budget, self._ledger, channel="retained", decision=rerank_decision
            )
        call: EncodingCall | None = None
        recovered: EncodingRecoveryEvidence | None = None
        bundle: CorpusEvidenceBundle | None = None
        outcome: Literal["success", "refused", "cancelled"] = "refused"
        cancelled = False
        self._active = True

        def observe(value: EncodingCall) -> None:
            nonlocal call
            if call is not None:
                raise ValueError("one corpus query must not make multiple encoding calls")
            call = EncodingCall.model_validate(value.model_dump())

        try:
            async with asyncio.timeout(min(remaining_seconds, reader.timeout_seconds)):
                if (
                    query_work is not None
                    and query_work.ack is not None
                    and query_work.ack.result_json is not None
                ):
                    candidate = CorpusEvidenceBundle.model_validate_json(query_work.ack.result_json)
                    call = candidate.query.encoding_call
                else:
                    candidate = await self._reader.read(
                        text,
                        encoding_observer=observe,
                        rerank_invoker=invoker,
                        query_reservation=query_work.reservation
                        if query_work is not None
                        else None,
                        resume_operation=query_work is not None and query_work.resuming,
                    )
            recovered = candidate.query.encoding_recovery
            if call is None:
                if recovered is None or not recovered.reused:
                    raise ValueError("query without a new call requires its acknowledged recovery")
                # The original audited call is evidence, not a new observer
                # callback or another provider charge. Query allowance still
                # records this read; corpus lifetime reservations never reset.
                call = EncodingCall.model_validate(candidate.query.encoding_call.model_dump())
            probe = ResearchRetrievalReport(
                schema="ghimera.research-retrieval/2"
                if self._reranking is not None
                else "ghimera.research-retrieval/1",
                policy=policy,
                intent=self._intent,
                observations=tuple(self._observations)
                + (
                    RetrievalObservation(
                        query_text=text,
                        outcome="success",
                        encoding_call=call,
                        bundle_sha256=bundle_digest(candidate),
                        reason="snapshot_admitted",
                        encoding_recovery=recovered,
                    ),
                ),
                snapshots=tuple(self._snapshots) + (candidate,),
                reranking_policy=self._reranking,
            )
            if self._budget is not None and self._ledger is not None:
                probe.validate_run(self._budget.config, self._ledger.snapshot())
            bundle, outcome = probe.snapshots[-1], "success"
            if query_work is not None and query_work.ack is None:
                acknowledged = query_work.acknowledgement(bundle.model_dump_json().encode())
                query_work.ledger.append(
                    LedgerRow(
                        sequence=query_work.ledger.next_sequence,
                        event="query_ack",
                        reason="retained_query_ended",
                        query_ack=acknowledged,
                    )
                )
                query_work.ack = acknowledged
        except asyncio.CancelledError:
            outcome = "cancelled"
            cancelled = True
            raise
        except (ValueError, OSError, TimeoutError) as exc:
            raise GhimeraRefused(RefusalCode.SEARCH_UNAVAILABLE) from exc
        finally:
            if query_work is not None and query_work.ack is None:
                acknowledged = query_work.acknowledgement(
                    None, "cancelled" if cancelled else "refused"
                )
                query_work.ledger.append(
                    LedgerRow(
                        sequence=query_work.ledger.next_sequence,
                        event="query_ack",
                        reason="retained_query_ended",
                        query_ack=acknowledged,
                    )
                )
                query_work.ack = acknowledged
            self._observations.append(
                RetrievalObservation(
                    query_text=text,
                    outcome=outcome,
                    encoding_call=call,
                    bundle_sha256=bundle_digest(bundle) if bundle is not None else None,
                    reason="snapshot_admitted"
                    if bundle is not None
                    else "query_cancelled"
                    if outcome == "cancelled"
                    else "query_refused",
                    encoding_recovery=recovered,
                )
            )
            if bundle is not None:
                self._snapshots.append(bundle)
            self._active = False
