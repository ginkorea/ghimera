"""Use the original journal/model-work owner for CPU rerank reservations and ACKs."""

import hashlib
from typing import TYPE_CHECKING, Literal

from ghimera.corpus_config import CorpusConfig
from ghimera.corpus_evidence_config import CorpusEvidenceConfig
from ghimera.corpus_search_config import CorpusSearchConfig
from ghimera.ledger import Ledger
from ghimera.model_work import (
    FatalModelWorkFailure,
    ModelInvocation,
    port_input,
    record_output,
    uncertain_model_sequences,
)
from ghimera.models import LedgerRow, ModelIdentity
from ghimera.reranking_types import PassageReranker, RerankRequest, RerankScores
from ghimera.research_reranking_types import (
    RerankDecision,
    RerankReservation,
    RerankRunEvidence,
    RunRerankResult,
)
from ghimera.retrieval import HybridRetrievalConfig, RetrievalEvidence

if TYPE_CHECKING:
    from ghimera.budget import RunBudget
    from ghimera.config import GhimeraConfig


def _model(request: RerankRequest) -> ModelIdentity:
    return ModelIdentity(
        model_id=request.policy.model_id,
        revision=request.policy.revision,
        location="self_hosted",
    )


def _admit_corpus(
    config: "GhimeraConfig",
    channel: Literal["discovery", "retained"],
    corpus: CorpusConfig,
    corpus_id: str,
    retrieval: HybridRetrievalConfig | None,
) -> None:
    research = config.research
    if (
        research is None
        or research.reranking is None
        or config.journal is None
        or config.model_work is None
        or config.model_work.results is None
    ):
        raise ValueError("learned research requires its original retained durable run policy")
    bindings: tuple[CorpusEvidenceConfig | CorpusSearchConfig, ...]
    if channel == "retained":
        reuse = research.retained_evidence
        bindings = (reuse.reader,) if reuse is not None else ()
    else:
        candidates = (
            tuple(p.binding for p in config.discovery.providers)
            if config.discovery is not None
            else (config.search,)
            if config.search is not None
            else ()
        )
        bindings = tuple(p for p in candidates if isinstance(p, CorpusSearchConfig))
    if not any(
        binding.corpus_id == corpus_id
        and binding.corpus_config_sha256 == corpus.identity
        and binding.query_encoder == corpus.query_encoder
        and binding.retrieval == retrieval
        for binding in bindings
    ):
        raise ValueError("reranking is not bound to this run's exact native corpus reader")


def _admitted(config: "GhimeraConfig", reservation: RerankReservation) -> None:
    _admit_corpus(
        config,
        reservation.channel,
        reservation.corpus,
        reservation.request.corpus_id,
        reservation.retrieval.policy,
    )


def validate_rerank_rows(
    config: "GhimeraConfig", rows: tuple[LedgerRow, ...]
) -> tuple[int, int, int]:
    """Count originals including UNKNOWN; validate every retained CPU return/replay."""
    from ghimera.budget import RunBudget

    uncertain_model_sequences(rows)
    calls = pairs = chars = 0
    keys: set[tuple[str, str]] = set()
    for row in rows:
        reservation = row.rerank_reservation
        if reservation is None:
            continue
        _admitted(config, reservation)
        # Reparse nested source proofs, even for model_copy-created caller objects.
        reservation = RerankReservation.model_validate(reservation.model_dump())
        key = (reservation.channel, reservation.operation_key)
        if key in keys:
            raise ValueError("one fresh rerank operation cannot reserve multiple invocations")
        keys.add(key)
        wire = port_input(RunBudget(config, lambda: 0.0), reservation)
        intent = row.model_intent
        if (
            intent is None
            or intent.phase != "reranking"
            or row.model != _model(reservation.request)
            or row.url is not None
            or intent.input_scope != "port_input"
            or intent.input_sha256 != hashlib.sha256(wire).hexdigest()
            or intent.input_bytes != len(wire)
        ):
            raise ValueError("rerank intent changed its exact native request or CPU identity")
        calls += 1
        pairs += len(reservation.request.candidates)
        chars += reservation.input_chars
        for acknowledged in rows:
            ack = acknowledged.model_ack
            if ack is None or ack.intent_sequence != row.sequence or ack.outcome != "returned":
                continue
            if ack.stored_output is None or ack.output_scope != "port_output":
                raise ValueError("rerank ACK must retain the original CPU scores")
            scores = RerankScores.model_validate_json(ack.stored_output.body())
            scores.validate_request(reservation.request)
    policy = config.research.reranking if config.research is not None else None
    if policy is not None and (
        calls > policy.max_calls or pairs > policy.max_pairs or chars > policy.max_input_chars
    ):
        raise ValueError("original rerank reservations exceed their declared run allowance")
    return calls, pairs, chars


