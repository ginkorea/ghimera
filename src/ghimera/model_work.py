"""Persist intent before invoking any injected model port; never auto-retry uncertainty.

Uses the existing run journal, its writer ownership, fsync and capacity policy.
No source requests, credentials, new database or inference-server lifecycle.
Port observations are not assertions about token spend or accepted model quality.
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal, TypeVar

from pydantic import BaseModel

from ghimera.ledger import Ledger
from ghimera.model_types import ModelCallEvidence
from ghimera.model_work_types import ModelAcknowledgement, ModelIntent, ModelPhase
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


def uncertain_model_sequences(rows: tuple[LedgerRow, ...]) -> tuple[int, ...]:
    """Validate the invocation chain and identify unanswered/uncertain reservations."""
    intents: dict[int, LedgerRow] = {}
    acknowledgements: dict[int, ModelAcknowledgement] = {}
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
            ):
                raise ValueError("model acknowledgement must match one original invocation")
            acknowledgements[sequence] = row.model_ack
    return tuple(
        sequence
        for sequence in intents
        if sequence not in acknowledgements or acknowledgements[sequence].uncertain
    )


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
    ) -> None:
        self._ledger, self._model, self._scope, self._url = ledger, model, scope, url
        self._sequence: int | None = None
        self._invoked = False
        policy = budget.config.model_work
        reservation = reserve or budget.reserve_judge
        if policy is None:
            reservation()
            return
        if model.location == "external":
            raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
        if len(request) > policy.max_input_bytes:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        if not ledger.has_durable_binding(budget.config):
            raise FatalModelWorkFailure("configured model work requires the run's durable sink")
        if len(uncertain_model_sequences(ledger.snapshot())) >= policy.max_unanswered_calls:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        reservation()
        try:
            self._sequence = ledger.next_sequence
            ledger.append(
                LedgerRow(
                    sequence=self._sequence,
                    event="model_intent",
                    model=model,
                    url=url,
                    reason="model_invocation_reserved",
                    model_intent=ModelIntent(
                        schema="ghimera.model-intent/1",
                        phase=phase,
                        input_scope=scope,
                        input_sha256=hashlib.sha256(request).hexdigest(),
                        input_bytes=len(request),
                        judge_reservation=budget.judge_calls,
                    ),
                )
            )
        except (GhimeraRefused, ValueError, OSError) as exc:
            raise FatalModelWorkFailure("model intent was not durably acknowledged") from exc

    async def invoke(self, call: Callable[[], Awaitable[T]], observe: Callable[[T], bytes]) -> T:
        if self._invoked:
            raise FatalModelWorkFailure("a model invocation cannot be reused or retried")
        self._invoked = True
        if self._sequence is None:
            return await call()
        outcome: Literal["returned", "refused", "cancelled", "failed"] = "failed"
        observed: bytes | None = None
        refused_call: ModelCallEvidence | None = None
        try:
            result = await call()
            observed = observe(result)
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
                        ),
                    )
                )
            except (GhimeraRefused, ValueError, OSError) as exc:
                raise FatalModelWorkFailure("model result was not durably acknowledged") from exc
