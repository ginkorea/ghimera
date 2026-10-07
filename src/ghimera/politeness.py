"""Run-instance scheduling: per-host/global slots and spacing, robots cache."""

import asyncio
import math
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
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
    cooldown_until: float = 0.0
    backoff_seconds: float = 0.0


@dataclass(frozen=True)
class RobotsEntry:
    parser: Protego
    expires: float


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def retry_after_seconds(value: str | None, now: float) -> float | None:
    """HTTP delta-seconds or date, never shorten an explicit server cooldown.

    An enormous numeric delay remains an infinite cooldown for this bounded run,
    not permission to retry early. Missing/invalid headers use operator backoff.
    """
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdecimal():
        return float(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        return max(0.0, date.timestamp() - now)
    except (ValueError, TypeError, OverflowError):
        return None


class Politeness:
    def __init__(
        self,
        config: GhimeraConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if config.http is None:
            raise ValueError("politeness needs HTTP policy")
        self._config = config
        self._http = config.http
        self._clock, self._wall_clock = clock, wall_clock
        self._sleep, self._uniform = sleep, uniform
        self._global = asyncio.Semaphore(config.global_concurrency)
        self._rate = asyncio.Lock()
        self._next_start = 0.0
        self._hosts: dict[str, HostState] = {}
        self._robots: dict[str, RobotsEntry] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}

    def matches(self, config: GhimeraConfig) -> bool:
        return self._config == config

    def _host(self, origin: str) -> HostState:
        return self._hosts.setdefault(
            origin, HostState(asyncio.Semaphore(self._config.per_host_concurrency), asyncio.Lock())
        )

    def observe_response(self, page: Page) -> None:
        """Update an origin before releasing its active request slot."""
        policy = self._config.cadence
        if policy is None or page.status is None:
            return
        host = self._host(origin_of(page.final_url))
        if page.status not in policy.throttle_statuses:
            if self._clock() >= host.cooldown_until and page.status < 400:
                host.backoff_seconds = 0.0
            return
        previous = host.backoff_seconds
        host.backoff_seconds = min(
            policy.max_backoff_seconds,
            previous * policy.throttle_multiplier if previous else policy.throttle_base_seconds,
        )
        declared = (
            retry_after_seconds(page.header("retry-after"), self._wall_clock())
            if policy.respect_retry_after
            else None
        )
        # The cap limits our exponential policy, not the server's requested wait.
        delay = max(host.backoff_seconds, declared if declared is not None else 0.0)
        host.cooldown_until = max(host.cooldown_until, self._clock() + delay)

    def _spacing(self, origin: str) -> float:
        spacing = self._config.per_host_delay_seconds
        if entry := self._robots.get(origin):
            token = self._http.robots.product_token
            if crawl_delay := entry.parser.crawl_delay(token):
                spacing = max(spacing, crawl_delay)
            if rate := entry.parser.request_rate(token):
                spacing = max(spacing, rate.seconds / rate.requests)
        if policy := self._config.cadence:
            jitter = self._uniform(0.0, policy.jitter_seconds)
            if not math.isfinite(jitter) or not 0.0 <= jitter <= policy.jitter_seconds:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            spacing += jitter
        return spacing

    async def _start(self, host: HostState, origin: str) -> None:
        """Return holding one global slot; a cooled host never reserves it."""
        while True:
            await self._sleep(max(0.0, max(host.next_start, host.cooldown_until) - self._clock()))
            await self._global.acquire()
            try:
                async with self._rate:
                    await self._sleep(max(0.0, self._next_start - self._clock()))
                    now = self._clock()
                    # Another in-flight response may have extended this origin's
                    # cooldown while we waited for the global rate or capacity.
                    if now < max(host.next_start, host.cooldown_until):
                        self._global.release()
                        continue
                    spacing = self._spacing(origin)
                    self._next_start = now + 1 / self._config.global_requests_per_second
                    host.next_start = now + spacing
                    return
            except BaseException:
                self._global.release()
                raise

    @asynccontextmanager
    async def slot(self, url: str) -> AsyncIterator[None]:
        origin = origin_of(url)
        host = self._host(origin)
        async with host.slots:
            async with host.spacing:
                await self._start(host, origin)
            try:
                yield
            finally:
                self._global.release()

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
