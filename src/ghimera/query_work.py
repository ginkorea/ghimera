"""Native query reservation/ACK plumbing; no separate persistence or retry owner."""

import hashlib
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from ghimera.query_work_types import (
    QueryAcknowledgement,
    QueryCorpusBinding,
    QueryCursor,
    QueryReservation,
)
from ghimera.research_reranking_types import RerankDecision

if TYPE_CHECKING:
    from ghimera.budget import RunBudget
    from ghimera.config import GhimeraConfig
    from ghimera.ledger import Ledger
    from ghimera.models import LedgerRow


class QueryWork:
    """One explicitly selected serial call, owned by its research driver."""

    def __init__(
        self,
        budget: "RunBudget",
        ledger: "Ledger",
        cursor: QueryCursor,
        before: Callable[[QueryReservation], None],
        *,
        original: QueryReservation | None = None,
        original_sequence: int | None = None,
        acknowledgement: QueryAcknowledgement | None = None,
        acknowledgement_sequence: int | None = None,
        rerank_sequence: int | None = None,
    ) -> None:
        self.budget, self.ledger, self.cursor, self._before = budget, ledger, cursor, before
        self.original, self.sequence = original, original_sequence
        self.ack, self.ack_sequence = acknowledgement, acknowledgement_sequence
        self.rerank_sequence = rerank_sequence
        self._prepared: QueryReservation | None = None
        self.resuming = original_sequence is not None
        self._committed = self.resuming

    def prepare(
        self,
        channel: Literal["discovery", "retained"],
        request: bytes,
        provider: tuple[str, str],
        *,
        corpus: QueryCorpusBinding | None = None,
        retained_reservation: int | None = None,
        input_chars: int = 0,
        rerank_operation_key: str | None = None,
    ) -> QueryReservation:
        if self._prepared is not None:
            raise ValueError("one query work cannot prepare twice")
        if not self.ledger.has_replay_binding(self.budget.config):
            raise ValueError("query work requires its exact owning native committed journal")
        if self.original is not None:
            original = self.original
            if (
                original.cursor != self.cursor
                or original.channel != channel
                or (original.provider, original.provider_revision) != provider
                or original.request_json != request
                or original.corpus != corpus
                or original.retained_reservation != retained_reservation
                or original.input_chars != input_chars
                or original.rerank_operation_key != rerank_operation_key
            ):
                raise ValueError("query replay changed its exact original reservation")
            reservation = original
        else:
            reservation = QueryReservation(
                schema="ghimera.query-reservation/1",
                operation_id=uuid.uuid4().hex,
                cursor=self.cursor,
                channel=channel,
                provider=provider[0],
                provider_revision=provider[1],
                request_json=request,
                request_sha256=hashlib.sha256(request).hexdigest(),
                search_reservation=self.budget.search_calls + 1 if channel == "discovery" else None,
                fetch_reservation=self.budget.fetches + 1 if channel == "discovery" else None,
                retained_reservation=retained_reservation,
                input_chars=input_chars,
                corpus=corpus,
                rerank_operation_key=rerank_operation_key,
            )
            self._before(reservation)
        self._prepared = reservation
        return reservation

    def commit(self) -> None:
        from ghimera.models import LedgerRow

        if self._prepared is None:
            raise ValueError("query requires its prepared original control")
        if not self._committed:
            self.sequence = self.ledger.next_sequence
            self.ledger.append(
                LedgerRow(
                    sequence=self.sequence,
                    event="query_intent",
                    reason="query_reserved",
                    query_reservation=self._prepared,
                )
            )
            self._committed = True
        elif (
            self.sequence is None
            or self.ledger.snapshot()[self.sequence].query_reservation != self._prepared
        ):
            raise ValueError("query replay lost its native original intent")

    @property
    def reservation(self) -> QueryReservation:
        if self._prepared is None:
            raise ValueError("query has no prepared original reservation")
        return self._prepared

    def acknowledgement(
        self, body: bytes | None, outcome: str = "returned"
    ) -> QueryAcknowledgement:
        if self.sequence is None:
            raise ValueError("query cannot acknowledge before its original reservation")
        return QueryAcknowledgement.model_validate(
            dict(
                schema="ghimera.query-ack/1",
                reservation_sequence=self.sequence,
                outcome=outcome,
                result_json=body,
                result_sha256=hashlib.sha256(body).hexdigest() if body is not None else None,
            )
        )

    def decision(self, fallback: RerankDecision | None) -> RerankDecision | None:
        if self.rerank_sequence is None:
            return fallback
        row = self.ledger.snapshot()[self.rerank_sequence]
        if row.rerank_reservation is None:
            raise ValueError("query score replay lost its original native reservation")
        return RerankDecision(
            schema="ghimera.rerank-decision/1",
            action="replay",
            operation_key=row.rerank_reservation.operation_key,
            original_intent_sequence=row.sequence,
        )


