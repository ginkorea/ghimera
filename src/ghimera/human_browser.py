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
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Literal, Protocol, final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ghimera.browser_download_stream import DownloadSpend
from ghimera.browser_navigation import BrowserNavigationGuard
from ghimera.browser_navigation_types import BrowserNavigationEvidence
from ghimera.browser_operation_types import BrowserOperation
from ghimera.browser_pagination_types import BrowserPaginationEvidence
from ghimera.browser_tor import BrowserCommandLine, BrowserTorEvidence, TorProbeObservation
from ghimera.http import html_barrier
from ghimera.human_browser_errors import HumanCaptureCancelled, HumanCaptureFailure
from ghimera.human_browser_types import (
    AssistanceDecision,
    AssistanceObservation,
    BrowserAcquisition,
    BrowserAssistanceRequest,
    BrowserCapture,
    BrowserDownloadAction,
    BrowserDownloadCapture,
    BrowserDownloadEvidence,
    BrowserResponseCapture,
    BrowserResponseEvidence,
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

    def __init__(
        self,
        max_bytes: int,
        scope: CaptureScope | None,
        deadline: float,
        operation: BrowserOperation | None = None,
    ) -> None:
        self.bytes_read = 0
        self.capture_id = uuid.uuid4().hex
        self.max_bytes = max_bytes
        self.scope = scope
        self.deadline = deadline
        self.assistance: list[AssistanceObservation] = []
        self.operation = operation
        self.tor: BrowserTorEvidence | None = None

    @asynccontextmanager
    async def reading(self, maximum: int) -> AsyncIterator[DownloadSpend]:
        if self.operation is None:
            yield self
            return
        maximum = min(maximum, self.max_bytes - self.bytes_read)
        async with self.operation.output.reading(maximum) as spend:
            try:
                yield spend
            finally:
                self.bytes_read += spend.bytes_read


class HumanBrowserSession(ABC):
    """Shared capture ordering, scope, budgets, assistance and cancellation."""

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if "capture" in cls.__dict__ or "validate_config" in cls.__dict__:
            raise TypeError("browser session ordering/config validation is final")

    def __init__(self, config: HumanBrowserConfig, *, assistant: HumanAssistant | None) -> None:
        # Revalidate: Pydantic model_copy can otherwise bypass the policy boundary.
        config = HumanBrowserConfig.model_validate(config.model_dump())
        if config.declared_route == "tor" and config.tor_verification is None:
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
        operation: BrowserOperation | None = None,
    ) -> BrowserAcquisition:
        if (self.config.navigation is not None) != (operation is not None) or (
            self.config.navigation is not None and scope is None
        ):
            raise HumanCaptureFailure(RefusalCode.ADAPTER_CONTRACT, 0, ())
        if not self.config.permits(url) or (scope is not None and not scope.permits(url)):
            raise HumanCaptureFailure(RefusalCode.OUT_OF_SCOPE, 0, ())
        maximum = self.config.max_dom_bytes * (self.config.max_assistance_attempts + 1)
        if self.config.downloads is not None:
            maximum += self.config.downloads.max_file_bytes + 1
        if any(action.source_url == url for action in self.config.pagination):
            maximum += self.config.max_dom_bytes
        if self.config.tor_verification is not None:
            maximum += self.config.tor_verification.max_probe_bytes
        if max_bytes is not None:
            if type(max_bytes) is not int or max_bytes <= 0:
                raise HumanCaptureFailure(RefusalCode.BUDGET_EXHAUSTED, 0, ())
            maximum = min(maximum, max_bytes)
        timeout = self.config.timeout_seconds
        if timeout_seconds is not None:
            if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
                raise HumanCaptureFailure(RefusalCode.BUDGET_EXHAUSTED, 0, ())
            timeout = min(timeout, timeout_seconds)
        work = CaptureWork(maximum, scope, asyncio.get_running_loop().time() + timeout, operation)
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
    async def _capture(self, url: str, work: CaptureWork) -> BrowserAcquisition: ...

    async def _capture_cdp(self, url: str, work: CaptureWork) -> BrowserAcquisition:
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
    ) -> BrowserAcquisition:
        if self.config.tor_verification is not None:
            work.tor = await self._verify_tor(page, work)
        action = (
            next((item for item in self.config.downloads.actions if item.source_url == url), None)
            if self.config.downloads
            else None
        )
        if action is not None:
            return await self._download(browser, page, url, action, work)
        if self.config.downloads is not None and self.config.downloads.navigation_content_types:
            return await self._download(browser, page, url, None, work)
        pagination = next((a for a in self.config.pagination if a.source_url == url), None)
        if pagination is not None:
            async with self._navigation(page, pagination.navigation_url, work) as landing_guard:
                await page.goto(pagination.navigation_url, wait_until="domcontentloaded")
                landing_dom, _, _, landing = await self._assisted_dom(
                    page, url, work, landing_guard, navigation_root=pagination.navigation_url
                )
                if landing is None:
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            evidence = BrowserPaginationEvidence(
                action=pagination,
                landing_navigation=landing,
                landing_dom=landing_dom,
                landing_dom_sha256=hashlib.sha256(landing_dom).hexdigest(),
            )
            async with self._navigation(page, url, work) as guard:
                await page.locator(pagination.selector).click(
                    timeout=self.config.timeout_seconds * 1000
                )
                await page.wait_for_url(url, wait_until="domcontentloaded")
                return await self._capture_dom(browser, page, url, work, guard, evidence)
        async with self._navigation(page, url, work) as guard:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=self.config.timeout_seconds * 1000
            )
            return await self._capture_dom(browser, page, url, work, guard)

    @asynccontextmanager
    async def _navigation(
        self, page: "Page", url: str, work: CaptureWork
    ) -> AsyncIterator[BrowserNavigationGuard | None]:
        from patchright.async_api import Error

        if self.config.navigation is None:
            yield None
            return
        if work.operation is None or work.scope is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        async with BrowserNavigationGuard(
            self.config.navigation,
            browser_policy=self.config,
            page=page,
            request_url=url,
            scope=work.scope,
            admission=work.operation.admission,
            tor=work.tor,
        ) as guard:
            try:
                yield guard
            except Error:
                guard.check()
                raise

    async def _capture_dom(
        self,
        browser: BrowserIdentity,
        page: "Page",
        url: str,
        work: CaptureWork,
        guard: BrowserNavigationGuard | None = None,
        pagination: BrowserPaginationEvidence | None = None,
    ) -> BrowserCapture:
        dom, final_url, content_type, navigation = await self._assisted_dom(
            page, url, work, guard, navigation_root=url
        )
        result = BrowserCapture(
            dom=dom,
            evidence=HumanBrowserEvidence(
                schema="ghimera.human-browser-evidence/1",
                acquisition="browser_dom",
                capture_id=work.capture_id,
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
                declared_route=self.config.declared_route,
                route_verification="native_proxy_and_tor_probe"
                if work.tor is not None
                else "operator_declaration_only",
                browser_subresource_bytes=None,
                browser_subresource_requests=None,
                content_type=content_type,
                dom_sha256=hashlib.sha256(dom).hexdigest(),
                dom_bytes=len(dom),
                collector_dom_bytes_read=work.bytes_read,
                assistance=tuple(work.assistance),
                navigation=navigation,
                pagination=pagination,
                tor=work.tor,
            ),
        )
        result.validate_policy(self.config)
        return result

    async def _verify_tor(self, page: "Page", work: CaptureWork) -> BrowserTorEvidence:
        policy = self.config.tor_verification
        if policy is None or work.scope is None or not work.scope.permits(policy.probe_url):
            raise GhimeraRefused(RefusalCode.TOR_UNAVAILABLE)
        session = await page.context.new_cdp_session(page)
        try:
            arguments = BrowserCommandLine.model_validate(
                await session.send("Browser.getBrowserCommandLine")
            )
            policy.admit_command_line(arguments.arguments)
            observed = TorProbeObservation(policy.probe_url)
            session.on("Network.responseReceived", observed.observe)
            await session.send("Network.enable")
            return await self._tor_probe(page, work, observed)
        finally:
            await session.detach()

    async def _tor_probe(
        self, page: "Page", work: CaptureWork, observed: TorProbeObservation
    ) -> BrowserTorEvidence:
        policy = self.config.tor_verification
        if policy is None:
            raise GhimeraRefused(RefusalCode.TOR_UNAVAILABLE)
        async with self._navigation(page, policy.probe_url, work) as guard:
            response = await page.goto(policy.probe_url, wait_until="domcontentloaded")
            if response is None or response.status != 200 or page.url != policy.probe_url:
                raise GhimeraRefused(RefusalCode.TOR_UNAVAILABLE)
            async with work.reading(policy.max_probe_bytes) as spend:
                maximum = min(policy.max_probe_bytes, spend.max_bytes - spend.bytes_read)
                reply = DomReply.model_validate(
                    await page.evaluate(
                        """cap => {
                        const bytes = new TextEncoder().encode(document.body.innerText);
                        const kept = bytes.subarray(0, cap);
                        let binary = '';
                        for (let i=0; i<kept.length; i+=4096) {
                            binary += String.fromCharCode(...kept.subarray(i,i+4096));
                        }
                        return {url: document.URL, content_type: document.contentType,
                            dom_base64: btoa(binary), complete: bytes.length <= cap};
                    }""",
                        maximum,
                        isolated_context=True,
                    )
                )
                body = base64.b64decode(reply.dom_base64, validate=True)
                spend.bytes_read += len(body)
                if not reply.complete or len(body) > maximum:
                    raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
                if reply.url != policy.probe_url or guard is None:
                    raise GhimeraRefused(RefusalCode.TOR_UNAVAILABLE)
                navigation = await guard.settled_evidence(page.url)
        if observed.response is None or observed.failed:
            raise GhimeraRefused(RefusalCode.TOR_UNAVAILABLE)
        return BrowserTorEvidence(
            policy=policy,
            target_id=self.config.target_id,
            probe_body=body,
            probe_sha256=hashlib.sha256(body).hexdigest(),
            native_proxy_flags_verified=True,
            navigation=navigation,
            network=observed.response,
        )

    async def _assisted_dom(
        self,
        page: "Page",
        request_url: str,
        work: CaptureWork,
        guard: BrowserNavigationGuard | None,
        *,
        navigation_root: str,
    ) -> tuple[
        bytes, str, Literal["text/html", "application/xhtml+xml"], BrowserNavigationEvidence | None
    ]:
        async with AsyncExitStack() as owned:
            while True:
                dom, final_url, content_type = await self._dom(page, work)
                navigation = await guard.settled_evidence(final_url) if guard is not None else None
                barrier = html_barrier(content_type, dom)
                if barrier is None:
                    return dom, final_url, content_type, navigation
                if guard is not None:
                    await guard.release_for_assistance()
                    if work.operation is None:
                        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                    await work.operation.admission.yield_to_human()
                await self._assist(
                    request_url, final_url, work.capture_id, barrier, dom, work, navigation
                )
                if guard is not None:
                    # The human owns challenge/login actions. Re-admit a fresh
                    # collector navigation afterward, never attest to unknown
                    # human/browser traffic with the previous native chain.
                    guard = await owned.enter_async_context(
                        self._navigation(page, navigation_root, work)
                    )
                    try:
                        await page.goto(navigation_root, wait_until="domcontentloaded")
                    except Exception:
                        if guard is not None:
                            guard.check()
                        raise

    async def _download(
        self,
        browser: BrowserIdentity,
        page: "Page",
        url: str,
        action: BrowserDownloadAction | None,
        work: CaptureWork,
    ) -> BrowserAcquisition:
        policy = self.config.downloads
        navigation_url = action.navigation_url if action is not None else url
        selector = action.selector if action is not None else None
        if policy is None or (work.scope is not None and not work.scope.permits(navigation_url)):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        if action is None and not policy.navigation_content_types:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        capture_id = work.capture_id
        initiator_url: str | None = None
        landing_navigation: BrowserNavigationEvidence | None = None
        if selector is not None:
            async with self._navigation(page, navigation_url, work) as landing:
                await page.goto(navigation_url, wait_until="domcontentloaded")
                _, initiator_url, _, landing_navigation = await self._assisted_dom(
                    page, url, work, landing, navigation_root=navigation_url
                )
        async with self._navigation(page, url, work) as guard:
            return await self._download_file(
                browser,
                page,
                url,
                action,
                work,
                guard,
                capture_id=capture_id,
                navigation_url=navigation_url,
                initiator_url=initiator_url,
                landing_navigation=landing_navigation,
            )

    async def _download_file(
        self,
        browser: BrowserIdentity,
        page: "Page",
        url: str,
        action: BrowserDownloadAction | None,
        work: CaptureWork,
        guard: BrowserNavigationGuard | None,
        *,
        capture_id: str,
        navigation_url: str,
        initiator_url: str | None,
        landing_navigation: BrowserNavigationEvidence | None,
    ) -> BrowserAcquisition:
        from patchright.async_api import Error

        from ghimera.browser_download_stream import admit_download_media, read_download

        policy = self.config.downloads
        if policy is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        selector = action.selector if action is not None else None
        download: Download | None = None
        owned_downloads: list[Download] = []
        ready: asyncio.Future[Download] = asyncio.get_running_loop().create_future()

        def observed(item: "Download") -> None:
            if item.page == page and (
                guard.matches(item.url) if guard is not None else item.url == url
            ):
                owned_downloads.append(item)
                if not ready.done():
                    ready.set_result(item)

        page.on("download", observed)
        dom_spend = work.bytes_read
        try:
            if work.max_bytes - work.bytes_read <= 1:
                raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            # Register before the action. A refused/cancelled capture is never
            # retried automatically through another session or by another click.
            if selector is not None:
                if not self.config.permits(page.url) or (
                    work.scope is not None and not work.scope.permits(page.url)
                ):
                    raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
                await page.locator(selector).click(timeout=self.config.timeout_seconds * 1000)
            else:
                attachment_navigation = False
                try:
                    response = await page.goto(
                        url,
                        wait_until="domcontentloaded" if action is None else "commit",
                        timeout=self.config.timeout_seconds * 1000,
                    )
                except Error as exc:
                    # An aborted navigation alone never proves a file. It still
                    # requires the separately observed exact-page/URL event.
                    if "ERR_ABORTED" not in str(exc) and "Download is starting" not in str(exc):
                        raise
                    attachment_navigation = True
                if action is None and not attachment_navigation and not ready.done():
                    inline = policy.inline
                    header = response.headers.get("content-type", "") if response else ""
                    if inline is not None and header.split(";", 1)[0].strip().lower() in (
                        inline.content_types
                    ):
                        if (
                            response is None
                            or response.status != 200
                            or (guard is None and response.url != url)
                        ):
                            raise GhimeraRefused(RefusalCode.FETCH_FAILED)
                        chain = (
                            await guard.settled_evidence(response.url)
                            if guard is not None
                            else None
                        )
                        return await self._inline_response(browser, page, url, header, work, chain)
                    # Ordinary HTML follows its unchanged same-session DOM path:
                    # no timeout waiting for a nonexistent download and no refetch.
                    return await self._capture_dom(browser, page, url, work, guard)
            download = await ready
            navigation = await guard.settled_evidence(download.url) if guard is not None else None
            if (
                download.page != page
                or not self.config.permits(download.url)
                or (work.scope is not None and not work.scope.permits(download.url))
            ):
                raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
            async with work.reading(policy.max_file_bytes + 1) as spend:
                body = await read_download(
                    download,
                    max_file_bytes=policy.max_file_bytes,
                    chunk_bytes=policy.read_chunk_bytes,
                    spend=spend,
                    cleanup_timeout_seconds=self.config.cleanup_timeout_seconds,
                )
            content_type, media_observation = admit_download_media(
                body,
                (action.content_type,) if action is not None else policy.navigation_content_types,
            )
            evidence = BrowserDownloadEvidence(
                schema="ghimera.browser-download-evidence/1",
                acquisition="browser_download",
                capture_id=capture_id,
                request_url=url,
                final_url=download.url,
                navigation_url=navigation_url,
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
                declared_route=self.config.declared_route,
                route_verification="native_proxy_and_tor_probe"
                if work.tor is not None
                else "operator_declaration_only",
                browser_subresource_bytes=None,
                browser_subresource_requests=None,
                browser_download_bytes=None,
                content_type=content_type,
                media_observation=media_observation,
                file_sha256=hashlib.sha256(body).hexdigest(),
                file_bytes=len(body),
                collector_file_bytes_read=work.bytes_read - dom_spend,
                collector_dom_bytes_read=dom_spend,
                assistance=tuple(work.assistance),
                navigation=navigation,
                landing_navigation=landing_navigation,
                tor=work.tor,
            )
            result = BrowserDownloadCapture(body=body, evidence=evidence)
            result.validate_policy(self.config)
            return result
        finally:
            page.remove_listener("download", observed)
            if not ready.done():
                ready.cancel()
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

    async def _inline_response(
        self,
        browser: BrowserIdentity,
        page: "Page",
        url: str,
        navigation_header: str,
        work: CaptureWork,
        navigation: BrowserNavigationEvidence | None = None,
    ) -> BrowserResponseCapture:
        from ghimera.browser_inline_stream import read_inline

        policy = self.config.downloads
        if policy is None or policy.inline is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        final_url = navigation.final_url if navigation is not None else url
        if (
            page.url != final_url
            or not self.config.permits(final_url)
            or (work.scope is not None and not work.scope.permits(final_url))
        ):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        if work.operation is not None:
            await work.operation.admission.admit_inline(final_url)
        async with work.reading(policy.max_file_bytes + 1) as spend:
            read = await read_inline(
                page,
                final_url,
                formats=policy.inline.content_types,
                max_file_bytes=policy.max_file_bytes,
                spend=spend,
                timeout_seconds=work.deadline - asyncio.get_running_loop().time(),
                cleanup_timeout_seconds=self.config.cleanup_timeout_seconds,
            )
        evidence = BrowserResponseEvidence(
            schema="ghimera.browser-response-evidence/1",
            acquisition="browser_response",
            capture_id=uuid.uuid4().hex,
            request_url=url,
            final_url=read.reply.url,
            document_url=read.reply.document_url,
            session_id=self.config.session_id,
            target_id=self.config.target_id,
            policy_digest=self.config.content_digest(),
            adapter_revision=self.config.adapter_revision,
            response_adapter_revision=policy.inline.adapter_revision,
            driver_version=self.config.driver_version,
            browser_version=browser.version,
            lifecycle=self.config.lifecycle,
            network_boundary=self.config.network_boundary,
            declared_route=self.config.declared_route,
            route_verification="native_proxy_and_tor_probe"
            if work.tor is not None
            else "operator_declaration_only",
            browser_subresource_bytes=None,
            browser_subresource_requests=None,
            browser_fetch_bytes=None,
            navigation_status=200,
            navigation_content_type=navigation_header,
            response_status=read.reply.response_status,
            response_content_type=read.reply.response_content_type,
            content_type=read.content_type,
            media_observation=read.media_observation,
            file_sha256=hashlib.sha256(read.body).hexdigest(),
            file_bytes=len(read.body),
            collector_file_bytes_read=len(read.body),
            navigation=navigation,
            tor=work.tor,
        )
        capture = BrowserResponseCapture(body=read.body, evidence=evidence)
        capture.validate_policy(self.config)
        return capture

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
        async with work.reading(self.config.max_dom_bytes) as spend:
            return await self._dom_read(page, work, spend)

    async def _dom_read(
        self, page: "Page", work: CaptureWork, spend: DownloadSpend
    ) -> tuple[bytes, str, Literal["text/html", "application/xhtml+xml"]]:
        if not self.config.permits(page.url) or (
            work.scope is not None and not work.scope.permits(page.url)
        ):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        allowance = min(self.config.max_dom_bytes, spend.max_bytes - spend.bytes_read)
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
        spend.bytes_read += len(dom)
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
        navigation: BrowserNavigationEvidence | None = None,
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
            navigation=navigation,
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

    async def _capture(self, url: str, work: CaptureWork) -> BrowserAcquisition:
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

    async def _capture(self, url: str, work: CaptureWork) -> BrowserAcquisition:
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
