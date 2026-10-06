"""Final fetch ladder: scope → politeness → bounded attempts → ledger → result."""

import asyncio
import random
from abc import ABC, abstractmethod
from collections import OrderedDict
from types import MappingProxyType
from typing import ClassVar, final
from urllib.parse import urljoin

from chimera.browser import PageRenderer, ResourceFetcher
from chimera.budget import RunBudget
from chimera.config import ChimeraConfig
from chimera.ledger import Ledger
from chimera.models import FetchRequest, LedgerRow, Page, Scope
from chimera.politeness import Politeness
from chimera.refusals import (
    ChimeraRefused,
    FetchCancelled,
    FetchFailure,
    HttpStatusRefused,
    RefusalCode,
)
from chimera.transport_types import TransportEvidence


class FetchRoute(ABC):
    name: ClassVar[str]
    needs_browser: ClassVar[bool]
    cost: ClassVar[int]
    uses_http: ClassVar[bool] = False
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

    def validate_config(self, config: ChimeraConfig) -> None:
        """Stateless fixture routes accept config; stateful routes check their binding."""
        return None

    def validate_redirect(self, previous: str, target: str) -> None:
        """Production providers enforce their configured transport transition here."""
        return None

    def transport_selection(self, url: str) -> TransportEvidence | None:
        return None

    @abstractmethod
    async def attempt(self, request: FetchRequest) -> Page: ...

    @abstractmethod
    def escalation_reason(self, page: Page) -> str | None: ...