def validate_run_evidence(
    config: "GhimeraConfig",
    rows: tuple[LedgerRow, ...],
    request: RerankRequest,
    scores: RerankScores,
    evidence: RerankRunEvidence | None,
    *,
    channel: Literal["discovery", "retained"],
    before_sequence: int | None = None,
) -> None:
    validate_rerank_rows(config, rows)
    if evidence is None or not 0 <= evidence.intent_sequence < evidence.ack_sequence < len(rows):
        raise ValueError("learned research requires its original run reservation and ACK")
    original, acknowledged = rows[evidence.intent_sequence], rows[evidence.ack_sequence]
    reservation, ack = original.rerank_reservation, acknowledged.model_ack
    if (
        reservation is None
        or reservation.channel != channel
        or reservation.request != request
        or reservation.sha256 != evidence.reservation_sha256
        or ack is None
        or ack.intent_sequence != original.sequence
        or ack.outcome != "returned"
        or ack.stored_output is None
        or ack.output_sha256 != evidence.output_sha256
        or ack.output_bytes != evidence.output_bytes
        or record_output(scores) != ack.stored_output.body()
        or (before_sequence is not None and evidence.ack_sequence >= before_sequence)
    ):
        raise ValueError("learned research scores changed their exact original acknowledged work")
    scores.validate_request(request)
    if evidence.replay_sequence is not None:
        if evidence.replay_sequence >= len(rows) or (
            before_sequence is not None and evidence.replay_sequence >= before_sequence
        ):
            raise ValueError("rerank local read is outside its original owning prefix")
        replay = rows[evidence.replay_sequence].model_replay
        if (
            replay is None
            or replay.intent_sequence != evidence.intent_sequence
            or replay.ack_sequence != evidence.ack_sequence
            or replay.output_sha256 != evidence.output_sha256
            or replay.output_bytes != evidence.output_bytes
        ):
            raise ValueError("rerank local read requires its exact original ACK")


