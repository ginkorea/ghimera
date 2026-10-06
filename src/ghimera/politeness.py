"""Run-instance scheduling: per-host/global slots and spacing, robots cache."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

from protego import Protego

from ghimera.config import GhimeraConfig
from ghimera.ledger import Ledger
from ghimera.models import LedgerRow, Page
from ghimera.refusals import GhimeraRefused, RefusalCode


@dataclass
class HostState:
    slots: asyncio.Semaphore
    spacing: asyncio.Lock
    next_start: float = 0.0


@dataclass(frozen=True)
class RobotsEntry:
    parser: Protego
    expires: float


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class Politeness:
    def __init__(self, config: GhimeraConfig) -> None:
        if config.http is None:
            raise ValueError("politeness needs HTTP policy")
        self._config = config
        self._http = config.http
        self._global = asyncio.Semaphore(config.global_concurrency)
        self._rate = asyncio.Lock()
        self._next_start = 0.0
        self._hosts: dict[str, HostState] = {}
        self._robots: dict[str, RobotsEntry] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}

    def matches(self, config: GhimeraConfig) -> bool:
        return self._config == config

    @asynccontextmanager
    async def slot(self, url: str) -> AsyncIterator[None]:
        origin = origin_of(url)
        host = self._hosts.setdefault(
            origin, HostState(asyncio.Semaphore(self._config.per_host_concurrency), asyncio.Lock())
        )
        async with host.slots, self._global:
            loop = asyncio.get_running_loop()
            async with host.spacing:
                await asyncio.sleep(max(0.0, host.next_start - loop.time()))
                async with self._rate:
                    await asyncio.sleep(max(0.0, self._next_start - loop.time()))
                    self._next_start = loop.time() + 1 / self._config.global_requests_per_second
                    spacing = self._config.per_host_delay_seconds
                    if entry := self._robots.get(origin):
                        token = self._http.robots.product_token
                        if crawl_delay := entry.parser.crawl_delay(token):
                            spacing = max(spacing, crawl_delay)
                        if rate := entry.parser.request_rate(token):
                            spacing = max(spacing, rate.seconds / rate.requests)
                    host.next_start = loop.time() + spacing
            yield

    async def permits(
        self, url: str, fetch: Callable[[str], Awaitable[Page]], ledger: Ledger
    ) -> None:
        # The sole robots override enforcement point; no global environment flag.
        policy = self._http.robots
        if policy.mode == "recorded_override":
            decision = policy.decision
            if decision is None:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            if urlsplit(url).hostname in decision.allowed_hosts:
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="policy",
                        url=url,
                        reason=f"robots_override:{decision.decision_id}:{decision.decided_by}:"
                        f"{decision.reason}",
                    )
                )
                return
        origin = origin_of(url)
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        loop = asyncio.get_running_loop()
        async with lock:
            entry = self._robots.get(origin)
            if entry is None or entry.expires <= loop.time():
                try:
                    page = await fetch(origin + "/robots.txt")
                except GhimeraRefused as exc:
                    if exc.code == RefusalCode.BUDGET_EXHAUSTED:
                        raise
                    raise GhimeraRefused(RefusalCode.ROBOTS_DISALLOWED) from None
                if page.status in {404, 410}:
                    parser = Protego.parse("")
                elif page.status == 200:
                    if page.content_type not in {"text/plain", "text/html"}:
                        raise GhimeraRefused(RefusalCode.ROBOTS_DISALLOWED)
                    parser = Protego.parse(page.body.decode("utf-8", errors="replace"))
                else:
                    raise GhimeraRefused(RefusalCode.ROBOTS_DISALLOWED)
                entry = RobotsEntry(parser, loop.time() + self._http.robots_cache_seconds)
                self._robots[origin] = entry
        if not entry.parser.can_fetch(url, self._http.robots.product_token):
            raise GhimeraRefused(RefusalCode.ROBOTS_DISALLOWED)
