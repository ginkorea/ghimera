"""Owned CDP request-stage interception over a borrowed, exact browser page.

This component admits one main-frame redirect chain before contact. It does
not meter/control the caller's browser subresources, transfer credentials or
close the page. The application must delegate exclusive navigation during the
operation; competing interception owners are not discoverable through CDP.
"""

import asyncio
from importlib.metadata import PackageNotFoundError, version
from types import TracebackType
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ghimera.browser_navigation_types import (
    BrowserNavigationAdmission,
    BrowserNavigationConfig,
    BrowserNavigationEvidence,
    BrowserNavigationHop,
)
from ghimera.human_browser_types import CaptureScope, HumanBrowserConfig, Identifier
from ghimera.refusals import GhimeraRefused, RefusalCode

if TYPE_CHECKING:
    from patchright.async_api import CDPSession, Page


class _Frame(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: Identifier


class _FrameTree(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    frame: _Frame


class _FrameReply(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    frame_tree: _FrameTree = Field(alias="frameTree")


class _Request(BaseModel):
    # The vendor carries secret headers here. Never retain/log its original
    # payload; narrow immediately and ignore all but the source URL.
    model_config = ConfigDict(extra="ignore", frozen=True)
    url: str


class _Paused(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    request_id: Identifier = Field(alias="requestId")
    request: _Request
    frame_id: Identifier = Field(alias="frameId")
    resource_type: Literal["Document"] = Field(alias="resourceType")
    network_id: Identifier | None = Field(default=None, alias="networkId")
    redirected_from: Identifier | None = Field(default=None, alias="redirectedRequestId")


class BrowserNavigationGuard:
    """Single-use async lifecycle. Admission and wire operations are serialized.

    Enter before goto/click, keep the context through the observed terminal
    navigation/download event, check/refuse vendor errors through check(), then
    bind the actual final URL with evidence(). Context exit cancels admission,
    fails paused requests and releases only this operation's CDP session.
    """

    def __init__(
        self,
        config: BrowserNavigationConfig,
        *,
        browser_policy: HumanBrowserConfig,
        page: "Page",
        request_url: str,
        scope: CaptureScope,
        admission: BrowserNavigationAdmission,
    ) -> None:
        self.config = BrowserNavigationConfig.model_validate(config.model_dump())
        self._browser_policy = HumanBrowserConfig.model_validate(browser_policy.model_dump())
        if self._browser_policy.declared_route == "tor":
            raise GhimeraRefused(RefusalCode.TOR_UNAVAILABLE)
        try:
            if version("patchright") != self._browser_policy.driver_version:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        except PackageNotFoundError:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        if self._browser_policy.adapter != "patchright_page":
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if not self._browser_policy.permits(request_url) or not scope.permits(request_url):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        self._page, self._request_url = page, request_url
        self._scope, self._admission = scope, admission
        self._session: CDPSession | None = None
        self._frame_id: str | None = None
        self._hops: list[BrowserNavigationHop] = []
        self._pending: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()
        self._failure: RefusalCode | None = None
        self._entered = False
        self._closing = False

    async def __aenter__(self) -> Self:
        from patchright.async_api import Error

        from ghimera.human_browser import TargetReply

        if self._entered:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self._entered = True
        try:
            if self._page.is_closed():
                raise GhimeraRefused(RefusalCode.SOURCE_SESSION_UNAVAILABLE)
            session = await self._page.context.new_cdp_session(self._page)
            self._session = session
            target = TargetReply.model_validate(await session.send("Target.getTargetInfo"))
            if (
                target.target_info.target_id != self._browser_policy.target_id
                or target.target_info.type != "page"
            ):
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            if target.target_info.url != "about:blank" and not self._browser_policy.permits(
                target.target_info.url
            ):
                raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
            reply = _FrameReply.model_validate(await session.send("Page.getFrameTree"))
            self._frame_id = reply.frame_tree.frame.id
            session.on("Fetch.requestPaused", self._paused)
            await session.send(
                "Fetch.enable",
                {"patterns": [{"resourceType": "Document", "requestStage": "Request"}]},
            )
            return self
        except BaseException as exc:
            await self._close()
            if isinstance(exc, (Error, ValidationError)):
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
            raise

    def _paused(self, payload: object) -> None:
        # A regular callback, not an unowned driver's async event coroutine.
        try:
            event = _Paused.model_validate(payload)
        except ValidationError:
            self._failure = self._failure or RefusalCode.ADAPTER_CONTRACT
            task = asyncio.create_task(self._stop_navigation())
        else:
            self._pending.add(event.request_id)
            task = asyncio.create_task(self._resolve(event))
        self._tasks.add(task)
        task.add_done_callback(self._completed)

    def _completed(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self._failure = self._failure or RefusalCode.ADAPTER_CONTRACT

    async def _stop_navigation(self) -> None:
        from patchright.async_api import Error

        if self._session is not None:
            try:
                await self._session.send("Page.stopLoading")
            except Error:
                self._failure = self._failure or RefusalCode.ADAPTER_CONTRACT

    async def _resolve(self, event: _Paused) -> None:
        from patchright.async_api import Error

        async with self._lock:
            session = self._session
            if session is None:
                return
            try:
                if self._closing or self._failure is not None:
                    await self._fail(event.request_id)
                    return
                if event.frame_id != self._frame_id:
                    # Subframes remain in the caller's explicitly unmetered
                    # browser boundary, not in the main-frame evidence chain.
                    await session.send("Fetch.continueRequest", {"requestId": event.request_id})
                    self._pending.discard(event.request_id)
                    return
                if event.network_id is None:
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                hop = BrowserNavigationHop(
                    url=event.request.url,
                    request_id=event.request_id,
                    redirected_from=event.redirected_from,
                    frame_id=event.frame_id,
                    network_id=event.network_id,
                )
                previous = self._hops[-1] if self._hops else None
                if previous is None:
                    if hop.url != self._request_url or hop.redirected_from is not None:
                        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                elif (
                    hop.redirected_from != previous.request_id
                    or hop.network_id != previous.network_id
                    or hop.request_id in {item.request_id for item in self._hops}
                    or hop.url in {item.url for item in self._hops}
                    or len(self._hops) > self.config.max_redirects
                ):
                    raise GhimeraRefused(RefusalCode.FETCH_FAILED)
                if not self._browser_policy.permits(hop.url) or not self._scope.permits(hop.url):
                    raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
                async with asyncio.timeout(self.config.admission_timeout_seconds):
                    await self._admission.admit(hop.url)
                # Cancellation/exit cannot turn a late admission into contact.
                if self._closing:
                    await self._fail(event.request_id)
                    return
                if not self._browser_policy.permits(hop.url) or not self._scope.permits(hop.url):
                    raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
                await session.send("Fetch.continueRequest", {"requestId": event.request_id})
                self._pending.discard(event.request_id)
                self._hops.append(hop)
            except GhimeraRefused as exc:
                self._failure = self._failure or exc.code
                await self._fail(event.request_id)
            except TimeoutError:
                self._failure = self._failure or RefusalCode.FETCH_FAILED
                await self._fail(event.request_id)
            except (Error, ValidationError):
                self._failure = self._failure or RefusalCode.ADAPTER_CONTRACT
                await self._stop_navigation()
            except asyncio.CancelledError:
                # Exit owns failure of requests that were not continued.
                raise
            except Exception:
                # Admission implementations must not leak provider exception
                # strings (possibly credentials) into source evidence.
                self._failure = self._failure or RefusalCode.ADAPTER_CONTRACT
                await self._fail(event.request_id)

    async def _fail(self, request_id: str) -> None:
        from patchright.async_api import Error

        if self._session is None:
            return
        try:
            await self._session.send(
                "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}
            )
            self._pending.discard(request_id)
        except Error:
            self._failure = self._failure or RefusalCode.ADAPTER_CONTRACT
            await self._stop_navigation()

    def check(self) -> None:
        if self._failure is not None:
            raise GhimeraRefused(self._failure)

    def evidence(self, final_url: str) -> BrowserNavigationEvidence:
        self.check()
        if not self._entered or self._closing or self._pending or not self._hops:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        try:
            evidence = BrowserNavigationEvidence(
                schema="ghimera.browser-navigation-evidence/1",
                adapter_revision=self.config.adapter_revision,
                policy_digest=self.config.content_digest(),
                browser_policy_digest=self._browser_policy.content_digest(),
                target_id=self._browser_policy.target_id,
                request_url=self._request_url,
                final_url=final_url,
                hops=tuple(self._hops),
                observation="admitted_before_main_frame_request",
                browser_subresource_requests=None,
            )
            evidence.validate_policy(self.config, self._browser_policy)
            return evidence
        except ValueError:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._close()
        if exc_type is None:
            self.check()

    async def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._session is None:
            return
        # Shield only this bounded cleanup; caller cancellation is re-raised.
        cleanup = asyncio.create_task(self._settle())
        try:
            async with asyncio.timeout(self.config.cleanup_timeout_seconds):
                await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            try:
                async with asyncio.timeout(self.config.cleanup_timeout_seconds):
                    await asyncio.shield(cleanup)
            finally:
                if not cleanup.done():
                    cleanup.cancel()
                await asyncio.gather(cleanup, return_exceptions=True)
            raise
        except TimeoutError:
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None

    async def _settle(self) -> None:
        from patchright.async_api import Error

        session = self._session
        if session is None:
            return
        try:
            session.remove_listener("Fetch.requestPaused", self._paused)
            tasks = tuple(self._tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for request_id in tuple(self._pending):
                await self._fail(request_id)
            if self._pending:
                await self._stop_navigation()
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            await session.send("Fetch.disable")
        except Error:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        finally:
            try:
                await session.detach()
            except Error:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
            finally:
                self._session = None
