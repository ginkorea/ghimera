"""Final fetch ladder: scope → politeness → bounded attempts → ledger → result."""

import asyncio
import random
from abc import ABC, abstractmethod
from collections import OrderedDict
from types import MappingProxyType
from typing import ClassVar, final

from ghimera.browser import PageRenderer, ResourceFetcher
from ghimera.browser_operation import RunBrowserAdmission, RunBrowserOutputBudget
from ghimera.browser_operation_types import BrowserOperation
from ghimera.budget import RunBudget
from ghimera.challenge_types import ChallengeEvidence
from ghimera.challenges import ChallengeCancelled, ChallengeFailure
from ghimera.config import GhimeraConfig
from ghimera.human_browser_errors import HumanCaptureCancelled, HumanCaptureFailure
from ghimera.human_browser_types import AssistanceObservation
from ghimera.ledger import Ledger
from ghimera.models import FetchRequest, LedgerRow, Page, Scope
from ghimera.politeness import Politeness
from ghimera.refusals import (
    FetchCancelled,
    FetchFailure,
    GhimeraRefused,
    HttpStatusRefused,
    RefusalCode,
)
from ghimera.response import REDIRECT_STATUSES, conditional_cache_permitted
from ghimera.source_refresh import SourceRefreshFailure, SourceRefreshKey, SourceRefreshStore
from ghimera.source_session_types import SourceSessionUse
from ghimera.transport_types import TransportEvidence


class FetchRoute(ABC):
    name: ClassVar[str]
    needs_browser: ClassVar[bool]
    cost: ClassVar[int]
    uses_http: ClassVar[bool] = False
    captures_browser_dom: ClassVar[bool] = False
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
    async def execute(
        self, request: FetchRequest, *, operation: BrowserOperation | None = None
    ) -> Page:
        if operation is None:
            return await self.attempt(request)
        return await self.attempt_browser(request, operation)

    async def attempt_browser(self, request: FetchRequest, operation: BrowserOperation) -> Page:
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    def validate_config(self, config: GhimeraConfig) -> None:
        """Stateless fixture routes accept config; stateful routes check their binding."""
        return None

    def handles(self, url: str) -> bool:
        return True

    def validate_redirect(self, previous: str, target: str) -> None:
        """Production providers enforce their configured transport transition here."""
        return None

    def transport_selection(self, url: str) -> TransportEvidence | None:
        return None

    def source_session_selection(self, url: str) -> SourceSessionUse | None:
        return None

    def conditional_binding(self, url: str) -> str | None:
        """Providers opt in only when they can bind the full request representation."""
        return None

    async def clear_challenge(
        self, page: Page, *, timeout_seconds: float, max_bytes: int
    ) -> tuple[ChallengeEvidence, int]:
        raise GhimeraRefused(RefusalCode.CHALLENGE_NOT_SOLVED)

    @abstractmethod
    async def attempt(self, request: FetchRequest) -> Page: ...

    @abstractmethod
    def escalation_reason(self, page: Page) -> str | None: ...


