"""Explicit same-target DOM capture with a caller-supplied human assistance port.

The caller owns browser egress, credentials and lifecycle. This adapter never
discovers profiles, automates credentials/challenges, copies cookies, reports
unobserved HTTP response bytes or closes the caller's browser.
"""

import asyncio
import base64
import hashlib
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ghimera.http import html_barrier
from ghimera.human_browser_types import (
    AssistanceDecision,
    AssistanceObservation,
    BrowserAssistanceRequest,
    BrowserCapture,
    HumanAssistant,
    HumanBrowserConfig,
    HumanBrowserEvidence,
)
from ghimera.refusals import FetchCancelled, FetchFailure, GhimeraRefused, RefusalCode

if TYPE_CHECKING:
    from patchright.async_api import Browser, Page, Playwright


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


class HumanCaptureFailure(FetchFailure):
    def __init__(
        self, code: RefusalCode, bytes_read: int, observations: tuple[AssistanceObservation, ...]
    ) -> None:
        self.assistance = observations
        super().__init__(code, bytes_read)


class HumanCaptureCancelled(FetchCancelled):
    def __init__(self, bytes_read: int, observations: tuple[AssistanceObservation, ...]) -> None:
        self.assistance = observations
        super().__init__(bytes_read)


class CaptureWork:
    """Mutable observations belong to one capture, never to the browser/session."""

    def __init__(self) -> None:
        self.bytes_read = 0
        self.assistance: list[AssistanceObservation] = []


class ChromiumHumanSession:
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

    async def capture(self, url: str) -> BrowserCapture:
        if not self.config.permits(url):
            raise HumanCaptureFailure(RefusalCode.OUT_OF_SCOPE, 0, ())
        work = CaptureWork()
        try:
            async with asyncio.timeout(self.config.timeout_seconds), self._target_lock:
                return await self._capture(url, work)
        except asyncio.CancelledError:
            raise HumanCaptureCancelled(work.bytes_read, tuple(work.assistance)) from None
        except GhimeraRefused as exc:
            raise HumanCaptureFailure(exc.code, work.bytes_read, tuple(work.assistance)) from None
        except TimeoutError:
            raise HumanCaptureFailure(
                RefusalCode.FETCH_FAILED, work.bytes_read, tuple(work.assistance)
            ) from None

    async def _capture(self, url: str, work: CaptureWork) -> BrowserCapture:
        from patchright.async_api import Error, async_playwright

        driver: Playwright | None = None
        try:
            driver = await async_playwright().start()
            browser = await driver.chromium.connect_over_cdp(
                self.config.control_endpoint,
                no_defaults=True,
                timeout=self.config.timeout_seconds * 1000,
            )
            page = await self._select(browser)
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
        except (Error, ValidationError, ValueError):
            # Vendor errors may contain source/control URLs and secret headers.
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        finally:
            if driver is not None:
                # Stop this transport only, never browser.close/context.close.
                await self._detach(driver)

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
        if not self.config.permits(page.url):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
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
                self.config.max_dom_bytes,
                isolated_context=True,
            )
        )
        dom = base64.b64decode(reply.dom_base64, validate=True)
        work.bytes_read += len(dom)
        if not self.config.permits(reply.url) or page.url != reply.url:
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        if len(dom) > self.config.max_dom_bytes:
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
            deadline_unix_seconds=time.time() + self.config.assistance_timeout_seconds,
        )
        try:
            async with asyncio.timeout(self.config.assistance_timeout_seconds):
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
