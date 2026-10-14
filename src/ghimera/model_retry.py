"""Bounded backoff for transient model and encoder refusals.

A shared self-hosted service can briefly refuse while it reloads (409
``model_not_ready``, 429, 502-504) or drop a connection. Such a refusal is
retried with exponential backoff until ``max_wait_seconds`` of waiting is spent.
Every attempt is its own accounted call in the ledger. A refusal that is not
transient (wrong model, invalid envelope, contract fault) is never retried, and
an exhausted retry refuses only the text or page that needed the call.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")
TRANSIENT_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
Seconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class ModelRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.model-retry/1"] = Field(
        default="ghimera.model-retry/1", alias="schema"
    )
    max_wait_seconds: Seconds = 180.0
    initial_backoff_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 1.0
    max_backoff_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 30.0

    @model_validator(mode="after")
    def ordered(self) -> "ModelRetryPolicy":
        if self.initial_backoff_seconds > self.max_backoff_seconds:
            raise ValueError("initial backoff cannot exceed the maximum backoff")
        return self


DEFAULT_MODEL_RETRY = ModelRetryPolicy()


def transient_status(status: int | None) -> bool:
    return status in TRANSIENT_STATUSES


async def retry_transient(
    policy: ModelRetryPolicy,
    attempt: Callable[[], Awaitable[T]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run ``attempt``; on a refusal marked ``transient`` wait and try again."""
    waited, delay = 0.0, policy.initial_backoff_seconds
    while True:
        try:
            return await attempt()
        except Exception as exc:
            if not getattr(exc, "transient", False) or waited + delay > policy.max_wait_seconds:
                raise
        await sleep(delay)
        waited += delay
        delay = min(delay * 2, policy.max_backoff_seconds)