class FetchLadder:
    def __init__(
        self,
        routes: tuple[FetchRoute, ...],
        *,
        renderer: PageRenderer | None = None,
        source_refresh: SourceRefreshStore | None = None,
    ) -> None:
        if not routes or len({route.name for route in routes}) != len(routes):
            raise ValueError("a ladder needs distinct named routes")
        self._routes = tuple(sorted(routes, key=lambda route: route.cost))
        self._politeness: Politeness | None = None
        self._cache: OrderedDict[tuple[str, str, str | None], Page] = OrderedDict()
        self._renderer = renderer
        self._source_refresh = source_refresh

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if "fetch" in cls.__dict__:
            raise TypeError("FetchLadder.fetch is final")

    @final
    async def fetch(self, url: str, scope: Scope, budget: RunBudget, ledger: Ledger) -> Page:
        return await self._fetch(url, scope, budget, ledger, allow_render=True)

    async def fetch_image(self, url: str, scope: Scope, budget: RunBudget, ledger: Ledger) -> Page:
        """Passive image HTTP uses ordinary guards, but cannot escalate to a browser."""
        return await self._fetch(url, scope, budget, ledger, allow_render=False)

    def discard_cached(self, *urls: str) -> None:
        """Release transient resource bytes after relevance acceptance/refusal."""
        for key in tuple(self._cache):
            if key[1] in urls:
                self._cache.pop(key, None)

    def resource_fetcher(self, scope: Scope, budget: RunBudget, ledger: Ledger) -> ResourceFetcher:
        """Bind single-hop HTTP to the same run's policy, cache and accounting.

        The browser, not an invisible HTTP redirect loop, consumes the 3xx
        response. Its next request must return through this boundary.
        """
        ladder = self

        class BoundResources(ResourceFetcher):
            async def fetch(self, url: str) -> Page:
                return await ladder._fetch(
                    url, scope, budget, ledger, allow_render=False, single_hop=True
                )

        return BoundResources()

    async def _fetch(
        self,
        url: str,
        scope: Scope,
        budget: RunBudget,
        ledger: Ledger,
        *,
        allow_render: bool,
        single_hop: bool = False,
    ) -> Page:
        refresh = budget.config.source_refresh
        if (refresh is None) != (self._source_refresh is None) or (
            refresh is not None
            and self._source_refresh is not None
            and refresh != self._source_refresh.policy
        ):
            raise SourceRefreshFailure("fetch ladder requires its matching source refresh store")
        if self._renderer is not None:
            self._renderer.validate_config(budget.config)
        elif budget.config.browser is not None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if not scope.permits(url):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        for route in self._routes:
            route.validate_config(budget.config)
            if not route.handles(url) or (single_hop and route.captures_browser_dom):
                continue
            try:
                if route.uses_http or route.captures_browser_dom:
                    if budget.config.http is None:
                        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                    if self._politeness is None:
                        self._politeness = Politeness(budget.config)
                    elif not self._politeness.matches(budget.config):
                        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                    if route.captures_browser_dom:
                        browser = budget.config.human_browser
                        if browser is not None and browser.navigation is not None:
                            page = await self._guarded_browser(route, url, scope, budget, ledger)
                        else:
                            await self._browser_permitted(url, scope, budget, ledger)
                            page = await self._attempt(route, url, budget, ledger, scope=scope)
                    else:
                        page = (
                            await self._hop(route, url, scope, budget, ledger)
                            if single_hop
                            else await self._follow(route, url, scope, budget, ledger)
                        )
                else:
                    page = await self._attempt(route, url, budget, ledger)
            except GhimeraRefused as exc:
                code = exc.code
                if route.captures_browser_dom:
                    # A human/browser interaction is not replayable through a
                    # different session just because a transport timed out.
                    raise
                # Unresolved challenges and entitlement/robots walls stay terminal.
                if code != RefusalCode.FETCH_FAILED:
                    raise
                # 4xx must not be tried again through a more expensive route.
                if isinstance(exc, HttpStatusRefused):
                    raise
                continue
            if not scope.permits(page.final_url):
                raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
            if page.status is not None and 400 <= page.status < 500:
                raise GhimeraRefused(RefusalCode.FETCH_FAILED)
            if page.status is not None and page.status >= 500:
                continue
            if single_hop and page.status in REDIRECT_STATUSES:
                self._redirect(route, page, scope, ledger)
                return page
            if page.content_type not in scope.content_types:
                raise GhimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
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
        raise GhimeraRefused(RefusalCode.FETCH_FAILED)

    async def _browser_permitted(
        self, url: str, scope: Scope, budget: RunBudget, ledger: Ledger
    ) -> None:
        await self._browser_url_permitted(url, scope, budget, ledger)
        selected = budget.config.human_browser
        if selected is not None and selected.downloads is not None:
            action = next(
                (item for item in selected.downloads.actions if item.source_url == url), None
            )
            if action is not None and action.navigation_url != url:
                await self._browser_url_permitted(action.navigation_url, scope, budget, ledger)

    async def _browser_url_permitted(
        self, url: str, scope: Scope, budget: RunBudget, ledger: Ledger
    ) -> None:
        selected = budget.config.human_browser
        if not scope.permits(url) or selected is None or not selected.permits(url):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        http_routes = tuple(
            route for route in self._routes if route.uses_http and route.handles(url)
        )
        if len(http_routes) != 1 or self._politeness is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        http_route = http_routes[0]
        http_route.validate_config(budget.config)

        async def robots_get(target: str) -> Page:
            return await self._follow(http_route, target, scope, budget, ledger, robots=True)

        # Browser-owned traffic is separately declared; top-level collection
        # still honors the existing exact-host robots decision and cadence.
        await self._politeness.permits(url, robots_get, ledger)

    async def _guarded_browser(
        self, route: FetchRoute, url: str, scope: Scope, budget: RunBudget, ledger: Ledger
    ) -> Page:
        if self._politeness is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

        async def permitted(target: str) -> None:
            await self._browser_url_permitted(target, scope, budget, ledger)

        admission = RunBrowserAdmission(budget, self._politeness, ledger, permitted)
        operation = BrowserOperation(admission, RunBrowserOutputBudget(budget))
        try:
            return await self._execute(route, url, budget, ledger, (), scope, operation)
        finally:
            await admission.close()

    async def _render(
        self,
        route: FetchRoute,
        page: Page,
        scope: Scope,
        budget: RunBudget,
        ledger: Ledger,
        *,
        allow_challenge: bool = True,
    ) -> Page:
        renderer, policy = self._renderer, budget.config.browser
        if renderer is None or policy is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        resource_scope = Scope.model_validate(
            {
                **scope.model_dump(),
                "content_types": policy.resource_content_types,
            }
        )

        started = budget.clock()
        try:
            result = await renderer.render(
                page,
                scope,
                self.resource_fetcher(resource_scope, budget, ledger),
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
        except GhimeraRefused as exc:
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
            if (
                exc.code == RefusalCode.CHALLENGE_NOT_SOLVED
                and allow_challenge
                and budget.config.challenges is not None
            ):
                await self._clear_challenge(route, page, budget, ledger)
                retry = await self._follow(route, page.final_url, scope, budget, ledger)
                if retry.content_type not in scope.content_types:
                    raise GhimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED) from None
                reason = route.escalation_reason(retry)
                if reason == "javascript_required":
                    # A second rendered challenge is terminal. Never recursively
                    # launch solvers until the outer wall-clock expires.
                    return await self._render(
                        route, retry, scope, budget, ledger, allow_challenge=False
                    )
                if reason is not None:
                    raise GhimeraRefused(RefusalCode.CHALLENGE_NOT_SOLVED) from None
                return retry
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
        *,
        scope: Scope | None = None,
    ) -> Page:
        timeout = min(budget.config.request_timeout_seconds, budget.remaining_seconds)
        if timeout <= 0:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        async with asyncio.timeout(timeout):
            if (route.uses_http or route.captures_browser_dom) and self._politeness is not None:
                async with self._politeness.slot(url):
                    page = await self._execute(route, url, budget, ledger, headers, scope)
                    self._politeness.observe_response(page)
                    return page
            return await self._execute(route, url, budget, ledger, headers, scope)

    async def _execute(
        self,
        route: FetchRoute,
        url: str,
        budget: RunBudget,
        ledger: Ledger,
        headers: tuple[tuple[str, str], ...],
        scope: Scope | None,
        operation: BrowserOperation | None = None,
    ) -> Page:
        policy = budget.config.http if route.uses_http else None
        maximum = policy.max_response_bytes if policy else budget.remaining_bytes
        allowance = await budget.wait_bytes(maximum) if operation is None else maximum
        try:
            if operation is None:
                budget.reserve_fetch()
        except GhimeraRefused:
            if operation is None:
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
                "scope": scope,
            }
        )
        page: Page | None = None
        code: RefusalCode | None = None
        bytes_read = 0
        assistance: tuple[AssistanceObservation, ...] = ()
        try:
            try:
                async with asyncio.timeout(request.timeout_seconds):
                    page = (
                        await route.execute(request)
                        if operation is None
                        else await route.execute(request, operation=operation)
                    )
                bytes_read = (
                    page.human_browser.collector_bytes_read
                    if page.human_browser
                    else len(page.body)
                )
                page = Page.model_validate(page.model_dump())
                if route.captures_browser_dom != (page.human_browser is not None):
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                if operation is not None and bytes_read != operation.output.bytes_read:
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            except (GhimeraRefused, TimeoutError) as exc:
                code = exc.code if isinstance(exc, GhimeraRefused) else RefusalCode.FETCH_FAILED
                bytes_read = max(bytes_read, exc.bytes_read if isinstance(exc, FetchFailure) else 0)
                assistance = exc.assistance if isinstance(exc, HumanCaptureFailure) else ()
                if operation is not None:
                    bytes_read = operation.output.bytes_read
            except asyncio.CancelledError as exc:
                bytes_read = exc.bytes_read if isinstance(exc, FetchCancelled) else 0
                assistance = exc.assistance if isinstance(exc, HumanCaptureCancelled) else ()
                if operation is not None:
                    bytes_read = operation.output.bytes_read
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="fetch",
                        url=url,
                        route=route.name,
                        refusal=RefusalCode.FETCH_FAILED,
                        reason="request_cancelled",
                        source_session=route.source_session_selection(url),
                        bytes_read=bytes_read,
                        latency_seconds=max(0.0, budget.clock() - started),
                        transport=route.transport_selection(url),
                        human_assistance=assistance,
                    )
                )
                if operation is None:
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
                    source_session=page.source_session
                    if page
                    else route.source_session_selection(url),
                    challenge_use=page.challenge_use if page else None,
                    transport=page.transport if page else route.transport_selection(url),
                    human_browser=page.human_browser if page and code is None else None,
                    human_assistance=assistance,
                    latency_seconds=max(0.0, budget.clock() - started),
                )
            )
            if operation is None:
                budget.record_bytes(bytes_read)
            if code is not None:
                raise GhimeraRefused(code)
            if page is None:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            return page
        finally:
            if operation is None:
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
        policy = budget.config.http
        if policy is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        current = url
        for hop in range(policy.max_redirects + 1):
            page = await self._hop(route, current, scope, budget, ledger, robots=robots)
            if page.status in REDIRECT_STATUSES:
                if hop == policy.max_redirects:
                    raise GhimeraRefused(RefusalCode.FETCH_FAILED)
                current = self._redirect(route, page, scope, ledger)
                continue
            return page.model_copy(update={"url": url})
        raise GhimeraRefused(RefusalCode.FETCH_FAILED)

    @staticmethod
    def _redirect(route: FetchRoute, page: Page, scope: Scope, ledger: Ledger) -> str:
        from ghimera.response import redirect_target

        target = redirect_target(page.final_url, page.headers)
        if target is None:
            raise GhimeraRefused(RefusalCode.FETCH_FAILED)
        if not scope.permits(target):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        route.validate_redirect(page.final_url, target)
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event="fallback",
                url=target,
                route=route.name,
                reason="redirect",
            )
        )
        return target

    async def _hop(
        self,
        route: FetchRoute,
        url: str,
        scope: Scope,
        budget: RunBudget,
        ledger: Ledger,
        *,
        robots: bool = False,
    ) -> Page:
        """One HTTP hop; shared by document following and browser fulfilment."""
        policy, politeness = budget.config.http, self._politeness
        if policy is None or politeness is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        from ghimera.http import page_barrier

        if not scope.permits(url):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        if not robots:

            async def robots_get(target: str) -> Page:
                return await self._follow(route, target, scope, budget, ledger, robots=True)

            await politeness.permits(url, robots_get, ledger)
        binding = route.conditional_binding(url)
        cache_key = (route.name, url, binding)
        store = self._source_refresh
        key = (
            SourceRefreshKey(
                schema="ghimera.source-refresh-key/1",
                url=url,
                route=route.name,
                representation_sha256=binding,
            )
            if not robots and store is not None and url in store.policy.urls and binding is not None
            else None
        )
        original = await store.latest(key) if store is not None and key is not None else None
        prior = (
            original.page
            if original is not None
            else self._cache.get(cache_key)
            if not robots and key is None
            else None
        )
        headers: tuple[tuple[str, str], ...] = ()
        if prior is not None:
            if etag := prior.header("etag"):
                headers = (("if-none-match", etag),)
            elif modified := prior.header("last-modified"):
                headers = (("if-modified-since", modified),)
        page: Page | None = None
        for retry in range(budget.config.retry_budget + 1):
            try:
                page = await self._attempt(route, url, budget, ledger, headers)
                if not conditional_cache_permitted(page.headers):
                    self._cache.pop(cache_key, None)
                # Refusals and no-store responses must not resurrect an older version.
                if (
                    store is not None
                    and key is not None
                    and (
                        page.status not in {200, 304}
                        or not conditional_cache_permitted(page.headers)
                        or page.challenge_use is not None
                    )
                ):
                    await store.invalidate(key)
                    original, prior, headers = None, None, ()
                    self._cache.pop(cache_key, None)
                throttled = (
                    budget.config.cadence is not None
                    and page.status in budget.config.cadence.throttle_statuses
                )
                if (
                    not throttled
                    and page_barrier(page) == RefusalCode.CHALLENGE_NOT_SOLVED
                    and budget.config.challenges is not None
                ):
                    await self._clear_challenge(route, page, budget, ledger)
                    # Refetch original bytes through ordinary DNS/TLS/robots guards.
                    page = await self._attempt(route, url, budget, ledger)
                if not throttled and (barrier := page_barrier(page)):
                    raise GhimeraRefused(barrier)
                if page.status is None:
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                if page.status < 500 and not throttled:
                    break
            except GhimeraRefused as exc:
                if exc.code != RefusalCode.FETCH_FAILED:
                    raise
                page = None
            if retry == budget.config.retry_budget:
                if (
                    page is not None
                    and page.status is not None
                    and budget.config.cadence is not None
                    and page.status in budget.config.cadence.throttle_statuses
                ):
                    # A throttle cannot be bypassed through an expensive route.
                    raise HttpStatusRefused(page.status)
                raise GhimeraRefused(RefusalCode.FETCH_FAILED)
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="fallback",
                    url=url,
                    route=route.name,
                    reason="transient_retry",
                )
            )
            delay = policy.retry_backoff_seconds * (2**retry)
            delay += random.uniform(0, policy.retry_jitter_seconds)
            async with asyncio.timeout(budget.remaining_seconds):
                await asyncio.sleep(delay)
        if page is None:
            raise GhimeraRefused(RefusalCode.FETCH_FAILED)
        if page.status is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if not scope.permits(page.final_url):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        if page.status == 304:
            if prior is None:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            use = original.reuse(store.policy, store.now()) if original and store else None
            if use is not None and store is not None:
                use.validate_policy(store.policy, url, use.source_sha256)
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="policy",
                        url=url,
                        route=route.name,
                        reason="source_refresh_revalidated",
                        source_refresh=use,
                    )
                )
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
            return Page.model_validate(
                prior.model_copy(
                    update={
                        "url": url,
                        "revalidated": True,
                        "source_refresh": use,
                        "transport": page.transport,
                        "source_session": page.source_session,
                    }
                ).model_dump()
            )
        if not robots and page.status >= 300 and page.status not in REDIRECT_STATUSES:
            raise HttpStatusRefused(page.status)
        if (
            not robots
            and page.status == 200
            and conditional_cache_permitted(page.headers)
            and page.challenge_use is None
        ):
            if store is not None and key is not None:
                await store.capture(key, page)
            self._cache[cache_key] = page
            self._cache.move_to_end(cache_key)
            while len(self._cache) > policy.conditional_cache_entries:
                self._cache.popitem(last=False)
        return page

    async def _clear_challenge(
        self, route: FetchRoute, page: Page, budget: RunBudget, ledger: Ledger
    ) -> None:
        policy = budget.config.challenges
        if policy is None:
            raise GhimeraRefused(RefusalCode.CHALLENGE_NOT_SOLVED)
        budget.reserve_challenge()
        allowance = budget.reserve_bytes(policy.max_response_bytes)
        evidence, read, code = None, 0, None
        cancelled = False
        started = budget.clock()
        try:
            budget.reserve_fetch()  # The browser gateway is not free hidden work.
            try:
                async with asyncio.timeout(budget.remaining_seconds):
                    if self._politeness is None:
                        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                    async with self._politeness.slot(page.final_url):
                        evidence, read = await route.clear_challenge(
                            page, timeout_seconds=budget.remaining_seconds, max_bytes=allowance
                        )
            except (GhimeraRefused, TimeoutError) as exc:
                code = RefusalCode.CHALLENGE_NOT_SOLVED
                read = exc.bytes_read if isinstance(exc, ChallengeFailure) else 0
            except asyncio.CancelledError as exc:
                cancelled = True
                code = RefusalCode.CHALLENGE_NOT_SOLVED
                read = exc.bytes_read if isinstance(exc, ChallengeCancelled) else 0
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="challenge",
                    url=page.final_url,
                    route=policy.provider,
                    bytes_read=read,
                    challenge=evidence,
                    refusal=code,
                    reason="clearance_acquired_not_content_verified"
                    if evidence
                    else "challenge_failed",
                    latency_seconds=max(0.0, budget.clock() - started),
                )
            )
            budget.record_bytes(read)
            if cancelled:
                raise asyncio.CancelledError
            if code is not None:
                raise GhimeraRefused(code)
        finally:
            budget.release_bytes(allowance)
