"""Explicitly owned background delivery, retry and readback-gated retention."""

import asyncio
import sqlite3
from types import TracebackType
from typing import Literal

from ghimera.delivery_config import DeliveryWorkerConfig
from ghimera.delivery_outbox import DeliveryOutbox
from ghimera.delivery_sink import DeliverySink
from ghimera.delivery_types import DeliveryQueueSummary, DeliveryWorkerStatus


class DeliveryWorker:
    """No task starts on construction/import; an async context owns its lifecycle.

    Collection enqueues independently. This worker polls for later arrivals,
    uses the outbox's durable claims and never deletes without fresh readback.
    A graceful stop finishes the active cycle; expiry cancels and drains it,
    leaving any unfinished delivery claim uncertain for restart reconciliation.
    """

    def __init__(
        self, config: DeliveryWorkerConfig, *, outbox: DeliveryOutbox, sink: DeliverySink
    ) -> None:
        self.config = DeliveryWorkerConfig.model_validate(config.model_dump())
        if self.config.max_prunes_per_cycle > outbox.config.max_items:
            raise ValueError("worker retention must fit the explicit outbox item allowance")
        self._outbox, self._sink = outbox, sink
        self._phase: Literal["stopped", "starting", "running", "stopping", "failed"] = "stopped"
        self._task: asyncio.Task[None] | None = None
        self._stop, self._wake = asyncio.Event(), asyncio.Event()
        self._cycles = self._acknowledged = self._failed = self._pruned = self._refused = 0
        self._last_failure: (
            Literal["unavailable", "refused", "uncertain", "worker_failed"] | None
        ) = None
        self._queue: DeliveryQueueSummary | None = None
        self._cursor: str | None = None
        self._pruned_since_compaction = 0

    @property
    def status(self) -> DeliveryWorkerStatus:
        return DeliveryWorkerStatus(
            schema="ghimera.delivery-worker-status/1",
            policy_sha256=self.config.identity,
            phase=self._phase,
            cycles=self._cycles,
            acknowledged_items=self._acknowledged,
            failed_attempts=self._failed,
            pruned_items=self._pruned,
            prune_refusals=self._refused,
            last_failure=self._last_failure,
            queue=self._queue,
        )

    def wake(self) -> None:
        """Optional enqueue hint; polling still works without caller notifications."""
        self._wake.set()

    def request_stop(self) -> None:
        """Signal-safe loop callback; the lifecycle owner must still await stop()."""
        self._stop.set()
        self._wake.set()

    async def start(self) -> None:
        if self._phase != "stopped":
            raise ValueError("delivery worker is already started or needs a new failed instance")
        self._phase = "starting"
        try:
            await self._outbox.check_destination(self._sink)
            self._queue = await self._outbox.summary()
        except BaseException:
            self._phase, self._last_failure = "failed", "worker_failed"
            raise
        self._stop.clear()
        self._wake.clear()
        self._phase = "running"
        self._task = asyncio.create_task(self._run())

    async def wait(self) -> None:
        if self._task is None:
            raise ValueError("delivery worker has not started")
        try:
            await asyncio.shield(self._task)
        except asyncio.CancelledError:
            owner = asyncio.current_task()
            if (
                self._task.cancelled()
                and self._stop.is_set()
                and owner is not None
                and owner.cancelling() == 0
            ):
                return
            raise

    async def _settle(self, task: asyncio.Task[None]) -> None:
        # Storage work uses off_loop and drains before this task can settle.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        if not task.cancelled():
            task.result()  # Unexpected worker failure remains visible to its owner.

    async def stop(self) -> None:
        self.request_stop()
        task = self._task
        if task is None:
            return
        if task.done() and task.cancelled():
            return
        if not task.done():
            self._phase = "stopping"
        try:
            async with asyncio.timeout(self.config.shutdown_grace_seconds):
                await asyncio.shield(task)
        except TimeoutError:
            task.cancel()
            await self._settle(task)
        except asyncio.CancelledError:
            task.cancel()
            await self._settle(task)
            raise

    async def __aenter__(self) -> "DeliveryWorker":
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.stop()

    async def _deliver(self) -> None:
        states = await self._outbox.dispatch(self._sink)
        for state in states:
            if state.status == "acknowledged":
                self._acknowledged += 1
            else:
                self._failed += 1
                self._last_failure = state.last_failure

    async def _prune(self) -> None:
        if self.config.retention == "keep":
            return
        candidates = await self._outbox.acknowledged_candidates(
            limit=self.config.max_prunes_per_cycle,
            after_delivery_id=self._cursor,
            minimum_age_seconds=self.config.acknowledged_age_seconds,
        )
        if not candidates:
            self._cursor = None
        for identity in candidates:
            # Advance even on a readback refusal so a lost destination copy cannot
            # starve healthy neighbors. A later wrap retries retained refusals.
            self._cursor = identity
            try:
                state = await self._outbox.prune_acknowledged(identity, self._sink)
            except (OSError, sqlite3.Error, TimeoutError):
                self._refused += 1
                self._last_failure = "unavailable"
            except ValueError:
                self._refused += 1
                self._last_failure = "refused"
            else:
                if not state.payload_retained:
                    self._pruned += 1
                    self._pruned_since_compaction += 1
                    if self.config.rotate_acknowledged_attempts:
                        await self._outbox.rotate_acknowledged_attempts(identity)
        interval, free = (
            self.config.compact_after_pruned_items,
            self.config.compaction_minimum_free_bytes,
        )
        if interval is not None and free is not None and self._pruned_since_compaction >= interval:
            try:
                await self._outbox.compact(minimum_free_bytes=free)
            except (OSError, sqlite3.Error):
                self._refused += 1
                self._last_failure = "unavailable"
            else:
                self._pruned_since_compaction = 0

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._wake.clear()
                # Retention must not hold up an independent delivery in this cycle.
                async with asyncio.TaskGroup() as group:
                    group.create_task(self._deliver())
                    group.create_task(self._prune())
                self._queue = await self._outbox.summary()
                self._cycles += 1
                if not self._stop.is_set():
                    try:
                        async with asyncio.timeout(self.config.poll_seconds):
                            await self._wake.wait()
                    except TimeoutError:
                        pass
        except asyncio.CancelledError:
            raise
        except BaseException:
            self._phase, self._last_failure = "failed", "worker_failed"
            raise
        finally:
            if self._phase != "failed":
                self._phase = "stopped"
