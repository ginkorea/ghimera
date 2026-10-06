"""Final fetch ladder; C1 supplies real routes, robots and bounded streaming."""

import asyncio
from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import ClassVar, final

from chimera.budget import RunBudget
from chimera.ledger import Ledger
from chimera.models import FetchRequest, LedgerRow, Page, Scope
from chimera.refusals import ChimeraRefused, RefusalCode


class FetchRoute(ABC):
    name: ClassVar[str]
    needs_browser: ClassVar[bool]
    cost: ClassVar[int]
    TEMPLATE: ClassVar[str] = "execute"
    REFERENCE = MappingProxyType({"declaration": "FakeRoute", "template": "FakeRoute"})

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if "execute" in cls.__dict__:
            raise TypeError("FetchRoute.execute is final; implement attempt and escalation_reason")
        if (
            not isinstance(getattr(cls, "name", None), str)
            or not cls.name.strip()
            or not isinstance(getattr(cls, "needs_browser", None), bool)
            or type(getattr(cls, "cost", None)) is not int
            or cls.cost < 0
        ):
            raise TypeError("FetchRoute declares name, bool needs_browser, nonnegative cost")

    @final
    async def execute(self, request: FetchRequest) -> Page:
        return await self.attempt(request)

    @abstractmethod
    async def attempt(self, request: FetchRequest) -> Page: ...

    @abstractmethod
    def escalation_reason(self, page: Page) -> str | None: ...


class FetchLadder:
    def __init__(self, routes: tuple[FetchRoute, ...]) -> None:
        if not routes or len({route.name for route in routes}) != len(routes):
            raise ValueError("a ladder needs distinct named routes")
        self._routes = tuple(sorted(routes, key=lambda route: route.cost))

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if "fetch" in cls.__dict__:
            raise TypeError("FetchLadder.fetch is final")

    @final
    async def fetch(self, url: str, scope: Scope, budget: RunBudget, ledger: Ledger) -> Page:
        if not scope.permits(url):
            raise ChimeraRefused(RefusalCode.OUT_OF_SCOPE)
        for route in self._routes:
            budget.reserve_fetch()
            started = budget.clock()
            timeout = min(budget.config.request_timeout_seconds, budget.remaining_seconds)
            request = FetchRequest(
                url=url, max_bytes=budget.remaining_bytes, timeout_seconds=timeout
            )
            try:
                async with asyncio.timeout(timeout):
                    page = await route.execute(request)
            except (ChimeraRefused, TimeoutError) as exc:
                code = exc.code if isinstance(exc, ChimeraRefused) else RefusalCode.FETCH_FAILED
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="fetch",
                        url=url,
                        route=route.name,
                        refusal=code,
                        reason=code.value,
                        latency_seconds=max(0.0, budget.clock() - started),
                    )
                )
                # A challenge, login, paywall or robots denial is terminal, never escalated.
                if code != RefusalCode.FETCH_FAILED:
                    raise ChimeraRefused(code) from exc
                continue
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="fetch",
                    url=url,
                    route=route.name,
                    status=page.status,
                    bytes_read=len(page.body),
                    reason="route_result",
                    latency_seconds=max(0.0, budget.clock() - started),
                )
            )
            budget.record_bytes(len(page.body))
            if not scope.permits(page.final_url):
                raise ChimeraRefused(RefusalCode.OUT_OF_SCOPE)
            if 400 <= page.status < 500:
                raise ChimeraRefused(RefusalCode.FETCH_FAILED)
            if page.status >= 500:
                continue
            if page.content_type not in scope.content_types:
                raise ChimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
            reason = route.escalation_reason(page)
            if reason is None:
                return page
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="fallback",
                    url=url,
                    route=route.name,
                    reason=reason,
                )
            )
        raise ChimeraRefused(RefusalCode.FETCH_FAILED)