class FetchLadder:
    def __init__(
        self, routes: tuple[FetchRoute, ...], *, renderer: PageRenderer | None = None
    ) -> None:
        if not routes or len({route.name for route in routes}) != len(routes):
            raise ValueError("a ladder needs distinct named routes")
        self._routes = tuple(sorted(routes, key=lambda route: route.cost))
        self._politeness: Politeness | None = None
        self._cache: OrderedDict[str, Page] = OrderedDict()
        self._renderer = renderer

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if "fetch" in cls.__dict__:
            raise TypeError("FetchLadder.fetch is final")

    @final
    async def fetch(self, url: str, scope: Scope, budget: RunBudget, ledger: Ledger) -> Page:
        return await self._fetch(url, scope, budget, ledger, allow_render=True)

    async def _fetch(
        self,
        url: str,
        scope: Scope,
        budget: RunBudget,
        ledger: Ledger,
        *,
        allow_render: bool,
    ) -> Page:
        if self._renderer is not None:
            self._renderer.validate_config(budget.config)
        elif budget.config.browser is not None:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if not scope.permits(url):
            raise ChimeraRefused(RefusalCode.OUT_OF_SCOPE)
        for route in self._routes:
            route.validate_config(budget.config)
            try:
                if route.uses_http:
                    if budget.config.http is None:
                        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                    if self._politeness is None:
                        self._politeness = Politeness(budget.config)
                    elif not self._politeness.matches(budget.config):
                        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                    page = await self._follow(route, url, scope, budget, ledger)
                else:
                    page = await self._attempt(route, url, budget, ledger)
            except ChimeraRefused as exc:
                code = exc.code
                # A challenge, login, paywall or robots denial is terminal, never escalated.
                if code != RefusalCode.FETCH_FAILED:
                    raise
                # 4xx must not be tried again through a more expensive route.
                if isinstance(exc, HttpStatusRefused):
                    raise
                continue
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
            if reason == "javascript_required" and allow_render and self._renderer is not None:
                return await self._render(route, page, scope, budget, ledger)
        raise ChimeraRefused(RefusalCode.FETCH_FAILED)

    async def _render(
        self,
        route: FetchRoute,
        page: Page,
        scope: Scope,
        budget: RunBudget,
        ledger: Ledger,
    ) -> Page:
        renderer, policy = self._renderer, budget.config.browser
        if renderer is None or policy is None:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        ladder = self
        resource_scope = Scope.model_validate(
            {
                **scope.model_dump(),
                "content_types": policy.resource_content_types,
            }
        )

        class BoundResources(ResourceFetcher):
            async def fetch(self, url: str) -> Page:
                # Same ladder, budget, cache and global/per-host politeness; only
                # the declared subsidiary MIME types differ. No render recursion.
                return await ladder._fetch(
                    url,
                    resource_scope,
                    budget,
                    ledger,
                    allow_render=False,
                )

        started = budget.clock()
        try:
            result = await renderer.render(
                page,
                scope,
                BoundResources(),
                timeout_seconds=budget.remaining_seconds,
            )
            rendered = Page.model_validate({**page.model_dump(), "rendered": result})
            # A JS-created challenge/login/paywall is terminal too. The route's
            # detector sees DOM, while the returned Page still retains raw bytes.
            inspected = Page.model_validate(
                {
                    **page.model_dump(),
                    "body": result.html,
                    "rendered": None,
                }
            )
            route.escalation_reason(inspected)
        except ChimeraRefused as exc:
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="render",
                    url=page.final_url,
                    route=renderer.name,
                    refusal=exc.code,
                    reason=exc.code.value,
                    latency_seconds=max(0.0, budget.clock() - started),
                )
            )
            raise
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event="render",
                url=page.final_url,
                route=renderer.name,
                rendered=result,
                reason="isolated_render_complete",
                latency_seconds=max(0.0, budget.clock() - started),
            )
        )
        return rendered

    async def _attempt(
        self,
        route: FetchRoute,
        url: str,
        budget: RunBudget,
        ledger: Ledger,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> Page:
        timeout = min(budget.config.request_timeout_seconds, budget.remaining_seconds)
        if timeout <= 0:
            raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        async with asyncio.timeout(timeout):
            if route.uses_http and self._politeness is not None:
                async with self._politeness.slot(url):
                    return await self._execute(route, url, budget, ledger, headers)
            return await self._execute(route, url, budget, ledger, headers)

    async def _execute(
        self,
        route: FetchRoute,
        url: str,
        budget: RunBudget,
        ledger: Ledger,
        headers: tuple[tuple[str, str], ...],
    ) -> Page:
        policy = budget.config.http if route.uses_http else None
        maximum = policy.max_response_bytes if policy else budget.remaining_bytes
        allowance = budget.reserve_bytes(maximum)
        try:
            budget.reserve_fetch()
        except ChimeraRefused:
            budget.release_bytes(allowance)
            raise
        started = budget.clock()
        request = FetchRequest.model_validate(
            {
                "url": url,
                "max_bytes": allowance,
                "timeout_seconds": min(
                    budget.config.request_timeout_seconds, budget.remaining_seconds
                ),
                "headers": headers,
            }
        )
        page: Page | None = None
        code: RefusalCode | None = None
        bytes_read = 0
        try:
            try:
                async with asyncio.timeout(request.timeout_seconds):
                    page = await route.execute(request)
                bytes_read = len(page.body)
            except (ChimeraRefused, TimeoutError) as exc:
                code = exc.code if isinstance(exc, ChimeraRefused) else RefusalCode.FETCH_FAILED
                bytes_read = exc.bytes_read if isinstance(exc, FetchFailure) else 0
            except asyncio.CancelledError as exc:
                bytes_read = exc.bytes_read if isinstance(exc, FetchCancelled) else 0
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="fetch",
                        url=url,
                        route=route.name,
                        refusal=RefusalCode.FETCH_FAILED,
                        reason="request_cancelled",
                        bytes_read=bytes_read,
                        latency_seconds=max(0.0, budget.clock() - started),
                        transport=route.transport_selection(url),
                    )
                )
                budget.record_bytes(bytes_read)
                # asyncio.timeout converts precisely CancelledError, not our subtype.
                raise asyncio.CancelledError from None
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="fetch",
                    url=url,
                    route=route.name,
                    status=page.status if page else None,
                    bytes_read=bytes_read,
                    refusal=code,
                    reason=code.value if code else "route_result",
                    transport=page.transport if page else route.transport_selection(url),
                    latency_seconds=max(0.0, budget.clock() - started),
                )
            )
            budget.record_bytes(bytes_read)
            if code is not None:
                raise ChimeraRefused(code)
            if page is None:
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            return page
        finally:
            budget.release_bytes(allowance)

    async def _follow(
        self,
        route: FetchRoute,
        url: str,
        scope: Scope,
        budget: RunBudget,
        ledger: Ledger,
        *,
        robots: bool = False,
    ) -> Page:
        policy, politeness = budget.config.http, self._politeness
        if policy is None or politeness is None:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        from chimera.http import page_barrier

        current = url
        for hop in range(policy.max_redirects + 1):
            if not scope.permits(current):
                raise ChimeraRefused(RefusalCode.OUT_OF_SCOPE)
            if not robots:

                async def robots_get(target: str) -> Page:
                    return await self._follow(route, target, scope, budget, ledger, robots=True)

                await politeness.permits(current, robots_get, ledger)
            prior = self._cache.get(current) if not robots else None
            headers: tuple[tuple[str, str], ...] = ()
            if prior is not None:
                if etag := prior.header("etag"):
                    headers = (("if-none-match", etag),)
                elif modified := prior.header("last-modified"):
                    headers = (("if-modified-since", modified),)
            page: Page | None = None
            for retry in range(budget.config.retry_budget + 1):
                try:
                    page = await self._attempt(route, current, budget, ledger, headers)
                    if barrier := page_barrier(page):
                        raise ChimeraRefused(barrier)
                    if page.status < 500:
                        break
                except ChimeraRefused as exc:
                    if exc.code != RefusalCode.FETCH_FAILED:
                        raise
                    page = None
                if retry == budget.config.retry_budget:
                    raise ChimeraRefused(RefusalCode.FETCH_FAILED)
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="fallback",
                        url=current,
                        route=route.name,
                        reason="transient_retry",
                    )
                )
                delay = policy.retry_backoff_seconds * (2**retry)
                delay += random.uniform(0, policy.retry_jitter_seconds)
                async with asyncio.timeout(budget.remaining_seconds):
                    await asyncio.sleep(delay)
            if page is None:
                raise ChimeraRefused(RefusalCode.FETCH_FAILED)
            if not scope.permits(page.final_url):
                raise ChimeraRefused(RefusalCode.OUT_OF_SCOPE)
            if page.status in {301, 302, 303, 307, 308}:
                location = page.header("location")
                if location is None or hop == policy.max_redirects:
                    raise ChimeraRefused(RefusalCode.FETCH_FAILED)
                target = urljoin(page.final_url, location)
                route.validate_redirect(page.final_url, target)
                current = target
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="fallback",
                        url=current,
                        route=route.name,
                        reason="redirect",
                    )
                )
                continue
            if page.status == 304:
                if prior is None:
                    raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                self._cache.move_to_end(current)
                return prior.model_copy(update={"url": url, "revalidated": True})
            if not robots and (400 <= page.status or 300 <= page.status < 400):
                raise HttpStatusRefused(page.status)
            if not robots and page.status == 200:
                self._cache[current] = page
                self._cache.move_to_end(current)
                while len(self._cache) > policy.conditional_cache_entries:
                    self._cache.popitem(last=False)
            return page.model_copy(update={"url": url})
        raise ChimeraRefused(RefusalCode.FETCH_FAILED)
