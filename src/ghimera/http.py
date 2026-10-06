"""One real HTTP GET, with pinned DNS and bounded libcurl callbacks.

curl_cffi's setopt callback/options boundary is dynamic in the vendor API. Values
are built from validated contracts here; getinfo unions are narrowed explicitly.
There is no session cookie jar, netrc, inherited proxy, implicit redirect or TLS
verification override. Scheduling, robots and accounting belong to FetchLadder.
Source credentials are explicit in-memory bindings selected by exact origin/path.
"""

import asyncio
from collections.abc import Mapping
from typing import Protocol

from curl_cffi import AsyncCurl, Curl, CurlError, CurlInfo, CurlOpt
from curl_cffi.curl import CURL_WRITEFUNC_ERROR

from ghimera.challenge_types import ChallengeEvidence
from ghimera.challenges import ChallengeSessions
from ghimera.config import GhimeraConfig
from ghimera.fetch import FetchRoute
from ghimera.models import FetchRequest, Page
from ghimera.refusals import FetchCancelled, FetchFailure, GhimeraRefused, RefusalCode
from ghimera.response import RETAINED_HEADERS
from ghimera.source_session_types import SourceSessionUse
from ghimera.source_sessions import SourceCredentials, SourceSessions
from ghimera.transport import NetworkGuard as NetworkGuard
from ghimera.transport import Resolver, RoutingConnector, SystemResolver, validate_transition
from ghimera.transport_types import TransportEvidence


class CurlMulti(Protocol):
    """Narrow the vendor's partially annotated async lifecycle at this boundary."""

    def add_handle(self, curl: Curl) -> asyncio.Future[None]: ...

    async def close(self) -> None: ...


class BoundedBody:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.exhausted = False

    def write(self, chunk: bytes) -> int:
        room = self.limit - len(self.data)
        self.data.extend(chunk[:room])
        if len(chunk) > room:
            self.exhausted = True
            return CURL_WRITEFUNC_ERROR  # No exception crosses the C callback.
        return len(chunk)


