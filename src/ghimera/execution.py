"""Run-owned stage slots; upstream progress is bounded, not batch-serial."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from ghimera.execution_config import ExecutionConfig
from ghimera.spend import RunSpend

Stage = Literal["extraction", "scoring", "judge", "semantic", "visual"]


class StageSlots:
    """Optional legacy compatibility; every enabled capacity is an operator input."""

    def __init__(self, policy: ExecutionConfig | None, spend: RunSpend) -> None:
        self._spend = spend
        self._slots: dict[Stage, asyncio.Semaphore] = {}
        if policy is not None:
            self._slots = {
                "extraction": asyncio.Semaphore(policy.extraction_workers),
                "scoring": asyncio.Semaphore(policy.scoring_workers),
                "judge": asyncio.Semaphore(policy.judge_workers),
                "semantic": asyncio.Semaphore(policy.semantic_workers),
                "visual": asyncio.Semaphore(policy.visual_workers),
            }

    @asynccontextmanager
    async def slot(self, stage: Stage) -> AsyncIterator[None]:
        semaphore = self._slots.get(stage)
        if semaphore is None:
            yield
            return
        await semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()
