"""Explicit same-target DOM capture with a caller-supplied human assistance port.

The caller owns browser egress, credentials and lifecycle. This adapter never
discovers profiles, automates credentials/challenges, copies cookies, reports
unobserved HTTP response bytes or closes the caller's browser.
"""

import asyncio
import base64
import hashlib
import math
import time
import uuid
from abc import ABC, abstractmethod
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Literal, Protocol, final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ghimera.http import html_barrier
from ghimera.human_browser_errors import HumanCaptureCancelled, HumanCaptureFailure
from ghimera.human_browser_types import (
    AssistanceDecision,
    AssistanceObservation,
    BrowserAssistanceRequest,
    BrowserCapture,
    BrowserDownloadAction,
    BrowserDownloadCapture,
    BrowserDownloadEvidence,
    CaptureScope,
    HumanAssistant,
    HumanBrowserConfig,
    HumanBrowserEvidence,
)
from ghimera.refusals import GhimeraRefused, RefusalCode

if TYPE_CHECKING:
    from patchright.async_api import Browser, Download, Page, Playwright


class TargetInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    target_id: str = Field(alias="targetId", min_length=1)
    type: str
    url: str


class TargetReply(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    target_info: TargetInfo = Field(alias="targetInfo")


class DomReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str
    content_type: str
    dom_base64: str
    complete: bool = Field(strict=True)


class BrowserIdentity(Protocol):
    @property
    def version(self) -> str: ...


class BrowserVersionReply(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    product: str = Field(min_length=1)


class ObservedBrowser(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str


class CaptureWork:
    """Mutable observations belong to one capture, never to the browser/session."""

    def __init__(self, max_bytes: int, scope: CaptureScope | None, deadline: float) -> None:
        self.bytes_read = 0
        self.max_bytes = max_bytes
        self.scope = scope
        self.deadline = deadline
        self.assistance: list[AssistanceObservation] = []


class HumanBrowserSession(ABC):
    """Shared capture ordering, scope, budgets, assistance and cancellation."""

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if "capture" in cls.__dict__ or "validate_config" in cls.__dict__:
            raise TypeError("browser session ordering/config validation is final")

    def __init__(self, config: HumanBrowserConfig, *, assistant: HumanAssistant | None) -> None:
        # Revalidate: Pydantic model_copy can otherwise bypass the policy boundary.
        config = HumanBrowserConfig.model_validate(config.model_dump())
        if config.declared_route == "tor":
            # CDP attachment alone cannot prove Tor routing or prevent a direct fallback.
            raise GhimeraRefused(RefusalCode.TOR_UNAVAILABLE)
        if config.assistance_reasons and assistant is None:
            raise GhimeraRefused(RefusalCode.SOURCE_SESSION_UNAVAILABLE)
        try:
            if version("patchright") != config.driver_version:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        except PackageNotFoundError:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        self.config = config
        self._assistant = assistant
        # One target cannot be navigated/captured by two operations simultaneously.
        self._target_lock = asyncio.Lock()

    @final
    def validate_config(self, config: HumanBrowserConfig) -> None:
        if HumanBrowserConfig.model_validate(config.model_dump()) != self.config:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    @final
    async def capture(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
        timeout_seconds: float | None = None,
        scope: CaptureScope | None = None,
    ) -> BrowserCapture | BrowserDownloadCapture:
        if not self.config.permits(url) or (scope is not None and not scope.permits(url)):
            raise HumanCaptureFailure(RefusalCode.OUT_OF_SCOPE, 0, ())
        maximum = self.config.max_dom_bytes * (self.config.max_assistance_attempts + 1)
        if self.config.downloads is not None:
            maximum += self.config.downloads.max_file_bytes + 1
        if max_bytes is not None:
            if type(max_bytes) is not int or max_bytes <= 0:
                raise HumanCaptureFailure(RefusalCode.BUDGET_EXHAUSTED, 0, ())
            maximum = min(maximum, max_bytes)
        timeout = self.config.timeout_seconds
        if timeout_seconds is not None:
            if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
                raise HumanCaptureFailure(RefusalCode.BUDGET_EXHAUSTED, 0, ())
            timeout = min(timeout, timeout_seconds)
        work = CaptureWork(maximum, scope, asyncio.get_running_loop().time() + timeout)
        try:
            async with asyncio.timeout(timeout), self._target_lock:
                return await self._capture(url, work)
        except asyncio.CancelledError:
            raise HumanCaptureCancelled(work.bytes_read, tuple(work.assistance)) from None
        except GhimeraRefused as exc:
            raise HumanCaptureFailure(exc.code, work.bytes_read, tuple(work.assistance)) from None
        except TimeoutError:
            raise HumanCaptureFailure(
                RefusalCode.FETCH_FAILED, work.bytes_read, tuple(work.assistance)
            ) from None

    @abstractmethod
    async def _capture(
        self, url: str, work: CaptureWork
    ) -> BrowserCapture | BrowserDownloadCapture: ...

    async def _capture_cdp(
        self, url: str, work: CaptureWork
    ) -> BrowserCapture | BrowserDownloadCapture:
        from patchright.async_api import Error, async_playwright

        driver: Playwright | None = None
        try:
            if self.config.adapter != "patchright_cdp" or self.config.control_endpoint is None:
                raise GhimeraRefused(RefusalCode.SOURCE_SESSION_UNAVAILABLE)
            driver = await async_playwright().start()
            browser = await driver.chromium.connect_over_cdp(
                self.config.control_endpoint,
                no_defaults=True,
                timeout=self.config.timeout_seconds * 1000,
            )
            page = await self._select(browser)
            return await self._capture_page(browser, page, url, work)
        except (Error, ValidationError, ValueError):
            # Vendor errors may contain source/control URLs and secret headers.
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise asyncio.CancelledError from None
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        finally:
            if driver is not None:
                # Stop this transport only, never browser.close/context.close.
                await self._detach(driver)

    async def _capture_page(
        self, browser: BrowserIdentity, page: "Page", url: str, work: CaptureWork
    ) -> BrowserCapture | BrowserDownloadCapture:
        action = (
            next((item for item in self.config.downloads.actions if item.source_url == url), None)
            if self.config.downloads
            else None
        )
        if action is not None:
            return await self._download(browser, page, url, action, work)
        await page.goto(
            url, wait_until="domcontentloaded", timeout=self.config.timeout_seconds * 1000
        )
        capture_id = uuid.uuid4().hex
        while True:
            dom, final_url, content_type = await self._dom(page, work)
            barrier = html_barrier(content_type, dom)
            if barrier is None:
                result = BrowserCapture(
                    dom=dom,
                    evidence=HumanBrowserEvidence(
                        schema="ghimera.human-browser-evidence/1",
                        acquisition="browser_dom",
                        capture_id=capture_id,
                        request_url=url,
                        final_url=final_url,
                        session_id=self.config.session_id,
                        target_id=self.config.target_id,
                        policy_digest=self.config.content_digest(),
                        adapter_revision=self.config.adapter_revision,
                        driver_version=self.config.driver_version,
                        browser_version=browser.version,
                        lifecycle=self.config.lifecycle,
                        network_boundary=self.config.network_boundary,
                        declared_route="direct",
                        route_verification="operator_declaration_only",
                        browser_subresource_bytes=None,
                        browser_subresource_requests=None,
                        content_type=content_type,
                        dom_sha256=hashlib.sha256(dom).hexdigest(),
                        dom_bytes=len(dom),
                        collector_dom_bytes_read=work.bytes_read,
                        assistance=tuple(work.assistance),
                    ),
                )
                result.validate_policy(self.config)
                return result
            await self._assist(url, final_url, capture_id, barrier, dom, work)

    async def _download(
        self,
        browser: BrowserIdentity,
        page: "Page",
        url: str,
        action: BrowserDownloadAction,
        work: CaptureWork,
    ) -> BrowserDownloadCapture:
        from patchright.async_api import Error

        from ghimera.browser_download_stream import observe_document_media, read_download

        policy = self.config.downloads
        if policy is None or (
            work.scope is not None and not work.scope.permits(action.navigation_url)
        ):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        capture_id = uuid.uuid4().hex
        initiator_url: str | None = None
        if action.selector is not None:
            await page.goto(action.navigation_url, wait_until="domcontentloaded")
            while True:
                dom, final_url, content_type = await self._dom(page, work)
                if (barrier := html_barrier(content_type, dom)) is None:
                    initiator_url = final_url
                    break
                await self._assist(url, final_url, capture_id, barrier, dom, work)
        download: Download | None = None
        owned_downloads: list[Download] = []

        def observed(item: "Download") -> None:
            if item.page == page and item.url == action.source_url:
                owned_downloads.append(item)

        page.on("download", observed)
        dom_spend = work.bytes_read
        try:
            if work.max_bytes - work.bytes_read <= 1:
                raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            # Register before the action. A refused/cancelled capture is never
            # retried automatically through another session or by another click.
            async with page.expect_download(
                predicate=lambda item: item.url == action.source_url,
                timeout=self.config.timeout_seconds * 1000,
            ) as event:
                if action.selector is not None:
                    if not self.config.permits(page.url) or (
                        work.scope is not None and not work.scope.permits(page.url)
                    ):
                        raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
                    await page.locator(action.selector).click(
                        timeout=self.config.timeout_seconds * 1000
                    )
                else:
                    try:
                        await page.goto(
                            url, wait_until="commit", timeout=self.config.timeout_seconds * 1000
                        )
                    except Error as exc:
                        # Chromium reports an attachment navigation as aborted.
                        # The separately awaited event, URL and file prove capture.
                        if "ERR_ABORTED" not in str(exc) and "Download is starting" not in str(exc):
                            raise
            download = await event.value
            if (
                download.page != page
                or not self.config.permits(download.url)
                or (work.scope is not None and not work.scope.permits(download.url))
            ):
                raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
            body = await read_download(
                download,
                max_file_bytes=policy.max_file_bytes,
                chunk_bytes=policy.read_chunk_bytes,
                spend=work,
                cleanup_timeout_seconds=self.config.cleanup_timeout_seconds,
            )
            evidence = BrowserDownloadEvidence(
                schema="ghimera.browser-download-evidence/1",
                acquisition="browser_download",
                capture_id=capture_id,
                request_url=url,
                final_url=download.url,
                navigation_url=action.navigation_url,
                initiator_url=initiator_url,
                session_id=self.config.session_id,
                target_id=self.config.target_id,
                policy_digest=self.config.content_digest(),
                adapter_revision=self.config.adapter_revision,
                download_adapter_revision=policy.adapter_revision,
                driver_version=self.config.driver_version,
                browser_version=browser.version,
                lifecycle=self.config.lifecycle,
                network_boundary=self.config.network_boundary,
                declared_route="direct",
                route_verification="operator_declaration_only",
                browser_subresource_bytes=None,
                browser_subresource_requests=None,
                browser_download_bytes=None,
                content_type=action.content_type,
                media_observation=observe_document_media(body, action.content_type),
                file_sha256=hashlib.sha256(body).hexdigest(),
                file_bytes=len(body),
                collector_file_bytes_read=work.bytes_read - dom_spend,
                collector_dom_bytes_read=dom_spend,
                assistance=tuple(work.assistance),
            )
            result = BrowserDownloadCapture(body=body, evidence=evidence)
            result.validate_policy(self.config)
            return result
        finally:
            page.remove_listener("download", observed)
            if download is None and owned_downloads:
                download = owned_downloads[0]
            if download is not None:
                # Only this operation's artifact, never the borrowed context or
                # any unrelated downloads. Bound cleanup also after cancellation.
                task = asyncio.create_task(self._discard_download(download))
                try:
                    async with asyncio.timeout(self.config.cleanup_timeout_seconds):
                        await asyncio.shield(task)
                except (TimeoutError, asyncio.CancelledError):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise

    async def _discard_download(self, download: "Download") -> None:
        await download.cancel()
        await download.delete()

    async def _select(self, browser: "Browser") -> "Page":
        cdp = await browser.new_browser_cdp_session()
        try:
            reply = TargetReply.model_validate(
                await cdp.send("Target.getTargetInfo", {"targetId": self.config.target_id})
            )
        finally:
            await cdp.detach()
        target = reply.target_info
        if target.target_id != self.config.target_id or target.type != "page":
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if target.url != "about:blank" and not self.config.permits(target.url):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        for context in browser.contexts:
            for page in context.pages:
                # Metadata only; never read another tab's content/cookies/storage.
                if page.url != target.url:
                    continue
                session = await context.new_cdp_session(page)
                try:
                    identity = TargetReply.model_validate(
                        await session.send("Target.getTargetInfo")
                    )
                finally:
                    await session.detach()
                if identity.target_info.target_id == self.config.target_id:
                    return page
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    async def _dom(
        self, page: "Page", work: CaptureWork
    ) -> tuple[bytes, str, Literal["text/html", "application/xhtml+xml"]]:
        if not self.config.permits(page.url) or (
            work.scope is not None and not work.scope.permits(page.url)
        ):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        allowance = min(self.config.max_dom_bytes, work.max_bytes - work.bytes_read)
        if allowance <= 0:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        reply = DomReply.model_validate(
            await page.evaluate(
                """cap => {
                    const bytes = new TextEncoder().encode(document.documentElement.outerHTML);
                    const kept = bytes.subarray(0, cap);
                    let binary = '';
                    for (let i=0; i<kept.length; i+=4096) {
                        binary += String.fromCharCode(...kept.subarray(i, i+4096));
                    }
                    return {url: document.URL, content_type: document.contentType,
                        dom_base64: btoa(binary), complete: bytes.length <= cap};
                }""",
                allowance,
                isolated_context=True,
            )
        )
        dom = base64.b64decode(reply.dom_base64, validate=True)
        work.bytes_read += len(dom)
        if (
            not self.config.permits(reply.url)
            or page.url != reply.url
            or (work.scope is not None and not work.scope.permits(reply.url))
        ):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        if len(dom) > allowance:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if not reply.complete:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        if reply.content_type == "text/html":
            content_type: Literal["text/html", "application/xhtml+xml"] = "text/html"
        elif reply.content_type == "application/xhtml+xml":
            content_type = "application/xhtml+xml"
        else:
            raise GhimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
        if not dom:
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
        dom.decode("utf-8", errors="strict")
        return dom, reply.url, content_type

    async def _assist(
        self,
        url: str,
        final_url: str,
        capture_id: str,
        barrier: RefusalCode,
        dom: bytes,
        work: CaptureWork,
    ) -> None:
        reason: Literal["challenge_not_solved", "login_wall", "paywall"]
        if barrier == RefusalCode.CHALLENGE_NOT_SOLVED:
            reason = "challenge_not_solved"
        elif barrier == RefusalCode.LOGIN_WALL:
            reason = "login_wall"
        elif barrier == RefusalCode.PAYWALL:
            reason = "paywall"
        else:
            raise GhimeraRefused(barrier)
        timeout = min(
            self.config.assistance_timeout_seconds,
            work.deadline - asyncio.get_running_loop().time(),
        )
        if timeout <= 0:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        if (
            reason not in self.config.assistance_reasons
            or len(work.assistance) >= self.config.max_assistance_attempts
            or self._assistant is None
        ):
            raise GhimeraRefused(barrier)
        request = BrowserAssistanceRequest(
            schema="ghimera.browser-assistance/1",
            capture_id=capture_id,
            attempt=len(work.assistance) + 1,
            request_url=url,
            final_url=final_url,
            session_id=self.config.session_id,
            target_id=self.config.target_id,
            policy_digest=self.config.content_digest(),
            reason=reason,
            observed_dom_sha256=hashlib.sha256(dom).hexdigest(),
            observed_dom_bytes=len(dom),
            deadline_unix_seconds=time.time() + timeout,
        )
        try:
            async with asyncio.timeout(timeout):
                decision = await self._assistant.assist(request)
                decision = AssistanceDecision.model_validate(decision.model_dump())
        except TimeoutError:
            work.assistance.append(AssistanceObservation(request=request, action="timeout"))
            raise GhimeraRefused(barrier) from None
        except asyncio.CancelledError:
            work.assistance.append(AssistanceObservation(request=request, action="cancelled"))
            raise
        except Exception:
            work.assistance.append(
                AssistanceObservation(request=request, action="assistance_failed")
            )
            raise GhimeraRefused(RefusalCode.SOURCE_SESSION_UNAVAILABLE) from None
        if decision.request_digest != request.content_digest():
            work.assistance.append(
                AssistanceObservation(request=request, action="invalid_decision")
            )
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        work.assistance.append(AssistanceObservation(request=request, action=decision.action))
        if decision.action != "resume":
            raise GhimeraRefused(barrier)

    async def _detach(self, driver: "Playwright") -> None:
        from patchright.async_api import Error

        # A separate task gets a bounded chance to detach even after cancellation.
        task = asyncio.create_task(driver.stop())
        try:
            async with asyncio.timeout(self.config.cleanup_timeout_seconds):
                await asyncio.shield(task)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except Error:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None


class ChromiumHumanSession(HumanBrowserSession):
    """Attach to the explicit CDP endpoint for unchanged DOM capture."""

    def __init__(self, config: HumanBrowserConfig, *, assistant: HumanAssistant | None) -> None:
        super().__init__(config, assistant=assistant)
        if self.config.adapter != "patchright_cdp":
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    async def _capture(
        self, url: str, work: CaptureWork
    ) -> BrowserCapture | BrowserDownloadCapture:
        return await self._capture_cdp(url, work)


class BoundPageHumanSession(HumanBrowserSession):
    """Use the caller's actual Page/driver connection, without a second attach.

    The caller initializes download behavior, supplies the selected page, and
    owns its lifecycle. No global browser settings or cookies are changed.
    Scope, byte limits, assistance and capture serialization share the existing
    session implementation. The exact target is rechecked before each action.
    """

    def __init__(
        self, config: HumanBrowserConfig, *, page: "Page", assistant: HumanAssistant | None
    ) -> None:
        super().__init__(config, assistant=assistant)
        if self.config.adapter != "patchright_page":
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self._page = page

    async def _capture(
        self, url: str, work: CaptureWork
    ) -> BrowserCapture | BrowserDownloadCapture:
        from patchright.async_api import Error

        try:
            page = self._page
            if page.is_closed() or (
                page.url != "about:blank" and not self.config.permits(page.url)
            ):
                raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
            session = await page.context.new_cdp_session(page)
            try:
                target = TargetReply.model_validate(await session.send("Target.getTargetInfo"))
                revision = BrowserVersionReply.model_validate(
                    await session.send("Browser.getVersion")
                )
            finally:
                await session.detach()
            if (
                target.target_info.target_id != self.config.target_id
                or target.target_info.type != "page"
            ):
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            return await self._capture_page(
                ObservedBrowser(version=revision.product), page, url, work
            )
        except (Error, ValidationError, ValueError):
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise asyncio.CancelledError from None
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
