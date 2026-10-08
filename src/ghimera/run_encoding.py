"""Native run-journal encoding reservations, original vector ACKs and explicit replay.

Prefix validation is bounded by the configured journal record/byte limits. This
owner does not recover a source cursor, acquire a corpus generation or retry an
uncertain encoder. A journal write failure after contact leaves the intent held.
"""

import asyncio
import hashlib
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from ghimera.embedding_types import EncodingBatch, EncodingCall, encoding_request
from ghimera.journal_types import JournalEntry, JournalHeader, canonical, digest
from ghimera.judgment_validation import validate_native_reading, validate_scoring_readings
from ghimera.models import Goal, LedgerRow
from ghimera.refusals import EncodingCancelled, EncodingFailure, GhimeraRefused, RefusalCode
from ghimera.run_encoding_types import (
    RunEncodingAcknowledgement,
    RunEncodingDecision,
    RunEncodingIntent,
    RunEncodingReplay,
    RunEncodingScope,
)

if TYPE_CHECKING:
    from ghimera.budget import RunBudget
    from ghimera.config import GhimeraConfig
    from ghimera.ledger import Ledger
    from ghimera.model_config import EmbeddingServiceConfig
    from ghimera.ports import EvidenceEncoder

Purpose = Literal["intent", "source"]
BatchPlan = tuple[Purpose, "EmbeddingServiceConfig", tuple[str, ...], int]