class RunBoundReranker:
    """One explicit decision, same native budget and durable journal as research."""

    def __init__(
        self,
        budget: "RunBudget",
        ledger: Ledger,
        *,
        channel: Literal["discovery", "retained"],
        decision: RerankDecision,
    ) -> None:
        self._budget, self._ledger, self._channel = budget, ledger, channel
        self._decision = RerankDecision.model_validate(decision.model_dump())
        self._used = False

    @property
    def replaying(self) -> bool:
        return self._decision.action == "replay"

    def admit(
        self,
        corpus: CorpusConfig,
        corpus_id: str,
        *,
        query: str,
        retrieval: HybridRetrievalConfig | None,
        generation: int | None = None,
    ) -> None:
        budget, ledger, decision = self._budget, self._ledger, self._decision
        policy = budget.config.research.reranking if budget.config.research is not None else None
        if (
            policy is None
            or budget.config.model_work is None
            or budget.config.model_work.results is None
            or corpus.reranking is None
            or corpus.encoding_recovery is None
            or not ledger.has_replay_binding(budget.config)
        ):
            raise FatalModelWorkFailure(
                "learned research needs its exact durable and corpus ACK owners"
            )
        _admit_corpus(budget.config, self._channel, corpus, corpus_id, retrieval)
        rows = ledger.snapshot()
        usage = validate_rerank_rows(budget.config, rows)
        if usage != (budget.rerank_calls, budget.rerank_pairs, budget.rerank_chars):
            raise FatalModelWorkFailure("rerank budget lost original reservations")
        matching = tuple(
            row
            for row in rows
            if row.rerank_reservation is not None
            and row.rerank_reservation.channel == self._channel
            and row.rerank_reservation.operation_key == decision.operation_key
        )
        if decision.action == "fresh":
            if matching or any(
                rows[i].rerank_reservation is not None and i not in budget.active_rerank_sequences
                for i in uncertain_model_sequences(rows)
            ):
                raise FatalModelWorkFailure(
                    "original rerank work is held; select its exact ACK explicitly"
                )
            budget.check_rerank(0, 0)
        else:
            sequence = decision.original_intent_sequence
            if (
                sequence is None
                or len(matching) != 1
                or matching[0].sequence != sequence
                or matching[0].rerank_reservation is None
                or matching[0].rerank_reservation.corpus != corpus
                or matching[0].rerank_reservation.request.corpus_id != corpus_id
                or matching[0].rerank_reservation.request.query != query
                or (
                    generation is not None
                    and matching[0].rerank_reservation.request.generation != generation
                )
                or sequence in uncertain_model_sequences(rows)
            ):
                raise FatalModelWorkFailure(
                    "rerank replay requires its exact original acknowledged corpus"
                )

    async def score(
        self,
        request: RerankRequest,
        reranker: PassageReranker,
        *,
        corpus: CorpusConfig,
        retrieval: RetrievalEvidence,
    ) -> RunRerankResult:
        if self._used or reranker.config != request.policy:
            raise FatalModelWorkFailure("one run-bound rerank decision cannot change or be reused")
        self.admit(
            corpus,
            request.corpus_id,
            query=request.query,
            retrieval=retrieval.policy,
            generation=request.generation,
        )
        self._used = True
        reservation = RerankReservation(
            schema="ghimera.rerank-reservation/1",
            channel=self._channel,
            operation_key=self._decision.operation_key,
            corpus=corpus,
            retrieval=retrieval,
            request=request,
        )
        _admitted(self._budget.config, reservation)
        wire = port_input(self._budget, reservation)
        replaying = self._decision.original_intent_sequence
        if replaying is None:
            self._budget.check_rerank(len(request.candidates), reservation.input_chars)
        sequence = replaying if replaying is not None else self._ledger.next_sequence
        invocation = ModelInvocation(
            self._budget,
            self._ledger,
            phase="reranking",
            model=_model(request),
            request=wire,
            replay_intent_sequence=replaying,
            rerank_reservation=reservation,
        )

        def decode(body: bytes) -> RerankScores:
            scores = RerankScores.model_validate_json(body)
            scores.validate_request(request)
            return scores

        async def call() -> RerankScores:
            # Validate before recording returned, not only before consumer application.
            result = await reranker.score(request)
            if reranker.config != request.policy:
                raise ValueError("reranker changed its configured recipe during contact")
            return decode(record_output(result))

        if replaying is not None:
            scores = invocation.replay(lambda stored: decode(stored.body()))
        else:
            with self._budget.rerank_contact(sequence):
                scores = await invocation.invoke(call, record_output)
        rows = self._ledger.snapshot()
        acknowledged = next(
            row
            for row in rows
            if row.model_ack is not None and row.model_ack.intent_sequence == sequence
        )
        ack = acknowledged.model_ack
        if ack is None or ack.output_sha256 is None or ack.output_bytes is None:
            raise FatalModelWorkFailure("rerank return lost its original durable ACK")
        evidence = RerankRunEvidence(
            schema="ghimera.rerank-run/1",
            reservation_sha256=reservation.sha256,
            intent_sequence=sequence,
            ack_sequence=acknowledged.sequence,
            output_sha256=ack.output_sha256,
            output_bytes=ack.output_bytes,
            replay_sequence=rows[-1].sequence if replaying is not None else None,
        )
        validate_run_evidence(
            self._budget.config, rows, request, scores, evidence, channel=self._channel
        )
        return RunRerankResult(scores=scores, evidence=evidence)
