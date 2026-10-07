"""Current query observations and historical originals have distinct ownership."""

import asyncio
import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.corpus_evidence import CorpusEvidenceBundle, CorpusEvidenceReader
from ghimera.corpus_types import BoundCorpusDocument, CorpusRecord
from ghimera.embedding_types import EncodingCall
from ghimera.models import Document
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research_reuse_config import ResearchReuseConfig

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def bundle_digest(bundle: CorpusEvidenceBundle) -> str:
    return hashlib.sha256(bundle.model_dump_json().encode()).hexdigest()


class RetrievalObservation(CorpusRecord):
    query_text: Annotated[str, Field(min_length=1)]
    outcome: Literal["success", "refused", "cancelled"]
    encoding_call: EncodingCall | None
    bundle_sha256: Digest | None
    reason: Literal["snapshot_admitted", "query_refused", "query_cancelled"]

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
    schema_version: Literal["ghimera.research-retrieval/1"] = Field(alias="schema")
    policy: ResearchReuseConfig
    intent: Annotated[str, Field(min_length=1)]
    observations: tuple[RetrievalObservation, ...]
    snapshots: tuple[CorpusEvidenceBundle, ...]

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
    ) -> None:
        self._policy = ResearchReuseConfig.model_validate(policy.model_dump())
        self._reader, self._intent = reader, intent
        if reader.policy != self._policy.reader:
            raise ValueError("research reuse requires its exact configured reader")
        initial = (
            ResearchRetrievalReport.model_validate(restored.model_dump()) if restored else None
        )
        if initial is not None and (initial.policy != self._policy or initial.intent != intent):
            raise ValueError("resumed retrieval requires its original intent and policy")
        self._observations = list(initial.observations) if initial is not None else []
        self._snapshots = list(initial.snapshots) if initial is not None else []
        self._active = False
        _ = self.report

    @property
    def report(self) -> ResearchRetrievalReport:
        if self._active:
            raise ValueError("retrieval cannot checkpoint an active query")
        return ResearchRetrievalReport(
            schema="ghimera.research-retrieval/1",
            policy=self._policy,
            intent=self._intent,
            observations=tuple(self._observations),
            snapshots=tuple(self._snapshots),
        )

    async def query(self, text: str, *, remaining_seconds: float) -> None:
        if self._active:
            raise ValueError("one retained research session cannot overlap queries")
        if any(bundle.query_text == text for bundle in self._snapshots):
            return  # Includes a successful empty query; resume does not repeat it.
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
        call: EncodingCall | None = None
        bundle: CorpusEvidenceBundle | None = None
        outcome: Literal["success", "refused", "cancelled"] = "refused"
        self._active = True

        def observe(value: EncodingCall) -> None:
            nonlocal call
            if call is not None:
                raise ValueError("one corpus query must not make multiple encoding calls")
            call = EncodingCall.model_validate(value.model_dump())

        try:
            async with asyncio.timeout(min(remaining_seconds, reader.timeout_seconds)):
                candidate = await self._reader.read(text, encoding_observer=observe)
            probe = ResearchRetrievalReport(
                schema="ghimera.research-retrieval/1",
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
                    ),
                ),
                snapshots=tuple(self._snapshots) + (candidate,),
            )
            bundle, outcome = probe.snapshots[-1], "success"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except (ValueError, OSError, TimeoutError) as exc:
            raise GhimeraRefused(RefusalCode.SEARCH_UNAVAILABLE) from exc
        finally:
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
                )
            )
            if bundle is not None:
                self._snapshots.append(bundle)
            self._active = False
