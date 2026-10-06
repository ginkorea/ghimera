"""One real HTTP GET, with pinned DNS and bounded libcurl callbacks.

curl_cffi's setopt callback/options boundary is dynamic in the vendor API. Values
are built from validated contracts here; getinfo unions are narrowed explicitly.
There is no session cookie jar, netrc, inherited proxy, implicit redirect or TLS
verification override. Scheduling, robots and accounting belong to FetchLadder.
"""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from curl_cffi import AsyncCurl, Curl, CurlError, CurlInfo, CurlOpt
from curl_cffi.curl import CURL_WRITEFUNC_ERROR

from chimera.config import ChimeraConfig, NetworkPolicy
from chimera.fetch import FetchRoute
from chimera.models import FetchRequest, Page
from chimera.refusals import ChimeraRefused, FetchCancelled, FetchFailure, RefusalCode


class Resolver(Protocol):
    async def resolve(self, host: str, port: int) -> tuple[str, ...]: ...


class CurlMulti(Protocol):
    """Narrow the vendor's partially annotated async lifecycle at this boundary."""

    def add_handle(self, curl: Curl) -> asyncio.Future[None]: ...

    async def close(self) -> None: ...


class SystemResolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        try:
            entries = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except OSError:
            raise ChimeraRefused(RefusalCode.FETCH_FAILED) from None
        return tuple(dict.fromkeys(str(item[4][0]) for item in entries))


@dataclass(frozen=True)
class Destination:
    host: str
    port: int
    address: str

    @property
    def curl_resolve(self) -> str:
        address = f"[{self.address}]" if ":" in self.address else self.address
        return f"{self.host}:{self.port}:{address}"


class NetworkGuard:
    def __init__(self, policy: NetworkPolicy, resolver: Resolver) -> None:
        self._policy = policy
        self._resolver = resolver

    async def destination(self, url: str) -> Destination:
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if (
                parsed.scheme not in {"http", "https"}
                or host is None
                or parsed.username is not None
                or parsed.password is not None
                or any(ord(char) < 33 for char in url)
            ):
                raise ValueError("unsafe URL")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError("IP literal")
            addresses = await self._resolver.resolve(host, port)
            if not addresses:
                raise ChimeraRefused(RefusalCode.FETCH_FAILED)
            for value in addresses:
                address = ipaddress.ip_address(value)
                if self._policy.mode == "public":
                    if (
                        not address.is_global
                        or address.is_multicast
                        or address.is_reserved
                        or port not in {80, 443}
                    ):
                        raise ValueError("non-public destination")
                elif (
                    str(address) not in self._policy.fixture_addresses
                    or port not in self._policy.fixture_ports
                ):
                    raise ValueError("undeclared fixture destination")
            return Destination(host, port, addresses[0])
        except ValueError:
            raise ChimeraRefused(RefusalCode.OUT_OF_SCOPE) from None


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
        self._guard = NetworkGuard(config.http.network, resolver or SystemResolver())

    def validate_config(self, config: ChimeraConfig) -> None:
        if config != self._config:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    async def attempt(self, request: FetchRequest) -> Page:
        destination = await self._guard.destination(request.url)
        body = BoundedBody(min(request.max_bytes, self._http.max_response_bytes))
        headers = BoundedHeaders(self._http.max_header_bytes)
        curl = Curl()
        multi: CurlMulti = AsyncCurl()
        try:
            if curl.impersonate(self._config.impersonation_profile, default_headers=True) != 0:
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            curl.setopt(CurlOpt.URL, request.url)
            curl.setopt(CurlOpt.RESOLVE, [destination.curl_resolve])
            curl.setopt(CurlOpt.PROXY, "")
            curl.setopt(CurlOpt.NETRC, 0)
            curl.setopt(CurlOpt.FOLLOWLOCATION, 0)
            curl.setopt(CurlOpt.PROTOCOLS_STR, "http,https")
            curl.setopt(CurlOpt.SSL_VERIFYPEER, 1)
            curl.setopt(CurlOpt.SSL_VERIFYHOST, 2)
            curl.setopt(CurlOpt.TIMEOUT_MS, max(1, int(request.timeout_seconds * 1000)))
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
            )
        except CurlError:
            code = (
                RefusalCode.BUDGET_EXHAUSTED
                if body.exhausted
                else RefusalCode.ADAPTER_CONTRACT
                if headers.exhausted
                else RefusalCode.FETCH_FAILED
            )
            raise FetchFailure(code, len(body.data)) from None
        except asyncio.CancelledError:
            raise FetchCancelled(len(body.data)) from None
        finally:
            await multi.close()
            curl.close()

    def escalation_reason(self, page: Page) -> str | None:
        barrier = page_barrier(page)
        if barrier is not None:
            raise ChimeraRefused(barrier)
        if page.content_type == "text/html":
            body = page.body.decode("utf-8", errors="replace").lower()
            if "enable javascript" in body or "javascript is required" in body:
                return "javascript_required"
        return None
