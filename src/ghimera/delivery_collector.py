"""Optional durable delivery composition over existing collector/corpus facades."""

import asyncio
from typing import Literal, Protocol

from pydantic import Field, model_validator

from ghimera.delivery_outbox import DeliveryOutbox
from ghimera.delivery_types import DeliveryItem, DeliveryState, Result
from ghimera.models import Goal, Record, Scope
from ghimera.research_types import ResearchRequest


class ResultSource(Protocol):
    async def collect(self, goal: Goal, scope: Scope, *, run_id: str | None = None) -> Result: ...

    async def run(
        self,
        request: str | ResearchRequest,
        *,
        run_id: str | None = None,
        suspend_after_rounds: int | None = None,
    ) -> Result: ...

    async def resume(
        self,
        run_id: str,
        *,
        checkpoint_sha256: str,
        suspend_after_rounds: int | None = None,
    ) -> Result: ...


class QueuedCollection(Record):
    schema_version: Literal["ghimera.queued-collection/1"] = Field(alias="schema")
    item: DeliveryItem
    delivery: DeliveryState

    @model_validator(mode="after")
    def bound(self) -> "QueuedCollection":
        if self.delivery.delivery_id != self.item.identity:
            raise ValueError("queued collection must bind its exact durable outbox identity")
        if self.delivery.acknowledged is not None:
            self.delivery.acknowledged.validate_item(self.item)
        return self

    @property
    def result(self) -> Result:
        return self.item.result


class DeliveryHandoffFailure(Exception):
    def __init__(self, result: Result) -> None:
        self.result = result
        super().__init__("delivery_handoff_failed: preserve completed result before retrying")


class DeliveryHandoffCancelled(asyncio.CancelledError):
    def __init__(self, result: Result) -> None:
        self.result = result
        super().__init__("delivery_handoff_cancelled: preserve completed result before retrying")


class DeliveryCollector:
    """Queue complete results before acknowledging; never wait on destination contact.

    Borrow a Collector or PersistentCollector and caller-owned DeliveryOutbox.
    Existing source/corpus identities and ownership remain unchanged. The caller
    separately schedules outbox dispatch and owns the durable destination.
    """

    def __init__(self, source: ResultSource, outbox: DeliveryOutbox) -> None:
        self._source, self._outbox = source, outbox

    async def persist(self, result: Result) -> QueuedCollection:
        try:
            delivery = await self._outbox.enqueue(result)
            return QueuedCollection(
                schema="ghimera.queued-collection/1",
                item=DeliveryItem(
                    schema="ghimera.delivery-item/1",
                    target=self._outbox.config.target,
                    result=result,
                ),
                delivery=delivery,
            )
        except asyncio.CancelledError as exc:
            raise DeliveryHandoffCancelled(result) from exc
        except Exception as exc:
            raise DeliveryHandoffFailure(result) from exc

    async def collect(
        self, goal: Goal, scope: Scope, *, run_id: str | None = None
    ) -> QueuedCollection:
        await self._outbox.check_ready()
        return await self.persist(await self._source.collect(goal, scope, run_id=run_id))

    async def run(
        self,
        request: str | ResearchRequest,
        *,
        run_id: str | None = None,
        suspend_after_rounds: int | None = None,
    ) -> QueuedCollection:
        await self._outbox.check_ready()
        return await self.persist(
            await self._source.run(
                request, run_id=run_id, suspend_after_rounds=suspend_after_rounds
            )
        )

    async def resume(
        self,
        run_id: str,
        *,
        checkpoint_sha256: str,
        suspend_after_rounds: int | None = None,
    ) -> QueuedCollection:
        await self._outbox.check_ready()
        return await self.persist(
            await self._source.resume(
                run_id,
                checkpoint_sha256=checkpoint_sha256,
                suspend_after_rounds=suspend_after_rounds,
            )
        )
