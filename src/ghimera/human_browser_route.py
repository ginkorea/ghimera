"""A browser-DOM FetchRoute, sharing the ladder's ordering/accounting owner."""

from ghimera.browser_operation_types import BrowserOperation
from ghimera.config import GhimeraConfig
from ghimera.fetch import FetchRoute
from ghimera.http import html_barrier
from ghimera.human_browser_types import AuthorizedBrowserSession, BrowserCapture, HumanBrowserConfig
from ghimera.models import FetchRequest, Page
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.source_session_types import origin_key


class HumanBrowserRoute(FetchRoute):
    name = "human_browser_dom"
    needs_browser = True
    cost = 0
    captures_browser_dom = True

    def __init__(self, config: HumanBrowserConfig, session: AuthorizedBrowserSession) -> None:
        self._config, self._session = config, session

    def validate_config(self, config: GhimeraConfig) -> None:
        if config.human_browser != self._config:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self._session.validate_config(self._config)

    def handles(self, url: str) -> bool:
        # A selected origin never falls back to a different HTTP session just
        # because its requested path is not permitted by the browser policy.
        return any(origin_key(url) == origin_key(item.origin) for item in self._config.origins)

    async def attempt(self, request: FetchRequest) -> Page:
        return await self._capture(request, None)

    async def attempt_browser(self, request: FetchRequest, operation: BrowserOperation) -> Page:
        return await self._capture(request, operation)

    async def _capture(self, request: FetchRequest, operation: BrowserOperation | None) -> Page:
        if request.headers or request.scope is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if operation is None:
            capture = await self._session.capture(
                request.url,
                max_bytes=request.max_bytes,
                timeout_seconds=request.timeout_seconds,
                scope=request.scope,
            )
        else:
            capture = await self._session.capture(
                request.url,
                max_bytes=request.max_bytes,
                timeout_seconds=request.timeout_seconds,
                scope=request.scope,
                operation=operation,
            )
        capture.validate_policy(self._config)
        return Page(
            url=request.url,
            final_url=capture.evidence.final_url,
            status=None,
            content_type=capture.evidence.content_type,
            body=capture.dom if isinstance(capture, BrowserCapture) else capture.body,
            human_browser=capture.evidence,
        )

    def escalation_reason(self, page: Page) -> str | None:
        if page.human_browser is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if barrier := html_barrier(page.content_type, page.body):
            raise GhimeraRefused(barrier)
        return None
