"""Restart-safe result delivery; collection need not wait on downstream contact."""

import asyncio
import sqlite3
import time
from collections.abc import Callable
from typing import TypeVar

from ghimera.delivery_config import DeliveryOutboxConfig
from ghimera.delivery_sink import DeliverySink
from ghimera.delivery_storage import DeliveryStorage, Failure
from ghimera.delivery_types import DeliveryAck, DeliveryItem, DeliveryState, Result
from ghimera.directory_delivery import DirectoryDeliverySink
from ghimera.owned_worker import off_loop

Output = TypeVar("Output")


class DeliveryOutbox:
    """Caller-driven dispatcher with durable claims and explicit destination injection.

    No daemon, scheduler, implicit credentials, hidden retries or deletion.
    Multiple instances/processes may enqueue/dispatch; transactions own claims.
    Sink idempotency makes an expired or interrupted claim safe to reconcile.
    """

    def __init__(
        self,
        config: DeliveryOutboxConfig,
        *,
        create: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = DeliveryOutboxConfig.model_validate(config.model_dump())
        self._clock = clock
        store = DeliveryStorage(self.config, create=create)
        try:
            self._identity = store.check()
        finally:
            store.close()

    async def _apply(self, operation: Callable[[DeliveryStorage], Output]) -> Output:
        def work() -> Output:
            store = DeliveryStorage(self.config)
            try:
                if store.check() != self._identity:
                    raise ValueError("delivery outbox cannot switch its durable identity")
                return operation(store)
            finally:
                store.close()

        return await off_loop(work)

    async def check_ready(self) -> None:
        await self._apply(lambda store: None)

    async def enqueue(self, result: Result) -> DeliveryState:
        item = DeliveryItem(
            schema="ghimera.delivery-item/1", target=self.config.target, result=result
        )
        now = self._clock()
        return await self._apply(lambda store: store.enqueue(item, now))

    async def state(self, delivery_id: str) -> DeliveryState:
        return await self._apply(lambda store: store.state(delivery_id))

    async def result(self, delivery_id: str) -> Result:
        return (await self._apply(lambda store: store.item(delivery_id))).result

    def _sink_check(self, sink: DeliverySink) -> None:
        if sink.target != self.config.target:
            raise ValueError("outbox requires its explicitly configured destination")
        if isinstance(sink, DirectoryDeliverySink) and sink.directory == self.config.directory:
            raise ValueError("outbox and destination must use distinct directories")

    async def _finish(
        self, item: DeliveryItem, token: str, *, ack: DeliveryAck | None, failure: Failure | None
    ) -> DeliveryState:
        return await self._apply(
            lambda store: store.finish(
                item.identity, token, self._clock(), ack=ack, failure=failure
            )
        )

    async def dispatch(self, sink: DeliverySink) -> tuple[DeliveryState, ...]:
        self._sink_check(sink)
        completed: list[DeliveryState] = []
        attempts = 0

        async def worker() -> None:
            nonlocal attempts
            while attempts < self.config.max_claims_per_dispatch:
                # No await between checking and reserving this dispatch allowance.
                attempts += 1
                state = await self._dispatch_one(sink)
                if state is None:
                    return
                completed.append(state)

        async with asyncio.TaskGroup() as group:
            for _ in range(self.config.dispatch_concurrency):
                group.create_task(worker())
        return tuple(completed)

    async def _dispatch_one(self, sink: DeliverySink) -> DeliveryState | None:
        claim = await self._apply(lambda store: store.claim(self._clock()))
        if claim is None:
            return None
        item, token = claim
        try:
            async with asyncio.timeout(self.config.delivery_timeout_seconds):
                ack = await sink.deliver(item)
        except asyncio.CancelledError:
            # Leave the durable claim uncertain. A later dispatcher reconciles
            # after its configured expiry through the same idempotent sink.
            raise
        except (OSError, sqlite3.Error, TimeoutError):
            return await self._finish(item, token, ack=None, failure="unavailable")
        except ValueError:
            return await self._finish(item, token, ack=None, failure="refused")
        return await self._finish(item, token, ack=ack, failure=None)

    async def prune_acknowledged(self, delivery_id: str, sink: DeliverySink) -> DeliveryState:
        """Explicitly prune only after a fresh exact durable destination readback."""
        self._sink_check(sink)
        state = await self.state(delivery_id)
        if state.status != "acknowledged" or state.acknowledged is None:
            raise ValueError("unacknowledged results cannot be pruned")
        async with asyncio.timeout(self.config.delivery_timeout_seconds):
            observed = await sink.lookup(delivery_id)
        if observed is None or observed != state.acknowledged:
            raise ValueError("pruning requires unchanged durable destination readback")
        return await self._apply(lambda store: store.prune(delivery_id, observed))