class BoundedHeaders:
    """No cookie/auth retention, and no unbounded vendor header buffer."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._read = 0
        self.entries: list[tuple[str, str]] = []
        self.exhausted = False

    @property
    def values(self) -> dict[str, str]:
        """Scalar lookup compatibility; wire replay uses entries to keep repeats."""
        return dict(self.entries)

    def write(self, line: bytes) -> int:
        self._read += len(line)
        if self._read > self._limit:
            self.exhausted = True
            return CURL_WRITEFUNC_ERROR
        if line.startswith(b"HTTP/"):
            self.entries.clear()
        elif b":" in line:
            name, value = line.split(b":", 1)
            key = name.decode("ascii", errors="replace").lower()
            if key in RETAINED_HEADERS:
                self.entries.append((key, value.decode("latin-1").strip()))
        return len(line)


def page_barrier(page: Page) -> RefusalCode | None:
    """Refuse explicit interstitials, not ordinary reporting *about* challenges."""
    if page.content_type != "text/html":
        return None
    body = page.body.decode("utf-8", errors="replace").lower()
    # Entitlement walls take precedence even when the login form embeds CAPTCHA.
    if "type=" in body and "password" in body and "sign in to continue" in body:
        return RefusalCode.LOGIN_WALL
    if "subscribe to continue reading" in body or 'data-paywall="true"' in body:
        return RefusalCode.PAYWALL
    if (
        "<title>security check - substation</title>" in body
        and "document.cookie" in body
        and "__substation_pow" in body
    ):
        return RefusalCode.CHALLENGE_NOT_SOLVED
    if any(
        marker in body
        for marker in (
            "cf-turnstile",
            "g-recaptcha",
            "h-captcha",
            "captcha-container",
            "<title>just a moment",
            "<title>access denied",
            "verify you are human",
        )
    ):
        return RefusalCode.CHALLENGE_NOT_SOLVED
    return None


class CurlRoute(FetchRoute):
    name = "curl_cffi"
    needs_browser = False
    cost = 0
    uses_http = True

    def __init__(
        self,
        config: GhimeraConfig,
        *,
        resolver: Resolver | None = None,
        source_credentials: Mapping[str, SourceCredentials] | None = None,
    ) -> None:
        if config.http is None:
            raise ValueError("real HTTP routes require the versioned HTTP/network policy")
        self._config = config
        self._http = config.http
        self._sessions = SourceSessions(config.source_sessions, source_credentials)
        self._challenges = ChallengeSessions(config.challenges) if config.challenges else None
        self._connector = RoutingConnector(
            config.http.network, config.transport, resolver or SystemResolver()
        )

    def validate_config(self, config: GhimeraConfig) -> None:
        if config != self._config:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    def validate_redirect(self, previous: str, target: str) -> None:
        validate_transition(previous, target, self._config.transport)

    def transport_selection(self, url: str) -> TransportEvidence:
        return self._connector.selection(url)

    def source_session_selection(self, url: str) -> SourceSessionUse | None:
        selected = self._sessions.select(url)
        return selected.evidence if selected is not None else None

    async def attempt(self, request: FetchRequest) -> Page:
        source_session = self._sessions.select(request.url)
        clearance = self._challenges.select(request.url) if self._challenges else None
        # Never merge browser cookies into an entitled account's Cookie header.
        if source_session is not None:
            clearance = None
        started = asyncio.get_running_loop().time()
        connection = await self._connector.prepare(request.url, request.timeout_seconds)
        remaining = request.timeout_seconds - (asyncio.get_running_loop().time() - started)
        body = BoundedBody(min(request.max_bytes, self._http.max_response_bytes))
        headers = BoundedHeaders(self._http.max_header_bytes)
        curl = Curl()
        multi: CurlMulti = AsyncCurl()
        try:
            if remaining <= 0:
                raise GhimeraRefused(RefusalCode.FETCH_FAILED)
            if curl.impersonate(self._config.impersonation_profile, default_headers=True) != 0:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            curl.setopt(CurlOpt.URL, request.url)
            if connection.resolve:
                curl.setopt(CurlOpt.RESOLVE, list(connection.resolve))
            if connection.tunnel is not None:
                curl.setopt(CurlOpt.UNIX_SOCKET_PATH, connection.tunnel.path)
            curl.setopt(CurlOpt.PROXY, "")
            curl.setopt(CurlOpt.NOPROXY, "")  # An ambient NO_PROXY may not bypass a Tor route.
            curl.setopt(CurlOpt.NETRC, 0)
            curl.setopt(CurlOpt.FOLLOWLOCATION, 0)
            curl.setopt(CurlOpt.PROTOCOLS_STR, "http,https")
            curl.setopt(CurlOpt.SSL_VERIFYPEER, 1)
            curl.setopt(CurlOpt.SSL_VERIFYHOST, 2)
            curl.setopt(CurlOpt.TIMEOUT_MS, max(1, int(remaining * 1000)))
            curl.setopt(CurlOpt.ACCEPT_ENCODING, "")
            user_agent = clearance.user_agent if clearance else self._config.user_agent
            clearance_headers = (
                [b"Cookie: " + clearance.cookie.get_secret_value().encode()] if clearance else []
            )
            curl.setopt(
                CurlOpt.HTTPHEADER,
                [
                    f"User-Agent: {user_agent}".encode(),
                    *clearance_headers,
                    *(f"{key}: {value}".encode() for key, value in request.headers),
                    *(
                        f"{key}: {secret.get_secret_value()}".encode("ascii")
                        for key, secret in (source_session.headers if source_session else ())
                    ),
                ],
            )
            curl.setopt(CurlOpt.WRITEFUNCTION, body.write)
            curl.setopt(CurlOpt.HEADERFUNCTION, headers.write)
            await multi.add_handle(curl)
            if body.exhausted or headers.exhausted:
                raise FetchFailure(
                    RefusalCode.BUDGET_EXHAUSTED
                    if body.exhausted
                    else RefusalCode.ADAPTER_CONTRACT,
                    len(body.data),
                )
            status = curl.getinfo(CurlInfo.RESPONSE_CODE)
            final_url = curl.getinfo(CurlInfo.EFFECTIVE_URL)
            if not isinstance(status, int) or not isinstance(final_url, bytes):
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            result = Page(
                url=request.url,
                final_url=final_url.decode("utf-8"),
                status=status,
                content_type=headers.values.get("content-type", "application/octet-stream")
                .split(";", 1)[0]
                .strip()
                .lower(),
                body=bytes(body.data),
                headers=tuple(headers.entries),
                transport=connection.evidence,
                source_session=source_session.evidence if source_session else None,
                challenge_use=clearance.evidence if clearance else None,
            )
            if clearance is not None and page_barrier(result) is not None:
                if self._challenges is not None:
                    self._challenges.discard(request.url)
            return result
        except CurlError as exc:
            code = (
                RefusalCode.BUDGET_EXHAUSTED
                if body.exhausted
                else RefusalCode.ADAPTER_CONTRACT
                if headers.exhausted
                else RefusalCode.TOR_UNAVAILABLE
                if connection.proxy_endpoint and exc.code in {5, 7, 97}
                else RefusalCode.FETCH_FAILED
            )
            raise FetchFailure(code, len(body.data)) from None
        except asyncio.CancelledError:
            raise FetchCancelled(len(body.data)) from None
        finally:
            await multi.close()
            curl.close()
            await connection.close()

    async def clear_challenge(
        self, page: Page, *, timeout_seconds: float, max_bytes: int
    ) -> tuple[ChallengeEvidence, int]:
        if (
            self._challenges is None
            or self._sessions.select(page.final_url) is not None
            or page.transport is None
            or page.transport.mode != "direct"
        ):
            raise GhimeraRefused(RefusalCode.CHALLENGE_NOT_SOLVED)
        return await self._challenges.resolve(
            page.final_url, timeout_seconds=timeout_seconds, max_bytes=max_bytes
        )

    def escalation_reason(self, page: Page) -> str | None:
        barrier = page_barrier(page)
        if barrier is not None:
            raise GhimeraRefused(barrier)
        if page.content_type == "text/html":
            body = page.body.decode("utf-8", errors="replace").lower()
            if "enable javascript" in body or "javascript is required" in body:
                return "javascript_required"
        return None