def encoding_batches(
    service: "EmbeddingServiceConfig", texts: tuple[str, ...]
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    batches: list[tuple[int, tuple[str, ...]]] = []
    pending: list[str] = []
    start, size = 0, 0
    for text in texts:
        chars = len(text) + len(service.text_prefix)
        if chars > service.max_text_chars or chars > service.max_input_chars:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        if len(pending) >= service.max_batch_texts or size + chars > service.max_input_chars:
            batches.append((start, tuple(pending)))
            start += len(pending)
            pending, size = [], 0
        pending.append(text)
        size += chars
    if pending:
        batches.append((start, tuple(pending)))
    return tuple(batches)


def _scope_inputs(
    config: "GhimeraConfig",
    rows: tuple[LedgerRow, ...],
    sequence: int,
    scope: RunEncodingScope,
) -> tuple[str, ...]:
    policy = config.scoring
    proof = scope.source
    if policy is None or proof.reading_sequence >= sequence:
        raise ValueError("run encoding requires a prior original native reading")
    native = validate_native_reading(rows, proof.reading_sequence)
    if proof.reading_sha256 != hashlib.sha256(
        rows[proof.reading_sequence].model_dump_json().encode()
    ).hexdigest() or (native.source_url, native.source_sha256, native.text_sha256) != (
        proof.source_url,
        proof.source_sha256,
        proof.text_sha256,
    ):
        raise ValueError("run encoding changed its original source/parser/reading binding")
    from ghimera.semantic_scoring import selected_windows

    expected = selected_windows(native.native_text, Goal(text=scope.goal_text), policy)
    if scope.windows != expected or len(scope.link_inputs) > policy.max_links:
        raise ValueError("run encoding changed the complete ordered native scoring plan")
    for text in scope.link_inputs:
        url, separator, anchor = text.partition("\n")
        if not url or not separator or len(anchor) > policy.max_anchor_chars:
            raise ValueError("run encoding link inputs exceed the original observed anchor bounds")
    return tuple(native.native_text[start:end] for start, end in expected) + scope.link_inputs


def validate_run_encoding_rows(
    config: "GhimeraConfig",
    rows: tuple[LedgerRow, ...],
    *,
    header: JournalHeader | None = None,
    goal_text: str | None = None,
) -> tuple[int, int]:
    """Original intents charge once, including UNKNOWN; ACK/replay do not charge."""
    policy = config.scoring
    recovery = policy.run_encoding_recovery if policy is not None else None
    intents: dict[int, RunEncodingIntent] = {}
    acks: dict[int, LedgerRow] = {}
    completed: set[int] = set()
    calls, chars, stored = 0, 0, 0
    header_binding: tuple[str, str] | None = None
    if any(row.run_encoding_intent is not None for row in rows):
        validate_scoring_readings(config, rows)
    for index, row in enumerate(rows):
        if row.sequence != index:
            raise ValueError("run encoding requires the original contiguous ledger")
        intent = row.run_encoding_intent
        if intent is not None:
            if recovery is None or policy is None or config.journal is None:
                raise ValueError("run encoding requires its explicit scoring/3 and native journal")
            intent = RunEncodingIntent.model_validate(intent.model_dump())
            if set(intents) - set(acks):
                raise ValueError("unknown run encoding remains charged and held before new contact")
            source_inputs = _scope_inputs(config, rows, index, intent.scope)
            inputs = (intent.scope.goal_text,) if intent.purpose == "intent" else source_inputs
            service = policy.intent_encoder if intent.purpose == "intent" else policy.encoder
            allowed = encoding_batches(service, inputs)
            calls += 1
            chars += intent.input_chars
            binding = (intent.run_id, intent.header_sha256)
            if (
                intent.scoring_sha256
                != hashlib.sha256(policy.model_dump_json().encode()).hexdigest()
                or intent.service != service
                or (intent.batch_start, intent.texts) not in allowed
                or intent.reserved_call != calls
                or intent.reserved_chars != chars
                or (intent.purpose == "intent" and policy.reference_source != "intent")
                or (goal_text is not None and intent.scope.goal_text != goal_text)
                or (header_binding is not None and binding != header_binding)
                or (
                    header is not None
                    and (
                        binding != (header.run_id, digest(header))
                        or intent.scope.goal_text != header.goal.text
                        or header.config != config
                    )
                )
                or row.model is None
                or (row.model.model_id, row.model.revision, row.model.location)
                != (service.model_id, service.revision, "self_hosted")
                or row.url != (None if intent.purpose == "intent" else intent.scope.canonical_url)
                or row.refusal is not None
            ):
                raise ValueError(
                    "run encoding reservation drifted from its exact run/recipe/purpose"
                )
            if calls > policy.encoding_call_budget or chars > policy.encoding_char_budget:
                raise ValueError("run encoding reservations exceed original shared allowance")
            header_binding = binding
            intents[index] = intent
        if row.encoding_call is not None:
            original = row.run_encoding_sequence
            if original is None:
                if recovery is not None:
                    raise ValueError("scoring/3 encoding cannot bypass its original reservation")
                calls += 1
                chars += row.encoding_call.input_chars
            else:
                if recovery is None or original not in intents or original in completed:
                    raise ValueError("run encoding result requires one prior original reservation")
                reserved = intents[original]
                reserved.validate_call(row.encoding_call)
                if row.model != rows[original].model or row.url != rows[original].url:
                    raise ValueError("run encoding result changed its original model or source")
                completed.add(original)
                if row.encoding_call.outcome == "success" and row.run_encoding_ack is None:
                    raise ValueError("successful run encoding must durably retain original vectors")
        if row.run_encoding_ack is not None:
            ack = RunEncodingAcknowledgement.model_validate(row.run_encoding_ack.model_dump())
            original = ack.original_intent_sequence
            if recovery is None or original not in intents or original in acks:
                raise ValueError("run vector ACK requires its unique original reservation")
            reserved = intents[original]
            reserved.validate_call(ack.result.call)
            if (
                ack.result.call.status != 200
                or ack.result.call.response_bytes > reserved.service.max_response_bytes
                or (reserved.service.require_usage and ack.result.call.usage is None)
            ):
                raise ValueError("retained vector ACK lacks its admitted response telemetry")
            size = len(canonical(ack.result))
            stored += size
            if (
                ack.intent_sha256 != reserved.content_digest()
                or ack.result.call != row.encoding_call
                or size > recovery.max_result_bytes
                or stored > recovery.max_total_result_bytes
            ):
                raise ValueError("run vector ACK changed identity or exceeded retained bounds")
            acks[original] = row
        if row.run_encoding_replay is not None:
            replay = row.run_encoding_replay
            original = replay.original_intent_sequence
            acknowledged = acks.get(original)
            if acknowledged is None or acknowledged.run_encoding_ack is None:
                raise ValueError(
                    "unknown/refused run encoding has no original vector ACK to replay"
                )
            if (
                replay.original_ack_sequence != acknowledged.sequence
                or replay.intent_sha256 != intents[original].content_digest()
                or replay.ack_sha256 != acknowledged.run_encoding_ack.content_digest()
                or row.model != acknowledged.model
                or row.url != acknowledged.url
                or row.refusal is not None
                or row.encoding_call is not None
            ):
                raise ValueError("run vector replay changed original intent/ACK/source identity")
    return calls, chars


def encoding_usage(config: "GhimeraConfig", rows: tuple[LedgerRow, ...]) -> tuple[int, int]:
    return validate_run_encoding_rows(config, rows)


def unknown_encoding_sequences(rows: tuple[LedgerRow, ...]) -> tuple[int, ...]:
    acknowledged = {
        row.run_encoding_ack.original_intent_sequence
        for row in rows
        if row.run_encoding_ack is not None
    }
    return tuple(
        row.sequence
        for row in rows
        if row.run_encoding_intent is not None and row.sequence not in acknowledged
    )


class RunEncodingWork:
    """One scoring invocation's ordered explicit decisions and actual batch plan."""

    def __init__(
        self,
        budget: "RunBudget",
        ledger: "Ledger",
        scope: RunEncodingScope,
        plan: tuple[BatchPlan, ...],
        decisions: tuple[RunEncodingDecision, ...] | None,
    ) -> None:
        self.budget, self.ledger, self.scope, self.plan = budget, ledger, scope, plan
        from ghimera.config import GhimeraConfig

        GhimeraConfig.model_validate(budget.config.model_dump())
        self.header = ledger.run_header(budget.config)
        self.decisions = (
            tuple(RunEncodingDecision(mode="fresh") for _ in plan)
            if decisions is None
            else tuple(RunEncodingDecision.model_validate(d.model_dump()) for d in decisions)
        )
        self.position = 0
        self.last_encoding_sequence: int | None = None
        rows = ledger.snapshot()
        usage = validate_run_encoding_rows(budget.config, rows, header=self.header)
        if usage != (budget.encoding_calls, budget.encoding_chars):
            raise ValueError("run encoding budget must restore original charged intent counters")
        if unknown_encoding_sequences(rows):
            raise ValueError("unknown run encoding remains charged and held, never retried")
        if len(self.decisions) != len(plan):
            raise ValueError("explicit run encoding decisions must cover the entire batch plan")
        sequences = tuple(d.original_intent_sequence for d in self.decisions if d.mode == "replay")
        if len(set(sequences)) != len(sequences):
            raise ValueError("a replay schedule cannot adopt an original ACK twice")
        for decision, expected in zip(self.decisions, plan, strict=True):
            if decision.mode == "replay":
                self._retained(decision, expected)

    def _retained(
        self,
        decision: RunEncodingDecision,
        expected: BatchPlan,
    ) -> tuple[LedgerRow, LedgerRow]:
        original = decision.original_intent_sequence
        rows = self.ledger.snapshot()
        if original is None or original >= len(rows):
            raise ValueError("run encoding replay requires its explicit original sequence")
        reserved = rows[original]
        intent = reserved.run_encoding_intent
        purpose, service, texts, start = expected
        if intent is None or (
            intent.run_id != self.header.run_id
            or intent.header_sha256 != digest(self.header)
            or intent.scope != self.scope
            or (intent.purpose, intent.service, intent.texts, intent.batch_start)
            != (purpose, service, texts, start)
        ):
            raise ValueError(
                "run replay changed source/goal/reading/purpose/request or original run"
            )
        matches = tuple(
            row
            for row in rows
            if row.run_encoding_ack is not None
            and row.run_encoding_ack.original_intent_sequence == original
        )
        if len(matches) != 1:
            raise ValueError(
                "unknown/refused run encoding requires its original retained vector ACK"
            )
        return reserved, matches[0]

    async def encode_texts(
        self,
        purpose: Purpose,
        encoder: "EvidenceEncoder",
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        output: list[tuple[float, ...]] = []
        for start, batch in encoding_batches(encoder.config, texts):
            output.extend(
                (await self._batch((purpose, encoder.config, batch, start), encoder)).vectors
            )
        return tuple(output)

    async def _batch(self, expected: BatchPlan, encoder: "EvidenceEncoder") -> EncodingBatch:
        async with self.budget.encoding_lock:
            return await self._locked_batch(expected, encoder)

    async def _locked_batch(self, expected: BatchPlan, encoder: "EvidenceEncoder") -> EncodingBatch:
        config, ledger, budget = self.budget.config, self.ledger, self.budget
        ledger.run_header(config)
        usage = validate_run_encoding_rows(config, ledger.snapshot(), header=self.header)
        if usage != (budget.encoding_calls, budget.encoding_chars):
            raise ValueError("run encoding counters changed from the native charged prefix")
        if unknown_encoding_sequences(ledger.snapshot()):
            raise ValueError("unknown run encoding remains charged and held")
        if self.position >= len(self.plan) or expected != self.plan[self.position]:
            raise ValueError("run encoding changed the admitted ordered batch plan")
        decision = self.decisions[self.position]
        self.position += 1
        if decision.mode == "replay":
            reserved, acknowledged = self._retained(decision, expected)
            intent, ack = reserved.run_encoding_intent, acknowledged.run_encoding_ack
            if intent is None or ack is None:
                raise ValueError("original run vector ACK is absent")
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="run_encoding_replay",
                    model=acknowledged.model,
                    url=acknowledged.url,
                    reason="original_vector_ack_no_contact_no_new_charge",
                    run_encoding_replay=RunEncodingReplay(
                        schema="ghimera.run-encoding-replay/1",
                        original_intent_sequence=reserved.sequence,
                        original_ack_sequence=acknowledged.sequence,
                        intent_sha256=intent.content_digest(),
                        ack_sha256=ack.content_digest(),
                    ),
                )
            )
            self.last_encoding_sequence = acknowledged.sequence
            return ack.result
        purpose, service, texts, start = expected
        scoring = config.scoring
        if scoring is None or scoring.run_encoding_recovery is None or config.journal is None:
            raise ValueError("run encoding requires its explicit native policy")
        recovery = scoring.run_encoding_recovery
        budget.check_time()
        input_chars = sum(len(service.text_prefix) + len(text) for text in texts)
        if (
            usage[0] >= scoring.encoding_call_budget
            or usage[1] + input_chars > scoring.encoding_char_budget
        ):
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        intent = RunEncodingIntent(
            schema="ghimera.run-encoding-intent/1",
            run_id=self.header.run_id,
            header_sha256=digest(self.header),
            scoring_sha256=hashlib.sha256(scoring.model_dump_json().encode()).hexdigest(),
            purpose=purpose,
            scope=self.scope,
            service=service,
            texts=texts,
            batch_start=start,
            request_sha256=hashlib.sha256(encoding_request(service, texts)).hexdigest(),
            reserved_call=usage[0] + 1,
            reserved_chars=usage[1] + sum(len(service.text_prefix) + len(text) for text in texts),
        )
        row = LedgerRow(
            sequence=ledger.next_sequence,
            event="run_encoding_intent",
            model=encoder.model,
            url=None if purpose == "intent" else self.scope.canonical_url,
            reason="original_run_encoding_reservation_before_contact",
            run_encoding_intent=intent,
        )
        validate_run_encoding_rows(config, ledger.snapshot() + (row,), header=self.header)
        stored = sum(
            len(canonical(r.run_encoding_ack.result))
            for r in ledger.snapshot()
            if r.run_encoding_ack is not None
        )
        if stored + recovery.max_result_bytes > recovery.max_total_result_bytes:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        # Exact wrapper overhead around a bounded EncodingBatch; no guessed storage
        # helper or replacement persistence. Largest admitted sequence avoids a
        # concurrent observation increasing the decimal wrapper length.
        call = EncodingCall(
            schema="chimera.encoding-call/1",
            service=service,
            request_sha256=intent.request_sha256,
            response_sha256="0" * 64,
            response_bytes=service.max_response_bytes,
            input_sha256=tuple(
                hashlib.sha256((service.text_prefix + text).encode()).hexdigest() for text in texts
            ),
            input_chars=intent.input_chars,
            status=200,
            latency_seconds=service.timeout_seconds,
            usage=None,
            outcome="success",
        )
        sample = EncodingBatch(vectors=tuple((1.0,) * service.dimensions for _ in texts), call=call)
        sample_ack = RunEncodingAcknowledgement(
            schema="ghimera.run-encoding-ack/1",
            original_intent_sequence=row.sequence,
            intent_sha256=intent.content_digest(),
            result=sample,
            result_sha256=hashlib.sha256(sample.model_dump_json().encode()).hexdigest(),
        )
        sample_row = LedgerRow(
            sequence=config.journal.max_records - 1,
            event="encoding",
            model=encoder.model,
            url=row.url,
            reason="success",
            encoding_call=call,
            run_encoding_sequence=row.sequence,
            run_encoding_ack=sample_ack,
        )
        entry_size = (
            len(
                canonical(
                    JournalEntry(
                        schema="chimera.run-journal-entry/1",
                        previous_sha256="0" * 64,
                        row=sample_row,
                    )
                )
            )
            + 1
        )
        # The call is also existing ledger telemetry, hence two bounded copies.
        ack_size = (
            entry_size
            - len(canonical(sample))
            - len(canonical(call))
            + 2 * recovery.max_result_bytes
        )
        intent_size = (
            len(
                canonical(
                    JournalEntry(
                        schema="chimera.run-journal-entry/1",
                        previous_sha256="0" * 64,
                        row=row,
                    )
                )
            )
            + 1
        )
        ledger.check_capacity(config, (intent_size, ack_size))
        budget.reserve_encoding(intent.input_chars)
        ledger.append(row)
        started = asyncio.get_running_loop().time()
        observed: EncodingCall | None = None
        try:
            returned = await encoder.encode_batch(texts)
            candidate = EncodingCall.model_validate(returned.call.model_dump())
            intent.validate_call(candidate)
            observed = candidate
            result = EncodingBatch.model_validate(returned.model_dump())
            if (
                result.call.status != 200
                or result.call.response_bytes > service.max_response_bytes
                or (service.require_usage and result.call.usage is None)
            ):
                raise ValueError("original vector ACK lacks admitted response telemetry")
            if len(canonical(result)) > recovery.max_result_bytes:
                raise ValueError("original vector result exceeds retained allowance")
        except (Exception, asyncio.CancelledError) as exc:
            # Failed/refused/cancelled calls have no successful vector ACK. Original
            # reservation stays charged; it cannot be replayed or silently retried.
            if isinstance(exc, (EncodingFailure, EncodingCancelled)):
                observed = exc.call
            if observed is not None:
                try:
                    intent.validate_call(observed)
                except ValueError:
                    observed = None
            if observed is not None and observed.outcome == "success":
                observed = observed.model_copy(
                    update={
                        "outcome": "cancelled"
                        if isinstance(exc, asyncio.CancelledError)
                        else "refused"
                    }
                )
            if observed is None:
                observed = EncodingCall(
                    schema="chimera.encoding-call/1",
                    service=service,
                    request_sha256=intent.request_sha256,
                    response_sha256="0" * 64,
                    response_bytes=0,
                    input_sha256=call.input_sha256,
                    input_chars=intent.input_chars,
                    status=None,
                    latency_seconds=max(0.0, asyncio.get_running_loop().time() - started),
                    usage=None,
                    outcome="cancelled" if isinstance(exc, asyncio.CancelledError) else "refused",
                    telemetry="unavailable",
                )
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="encoding",
                    model=encoder.model,
                    url=row.url,
                    reason=observed.outcome,
                    encoding_call=observed,
                    run_encoding_sequence=row.sequence,
                    refusal=RefusalCode.ADAPTER_CONTRACT,
                )
            )
            if isinstance(exc, (ValueError, ValidationError)):
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
            raise
        ack = RunEncodingAcknowledgement(
            schema="ghimera.run-encoding-ack/1",
            original_intent_sequence=row.sequence,
            intent_sha256=intent.content_digest(),
            result=result,
            result_sha256=hashlib.sha256(result.model_dump_json().encode()).hexdigest(),
        )
        acknowledged = LedgerRow(
            sequence=ledger.next_sequence,
            event="encoding",
            model=encoder.model,
            url=row.url,
            reason="success",
            encoding_call=result.call,
            run_encoding_sequence=row.sequence,
            run_encoding_ack=ack,
        )
        validate_run_encoding_rows(config, ledger.snapshot() + (acknowledged,), header=self.header)
        ledger.append(acknowledged)
        self.last_encoding_sequence = acknowledged.sequence
        return result
