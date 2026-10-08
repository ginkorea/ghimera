"""Persist intent before invoking any injected model port; never auto-retry uncertainty.

Uses the existing run journal, its writer ownership, fsync and capacity policy.
No source requests, credentials, new database or inference-server lifecycle.
Port observations are not assertions about token spend or accepted model quality.
"""

import asyncio
import base64
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeVar

from pydantic import BaseModel

from ghimera.ledger import Ledger
from ghimera.model_reconciliation_types import (
    ModelAttemptAuthorization,
    ModelAttemptConsumption,
    ModelReconciliationDecision,
)
from ghimera.model_types import ModelCallEvidence
from ghimera.model_work_types import (
    ModelAcknowledgement,
    ModelIntent,
    ModelPhase,
    ModelReplay,
    ModelStoredOutput,
    ModelStoredWire,
    ModelWorkConfig,
)
from ghimera.models import LedgerRow, ModelIdentity
from ghimera.refusals import GhimeraRefused, ModelFailure, RefusalCode

if TYPE_CHECKING:
    from ghimera.budget import RunBudget
    from ghimera.model_http import ModelHttpResponse

T = TypeVar("T")


class FatalModelWorkFailure(RuntimeError):
    """Do not reinterpret lost durable accounting as a recoverable model refusal."""


