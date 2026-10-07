"""One run's policy/budget ownership during caller-bound browser navigation."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from ghimera.browser_download_stream import DownloadSpend
from ghimera.browser_operation_types import BrowserSourceAction
from ghimera.budget import RunBudget
from ghimera.ledger import Ledger
from ghimera.models import LedgerRow
from ghimera.politeness import Politeness
from ghimera.refusals import GhimeraRefused, RefusalCode


@dataclass
class _ReadLease:
    max_bytes: int
    bytes_read: int = 0


class RunBrowserOutputBudget:
    """Reserve only during an actual output read; robots never waits on it."""

    def __init__(self, budget: RunBudget) -> None:
        self._budget = budget
        self._bytes_read = 0

    @property
    def bytes_read(self) -> int:
        return self._bytes_read

    @asynccontextmanager
    async def reading(self, maximum: int) -> AsyncIterator[DownloadSpend]:
        if type(maximum) is not int or maximum <= 0:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        allowance = await self._budget.wait_bytes(maximum)
        spend = _ReadLease(allowance)
        try:
            yield spend
        finally:
            try:
                if type(spend.bytes_read) is not int or not 0 <= spend.bytes_read <= allowance:
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                self._budget.record_bytes(spend.bytes_read)
                self._bytes_read += spend.bytes_read
            finally:
                self._budget.release_bytes(allowance)


class RunBrowserAdmission:
    """Move one source-action slot between hops, releasing it before policy I/O."""

    def __init__(
        self,
        budget: RunBudget,
        politeness: Politeness,
        ledger: Ledger,
        permitted: Callable[[str], Awaitable[None]],
    ) -> None:
        self._budget = budget
        self._politeness = politeness
        self._ledger = ledger
        self._permitted = permitted
        self._slot: AbstractAsyncContextManager[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def _release(self) -> None:
        slot, self._slot = self._slot, None
        if slot is not None:
            await slot.__aexit__(None, None, None)

    async def admit(self, url: str) -> None:
        await self._admit(url, "main_frame_navigation")

    async def admit_inline(self, url: str) -> None:
        await self._admit(url, "inline_fetch")

    async def _admit(
        self, url: str, action: Literal["main_frame_navigation", "inline_fetch"]
    ) -> None:
        async with self._lock:
            if self._closed:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            await self._release()
            self._budget.check_time()
            await self._permitted(url)
            policy = self._budget.config.human_browser
            if policy is None or policy.navigation is None:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            self._budget.reserve_fetch()
            self._ledger.append(
                LedgerRow(
                    sequence=self._ledger.next_sequence,
                    event="policy",
                    url=url,
                    reason="browser_source_action_reserved",
                    browser_action=BrowserSourceAction(
                        schema="ghimera.browser-source-action/1",
                        action=action,
                        url=url,
                        policy_digest=policy.content_digest(),
                        target_id=policy.target_id,
                    ),
                )
            )
            slot = self._politeness.slot(url)
            await slot.__aenter__()
            self._slot = slot

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            await self._release()

    async def yield_to_human(self) -> None:
        async with self._lock:
            if self._closed:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            await self._release()