def validate_query_rows(config: "GhimeraConfig", rows: tuple["LedgerRow", ...]) -> tuple[int, ...]:
    """Validate complete and UNKNOWN originals; no omitted or double-charged call."""
    from ghimera.corpus_evidence import CorpusEvidenceBundle
    from ghimera.models import LedgerRow, count_fetch_attempts
    from ghimera.research_reranking import validate_run_evidence
    from ghimera.research_types import SearchRequest, SearchResponse

    pending: dict[int, QueryReservation] = {}
    operations: set[str] = set()
    searches = retained = chars = 0
    policy = config.research_recovery
    for row in rows:
        reservation = row.query_reservation
        if reservation is not None:
            if (
                policy is None
                or policy.query_control != "serial_acknowledged"
                or reservation.operation_id in operations
            ):
                raise ValueError(
                    "query work needs its original explicit policy and unique operation"
                )
            if pending:
                raise ValueError("serial query recovery cannot overlap original UNKNOWN calls")
            if row != LedgerRow(
                sequence=row.sequence,
                event="query_intent",
                reason="query_reserved",
                query_reservation=reservation,
            ):
                raise ValueError("query intent must contain only its original reservation")
            operations.add(reservation.operation_id)
            pending[row.sequence] = reservation
            if reservation.channel == "discovery":
                searches += 1
                request = SearchRequest.model_validate_json(reservation.request_json)
                if (
                    reservation.search_reservation
                    != sum(
                        previous.query_reservation is not None
                        and previous.query_reservation.channel == "discovery"
                        or previous.event == "fetch"
                        and previous.query_ack is None
                        and previous.route is not None
                        and previous.route.startswith("search:")
                        for previous in rows[: row.sequence]
                    )
                    + 1
                    or reservation.fetch_reservation
                    != count_fetch_attempts(config, rows[: row.sequence]) + 1
                ):
                    raise ValueError("query reservation changed its original fetch/search debit")
                if (
                    config.research is None
                    or searches > config.research.query_budget
                    or len(request.query.text) > config.research.max_query_chars
                    or request.limit > config.research.results_per_query
                    or request.max_bytes
                    > min(
                        config.byte_budget,
                        config.http.max_response_bytes
                        if config.http is not None
                        else config.byte_budget,
                    )
                    or request.timeout_seconds > config.request_timeout_seconds
                ):
                    raise ValueError("query reservation exceeds its original search allowance")
                if config.discovery is not None:
                    provider = next(
                        (
                            p
                            for p in config.discovery.providers
                            if p.identity == (reservation.provider, reservation.provider_revision)
                        ),
                        None,
                    )
                    if provider is None:
                        raise ValueError("query reservation lost its original provider recipe")
                    prior = tuple(
                        previous
                        for previous in rows[: row.sequence]
                        if previous.event == "fetch"
                        and previous.route
                        == f"search:{reservation.provider}@{reservation.provider_revision}"
                    )
                    if (
                        len(prior) >= provider.max_calls
                        or request.limit > provider.max_results
                        or request.max_bytes
                        > min(
                            provider.max_response_bytes,
                            provider.byte_budget - sum(previous.bytes_read for previous in prior),
                        )
                        or request.timeout_seconds
                        > min(
                            provider.timeout_seconds,
                            provider.call_seconds_budget
                            - sum(previous.latency_seconds for previous in prior),
                        )
                    ):
                        raise ValueError("query reservation exceeds original provider allowance")
                if reservation.cursor.stage == "cited_by":
                    parents = tuple(
                        previous.reference_query
                        for previous in rows[: row.sequence]
                        if previous.reference_query is not None
                        and previous.sequence == reservation.cursor.parent_sequence
                    )
                    if (
                        len(parents) != 1
                        or parents[0].query != request.query.text
                        or parents[0].provider != reservation.provider
                        or parents[0].provider_revision != reservation.provider_revision
                    ):
                        raise ValueError(
                            "cited-by query lost its original parent/reference identity"
                        )
            else:
                retained += 1
                chars += reservation.input_chars
                reuse = config.research.retained_evidence if config.research is not None else None
                if (
                    reuse is None
                    or reservation.retained_reservation != retained
                    or retained > reuse.max_queries
                    or chars > reuse.max_input_chars
                    or reservation.input_chars
                    != len(reservation.request_json.decode())
                    + len(reuse.reader.query_encoder.text_prefix)
                    or reservation.corpus is None
                    or reservation.corpus.corpus_id != reuse.reader.corpus_id
                    or reservation.corpus.configuration.identity
                    != reuse.reader.corpus_config_sha256
                    or reservation.provider != "retained-corpus"
                    or reservation.provider_revision != reuse.reader.corpus_config_sha256
                    or len(reservation.request_json.decode()) > reuse.reader.max_query_chars
                ):
                    raise ValueError("retained query reservations exceed their original allowance")
        ack = row.query_ack
        if ack is not None:
            original = pending.pop(ack.reservation_sequence, None)
            if original is None or ack.reservation_sequence >= row.sequence:
                raise ValueError("query ACK must match one original reserved call")
            if original.channel == "discovery":
                request = SearchRequest.model_validate_json(original.request_json)
                if (
                    row.event != "fetch"
                    or row.route != f"search:{original.provider}@{original.provider_revision}"
                    or row.query != request.query.text
                ):
                    raise ValueError("search ACK changed its original provider/request")
                if ack.result_json is not None:
                    response = SearchResponse.model_validate_json(ack.result_json)
                    if (
                        row.search_response_sha256 != response.content_digest()
                        or row.bytes_read != len(response.raw)
                        or len(response.raw) > request.max_bytes
                        or len(response.hits) > request.limit
                        or row.refusal is not None
                        or row.transport != response.transport
                        or row.reason
                        != "grounded_search:" + hashlib.sha256(response.raw).hexdigest()
                    ):
                        raise ValueError("search ACK changed its bounded native return")
                    if original.corpus is not None:
                        from ghimera.corpus_search_wire import CorpusSearchWire

                        wire = CorpusSearchWire.model_validate_json(response.raw)
                        if (
                            wire.query.corpus_id != original.corpus.corpus_id
                            or wire.query.config_sha256 != original.corpus.configuration.identity
                            or wire.query.generation != original.corpus.generation
                            or wire.query_text != request.query.text
                        ):
                            raise ValueError(
                                "discovery ACK changed its original corpus/query/generation"
                            )
                        if wire.query.reranking is not None:
                            validate_run_evidence(
                                config,
                                rows,
                                wire.query.reranking.request,
                                wire.query.reranking.scores,
                                wire.query.reranking_run,
                                channel="discovery",
                                before_sequence=row.sequence,
                            )
                elif (
                    row.refusal is None
                    or row.search_response_sha256 is not None
                    or row.reason != row.refusal.value
                ):
                    raise ValueError(
                        "non-returned search ACK must retain native refusal/cancellation"
                    )
            elif row != LedgerRow(
                sequence=row.sequence,
                event="query_ack",
                reason="retained_query_ended",
                query_ack=ack,
            ):
                raise ValueError("retained ACK must contain only its original native outcome")
            if original.channel == "retained" and ack.result_json is not None:
                bundle = CorpusEvidenceBundle.model_validate_json(ack.result_json)
                if (
                    original.corpus is None
                    or bundle.query.corpus_id != original.corpus.corpus_id
                    or bundle.query.config_sha256 != original.corpus.configuration.identity
                    or bundle.query.generation != original.corpus.generation
                    or bundle.query_text.encode() != original.request_json
                ):
                    raise ValueError("retained ACK changed its original corpus/query/generation")
                learned = bundle.query.reranking
                if learned is not None:
                    validate_run_evidence(
                        config,
                        rows,
                        learned.request,
                        learned.scores,
                        bundle.query.reranking_run,
                        channel="retained",
                        before_sequence=row.sequence,
                    )
    return tuple(pending)
