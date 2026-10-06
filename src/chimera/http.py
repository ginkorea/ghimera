"""One real HTTP GET, with pinned DNS and bounded libcurl callbacks.

curl_cffi's setopt callback/options boundary is dynamic in the vendor API. Values
are built from validated contracts here; getinfo unions are narrowed explicitly.
There is no session cookie jar, netrc, inherited proxy, implicit redirect or TLS
verification override. Scheduling, robots and accounting belong to FetchLadder.
"""

import asyncio
from typing import Protocol

from curl_cffi import AsyncCurl, Curl, CurlError, CurlInfo, CurlOpt
from curl_cffi.curl import CURL_WRITEFUNC_ERROR

from chimera.config import ChimeraConfig
from chimera.fetch import FetchRoute
from chimera.models import FetchRequest, Page
from chimera.refusals import ChimeraRefused, FetchCancelled, FetchFailure, RefusalCode
from chimera.transport import NetworkGuard as NetworkGuard
from chimera.transport import Resolver, RoutingConnector, SystemResolver, validate_transition
from chimera.transport_types import TransportEvidence


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
        self.values: dict[str, str] = {}
        self.exhausted = False

    def write(self, line: bytes) -> int:
        self._read += len(line)
        if self._read > self._limit:
            self.exhausted = True
            return CURL_WRITEFUNC_ERROR
        if line.startswith(b"HTTP/"):
            self.values.clear()
        elif b":" in line:
            name, value = line.split(b":", 1)
            key = name.decode("ascii", errors="replace").lower()
            if key in {"content-type", "location", "etag", "last-modified", "retry-after"}:
                self.values[key] = value.decode("latin-1").strip()
        return len(line)


def page_barrier(page: Page) -> RefusalCode | None:
    """Refuse explicit interstitials, not ordinary reporting *about* challenges."""
    if page.content_type != "text/html":
        return None
    body = page.body.decode("utf-8", errors="replace").lower()
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
    if "type=" in body and "password" in body and "sign in to continue" in body:
        return RefusalCode.LOGIN_WALL
    if "subscribe to continue reading" in body or 'data-paywall="true"' in body:
        return RefusalCode.PAYWALL
    return None


class CurlRoute(FetchRoute):
    name = "curl_cffi"
    needs_browser = False
    cost = 0
    uses_http = True

    def __init__(self, config: ChimeraConfig, *, resolver: Resolver | None = None) -> None:
        if config.http is None:
            raise ValueError("real HTTP routes require the versioned HTTP/network policy")
        self._config = config
        self._http = config.http
        self._connector = RoutingConnector(
            config.http.network, config.transport, resolver or SystemResolver()
        )

    def validate_config(self, config: ChimeraConfig) -> None:
        if config != self._config:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    def validate_redirect(self, previous: str, target: str) -> None:
        validate_transition(previous, target, self._config.transport)

    def transport_selection(self, url: str) -> TransportEvidence:
        return self._connector.selection(url)

    async def attempt(self, request: FetchRequest) -> Page:
        started = asyncio.get_running_loop().time()
        connection = await self._connector.prepare(request.url, request.timeout_seconds)
        remaining = request.timeout_seconds - (asyncio.get_running_loop().time() - started)
        body = BoundedBody(min(request.max_bytes, self._http.max_response_bytes))
        headers = BoundedHeaders(self._http.max_header_bytes)
        curl = Curl()
        multi: CurlMulti = AsyncCurl()
        try:
            if remaining <= 0:
                raise ChimeraRefused(RefusalCode.FETCH_FAILED)
            if curl.impersonate(self._config.impersonation_profile, default_headers=True) != 0:
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
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
            curl.setopt(
                CurlOpt.HTTPHEADER,
                [
                    f"User-Agent: {self._config.user_agent}".encode(),
                    *(f"{key}: {value}".encode() for key, value in request.headers),
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
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            return Page(
                url=request.url,
                final_url=final_url.decode("utf-8"),
                status=status,
                content_type=headers.values.get("content-type", "application/octet-stream")
                .split(";", 1)[0]
                .strip()
                .lower(),
                body=bytes(body.data),
                headers=tuple(headers.values.items()),
                transport=connection.evidence,
            )
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

    def escalation_reason(self, page: Page) -> str | None:
        barrier = page_barrier(page)
        if barrier is not None:
            raise ChimeraRefused(barrier)
        if page.content_type == "text/html":
            body = page.body.decode("utf-8", errors="replace").lower()
            if "enable javascript" in body or "javascript is required" in body:
                return "javascript_required"
        return None