def port_input(budget: "RunBudget", *records: BaseModel, **values: object) -> bytes:
    """Exact logical port arguments, distinct from a provider's selected wire prompt."""
    if budget.config.model_work is None:
        return b""
    return json.dumps(
        {"records": [record.model_dump(mode="json") for record in records], "values": values},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def record_output(record: BaseModel) -> bytes:
    return record.model_dump_json().encode()


def wire_output(response: "ModelHttpResponse") -> bytes:
    # Preserve the exact response body hash, not a rewritten envelope.
    if response.status is None:
        raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
    return response.body


@dataclass(frozen=True)
class ModelObservation:
    body: bytes
    wire: ModelStoredWire


def wire_observation(response: "ModelHttpResponse") -> ModelObservation:
    body = wire_output(response)
    if response.status is None:
        raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
    return ModelObservation(
        body, ModelStoredWire(status=response.status, content_type=response.content_type)
    )


def uncertain_model_sequences(rows: tuple[LedgerRow, ...]) -> tuple[int, ...]:
    """Validate the invocation chain and identify unanswered/uncertain reservations."""
    intents: dict[int, LedgerRow] = {}
    acknowledgements: dict[int, ModelAcknowledgement] = {}
    acknowledged_rows: dict[int, LedgerRow] = {}
    reservations: set[int] = set()
    for row in rows:
        if row.model_intent is not None:
            reservation = row.model_intent.judge_reservation
            if reservation in reservations:
                raise ValueError("one judge reservation cannot invoke two model ports")
            reservations.add(reservation)
            intents[row.sequence] = row
        if row.model_ack is not None:
            sequence = row.model_ack.intent_sequence
            intent = intents.get(sequence)
            if (
                intent is None
                or sequence in acknowledgements
                or intent.model != row.model
                or intent.url != row.url
                or (
                    row.model_ack.outcome == "returned"
                    and intent.model_intent is not None
                    and row.model_ack.output_scope
                    != (
                        "wire_response"
                        if intent.model_intent.input_scope == "wire_request"
                        else "port_output"
                    )
                )
            ):
                raise ValueError("model acknowledgement must match one original invocation")
            acknowledgements[sequence] = row.model_ack
            acknowledged_rows[row.sequence] = row
        if row.model_replay is not None:
            replay = row.model_replay
            intent = intents.get(replay.intent_sequence)
            acknowledged = acknowledged_rows.get(replay.ack_sequence)
            ack = acknowledged.model_ack if acknowledged is not None else None
            if (
                intent is None
                or ack is None
                or ack.intent_sequence != replay.intent_sequence
                or ack.outcome != "returned"
                or ack.stored_output is None
                or row.model != intent.model
                or row.url != intent.url
                or replay.output_scope != ack.output_scope
                or replay.output_sha256 != ack.output_sha256
                or replay.output_bytes != ack.output_bytes
            ):
                raise ValueError("local replay must match its retained original acknowledgement")
    return tuple(
        sequence
        for sequence in intents
        if sequence not in acknowledgements or acknowledgements[sequence].uncertain
    )


def validate_model_rows(
    policy: ModelWorkConfig | None, judge_budget: int, rows: tuple[LedgerRow, ...]
) -> int:
    """Validate original accounting and retention before restore, replay or new contact."""
    uncertain = uncertain_model_sequences(rows)
    intents = tuple(row.model_intent for row in rows if row.model_intent is not None)
    if policy is None:
        if intents:
            raise ValueError("model intents require the original run's declared storage policy")
        return 0
    if tuple(intent.judge_reservation for intent in intents) != tuple(range(1, len(intents) + 1)):
        raise ValueError("model reservations must retain every call in order")
    if len(intents) > judge_budget or any(i.input_bytes > policy.max_input_bytes for i in intents):
        raise ValueError("model intents exceed their declared run/input budgets")
    used = 0
    for row in rows:
        ack = row.model_ack
        if ack is None:
            continue
        stored = ack.stored_output
        if policy.results is None:
            if stored is not None:
                raise ValueError("retained model answers require their original result policy")
        elif ack.outcome == "returned":
            if stored is None or len(stored.body()) > policy.results.max_result_bytes:
                raise ValueError("acknowledged return must retain its bounded original answer")
            used += len(stored.body())
    if len(uncertain) > policy.max_unanswered_calls:
        raise ValueError("model reservations exceed their unanswered-call bound")
    if policy.results is not None and (
        used + len(uncertain) * policy.results.max_result_bytes
        > policy.results.max_total_result_bytes
    ):
        raise ValueError("retained model answers exceed their total storage budget")
    return len(intents)


class ModelInvocation:
    """One reservation, one durable intent and at most one invocation.

    Construction is synchronous: quota refusal remains before the caller's model
    failure handler, and no port can start before the journal acknowledges intent.
    This is the existing ledger's lifecycle, not another persistence engine.
    """

    def __init__(
        self,
        budget: "RunBudget",
        ledger: Ledger,
        *,
        phase: ModelPhase,
        model: ModelIdentity,
        request: bytes,
        scope: Literal["port_input", "wire_request"] = "port_input",
        url: str | None = None,
        reserve: Callable[[], None] | None = None,
        replay_intent_sequence: int | None = None,
        decision: ModelReconciliationDecision | None = None,
        attempt: ModelAttemptAuthorization | None = None,
    ) -> None:
        self._ledger, self._model, self._scope, self._url = ledger, model, scope, url
        self._budget = budget
        self._sequence: int | None = None
        self._invoked = False
        self._replay: tuple[LedgerRow, LedgerRow] | None = None
        self._authorization: ModelAttemptAuthorization | None = None
        policy = budget.config.model_work
        self._results = policy.results if policy is not None else None
        reservation = reserve or budget.reserve_judge
        if policy is None:
            if replay_intent_sequence is not None:
                raise FatalModelWorkFailure("replay requires the original result-retention policy")
            reservation()
            return
        if model.location == "external":
            raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
        if len(request) > policy.max_input_bytes:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        if not ledger.has_durable_binding(budget.config):
            raise FatalModelWorkFailure("configured model work requires the run's durable sink")
        rows = ledger.snapshot()
        from ghimera.model_reconciliation import authorization, validate_decisions, validate_policy

        try:
            validate_policy(budget.config, rows)
            if validate_model_rows(policy, budget.config.judge_budget, rows) != budget.judge_calls:
                raise ValueError("budget does not preserve original model reservations")
        except ValueError as exc:
            raise FatalModelWorkFailure("original model reservations are inconsistent") from exc
        if replay_intent_sequence is not None:
            if decision is not None or attempt is not None:
                raise FatalModelWorkFailure("replay cannot authorize another model contact")
            self._bind_replay(budget, rows, phase, request, replay_intent_sequence)
            return
        if attempt is not None:
            if (
                decision is not None
                or attempt not in validate_decisions(rows)
                or not ledger.has_replay_binding(budget.config)
            ):
                raise FatalModelWorkFailure(
                    "attempt requires its owning exact committed authorization"
                )
            row = rows[attempt.attempt_intent_sequence]
            intent = row.model_intent
            if (
                intent is None
                or row.model != model
                or row.url != url
                or intent.phase != phase
                or intent.input_scope != scope
                or intent.input_sha256 != hashlib.sha256(request).hexdigest()
                or intent.input_bytes != len(request)
                or any(
                    item.model_attempt is not None and item.model_attempt.authorization == attempt
                    for item in rows
                )
            ):
                raise FatalModelWorkFailure("model attempt is changed or already consumed")
            self._sequence, self._authorization = row.sequence, attempt
            return
        if decision is not None:
            recovery = budget.config.research_recovery
            reconciliation = recovery.model_reconciliation if recovery is not None else None
            if (
                reconciliation is None
                or len(validate_decisions(rows)) >= reconciliation.max_decisions_per_run
                or len(decision.model_dump_json().encode()) > reconciliation.max_decision_bytes
                or decision.observed.ledger_rows != len(rows)
            ):
                raise FatalModelWorkFailure(
                    "decision is stale or exceeds original reconciliation policy"
                )
        uncertain = uncertain_model_sequences(rows)
        if len(uncertain) >= policy.max_unanswered_calls:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        if self._results is not None:
            used = sum(
                len(row.model_ack.stored_output.body())
                for row in rows
                if row.model_ack is not None and row.model_ack.stored_output is not None
            )
            reserved = (len(uncertain) + 1) * self._results.max_result_bytes
            if used + reserved > self._results.max_total_result_bytes:
                raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        try:
            self._sequence = ledger.next_sequence
            candidate = LedgerRow(
                sequence=self._sequence,
                event="model_intent",
                model=model,
                url=url,
                reason="model_reconciliation_reserved"
                if decision is not None
                else "model_invocation_reserved",
                model_intent=ModelIntent(
                    schema="ghimera.model-intent/1",
                    phase=phase,
                    input_scope=scope,
                    input_sha256=hashlib.sha256(request).hexdigest(),
                    input_bytes=len(request),
                    judge_reservation=budget.judge_calls + 1,
                ),
                model_decision=decision,
            )
            if decision is not None:
                validate_policy(budget.config, rows + (candidate,))
        except ValueError as exc:
            raise FatalModelWorkFailure("model decision is not admissible") from exc
        reservation()
        try:
            if (
                candidate.model_intent is None
                or candidate.model_intent.judge_reservation != budget.judge_calls
            ):
                raise ValueError("model reservation changed its native accounting")
            ledger.append(candidate)
            if decision is not None:
                validate_policy(budget.config, ledger.snapshot())
                self._authorization = authorization(ledger.snapshot()[-1])
        except (GhimeraRefused, ValueError, OSError) as exc:
            raise FatalModelWorkFailure("model intent was not durably acknowledged") from exc

    def _bind_replay(
        self,
        budget: "RunBudget",
        rows: tuple[LedgerRow, ...],
        phase: ModelPhase,
        request: bytes,
        sequence: int,
    ) -> None:
        if (
            self._results is None
            or type(sequence) is not int
            or not 0 <= sequence < len(rows)
            or not self._ledger.has_replay_binding(budget.config)
        ):
            raise FatalModelWorkFailure("replay requires its original run's committed prefix")
        original = rows[sequence]
        intent = original.model_intent
        acks = tuple(
            row
            for row in rows
            if row.model_ack is not None and row.model_ack.intent_sequence == sequence
        )
        if (
            intent is None
            or original.model != self._model
            or original.url != self._url
            or intent.phase != phase
            or intent.input_scope != self._scope
            or intent.input_sha256 != hashlib.sha256(request).hexdigest()
            or intent.input_bytes != len(request)
            or len(acks) != 1
            or acks[0].model_ack is None
            or acks[0].model_ack.outcome != "returned"
            or acks[0].model_ack.stored_output is None
        ):
            raise FatalModelWorkFailure("replay requires its exact acknowledged original input")
        self._replay = original, acks[0]

    def replay(self, decode: Callable[[ModelStoredOutput], T]) -> T:
        if self._invoked:
            raise FatalModelWorkFailure("a model invocation cannot be reused or retried")
        if self._replay is None:
            raise FatalModelWorkFailure("this invocation is not bound to an original answer")
        self._invoked = True
        original, acknowledged = self._replay
        ack = acknowledged.model_ack
        if (
            ack is None
            or ack.stored_output is None
            or ack.output_scope is None
            or ack.output_sha256 is None
            or ack.output_bytes is None
        ):
            raise FatalModelWorkFailure("original answer is not replayable")
        # Decode/validate before acknowledging a local read. A bad result cannot
        # trigger a new model call or gain a fabricated consumer-application ack.
        result = decode(ack.stored_output)
        try:
            self._ledger.append(
                LedgerRow(
                    sequence=self._ledger.next_sequence,
                    event="model_replay",
                    model=self._model,
                    url=self._url,
                    reason="retained_model_answer_read",
                    model_replay=ModelReplay(
                        schema="ghimera.model-replay/1",
                        intent_sequence=original.sequence,
                        ack_sequence=acknowledged.sequence,
                        output_scope=ack.output_scope,
                        output_sha256=ack.output_sha256,
                        output_bytes=ack.output_bytes,
                    ),
                )
            )
        except (GhimeraRefused, ValueError, OSError) as exc:
            raise FatalModelWorkFailure("model replay was not durably acknowledged") from exc
        return result

    async def invoke(
        self, call: Callable[[], Awaitable[T]], observe: Callable[[T], bytes | ModelObservation]
    ) -> T:
        if self._replay is not None:
            raise FatalModelWorkFailure("an original answer is replayed locally, not invoked again")
        if self._invoked:
            raise FatalModelWorkFailure("a model invocation cannot be reused or retried")
        self._invoked = True
        if self._authorization is not None:
            self._budget.check_time()
            from ghimera.model_reconciliation import validate_policy

            rows = self._ledger.snapshot()
            validate_policy(self._budget.config, rows)
            if (
                not self._ledger.has_replay_binding(self._budget.config)
                or self._ledger.next_sequence != self._authorization.attempt_intent_sequence + 1
                or any(
                    row.model_attempt is not None
                    and row.model_attempt.authorization == self._authorization
                    for row in rows
                )
            ):
                raise FatalModelWorkFailure("model attempt was consumed or changed before contact")
            try:
                self._ledger.append(
                    LedgerRow(
                        sequence=self._ledger.next_sequence,
                        event="model_attempt",
                        model=self._model,
                        url=self._url,
                        reason="model_attempt_consumed",
                        model_attempt=ModelAttemptConsumption(
                            schema="ghimera.model-attempt-consumption/1",
                            authorization=self._authorization,
                        ),
                    )
                )
            except (GhimeraRefused, ValueError, OSError) as exc:
                raise FatalModelWorkFailure("model attempt was not durably consumed") from exc
        if self._sequence is None:
            return await call()
        outcome: Literal["returned", "refused", "cancelled", "failed"] = "failed"
        observed: bytes | None = None
        refused_call: ModelCallEvidence | None = None
        stored_output: ModelStoredOutput | None = None
        try:
            result = await call()
            observation = observe(result)
            observed = (
                observation.body if isinstance(observation, ModelObservation) else observation
            )
            if self._results is not None:
                if len(observed) > self._results.max_result_bytes:
                    raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
                wire = observation.wire if isinstance(observation, ModelObservation) else None
                if (self._scope == "wire_request") != (wire is not None):
                    raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
                stored_output = ModelStoredOutput(
                    schema="ghimera.model-stored-output/1",
                    body_base64=base64.b64encode(observed).decode("ascii"),
                    wire=wire,
                )
            outcome = "returned"
            return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except GhimeraRefused as exc:
            outcome = "refused"
            if (
                isinstance(exc, ModelFailure)
                and exc.model_call.status is not None
                and exc.model_call.completion is not None
                and exc.model_call.outcome == "refused"
                and exc.model_call.service.model_id == self._model.model_id
                and exc.model_call.service.revision == self._model.revision
            ):
                refused_call = exc.model_call
            raise
        finally:
            try:
                self._ledger.append(
                    LedgerRow(
                        sequence=self._ledger.next_sequence,
                        event="model_ack",
                        model=self._model,
                        url=self._url,
                        reason="model_port_ended",
                        model_ack=ModelAcknowledgement(
                            schema="ghimera.model-ack/1",
                            intent_sequence=self._sequence,
                            outcome=outcome,
                            output_scope="wire_response"
                            if refused_call is not None
                            else (
                                "wire_response" if self._scope == "wire_request" else "port_output"
                            )
                            if observed is not None
                            else None,
                            output_sha256=refused_call.response_sha256
                            if refused_call is not None
                            else hashlib.sha256(observed).hexdigest()
                            if observed is not None
                            else None,
                            output_bytes=refused_call.response_bytes
                            if refused_call is not None
                            else len(observed)
                            if observed is not None
                            else None,
                            refused_call=refused_call,
                            stored_output=stored_output,
                        ),
                    )
                )
            except (GhimeraRefused, ValueError, OSError) as exc:
                raise FatalModelWorkFailure("model result was not durably acknowledged") from exc
