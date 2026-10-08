"""Pinned, bounded private JSON POST; separate from source/Tor traffic and auth."""

import asyncio
import ipaddress
import math
import re
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar
from urllib.parse import urlsplit

from curl_cffi import AsyncCurl, Curl, CurlError, CurlInfo, CurlOpt
from pydantic import SecretStr

from ghimera.http import BoundedBody, BoundedHeaders, CurlMulti
from ghimera.private_service_config import PrivateJsonPolicy, validate_private_json
from ghimera.refusals import GhimeraRefused
from ghimera.transport import Destination, Resolver, SystemResolver


@dataclass(frozen=True)
class JsonResponse:
    status: int | None
    body: bytes
    content_type: str


class JsonWireFailure(Exception):
    def __init__(self, response: JsonResponse) -> None:
        self.response = response
        super().__init__("private JSON request failed")


class JsonWireCancelled(asyncio.CancelledError):
    def __init__(self, response: JsonResponse) -> None:
        self.response = response
        super().__init__()


PolicyT = TypeVar("PolicyT", bound=PrivateJsonPolicy)


class PinnedJsonHttp(Generic[PolicyT]):
    def __init__(
        self,
        config: PolicyT,
        *,
        credential: SecretStr | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        validate_private_json(config)
        if (config.authorization != "none") != (credential is not None):
            raise ValueError("private credential must match the configured authorization mode")
        if credential is not None:
            text = credential.get_secret_value()
            if not text.strip() or any(ord(char) < 33 for char in text):
                raise ValueError("private credential must be a single safe header value")
            if (
                urlsplit(config.endpoint).scheme != "https"
                and not config.allow_plaintext_credentials
            ):
                raise ValueError("private credential requires HTTPS or exact plaintext approval")
        self._config, self._credential = config, credential
        self._resolver = resolver or SystemResolver()

    @property
    def config(self) -> PolicyT:
        return self._config

    async def _destination(self) -> Destination:
        parsed = urlsplit(self.config.endpoint)
        host = parsed.hostname
        if host is None:
            raise JsonWireFailure(JsonResponse(None, b"", ""))
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses: tuple[str, ...]
        try:
            addresses = (str(ipaddress.ip_address(host)),)
        except ValueError:
            try:
                addresses = await self._resolver.resolve(host, port)
            except (OSError, GhimeraRefused):
                raise JsonWireFailure(JsonResponse(None, b"", "")) from None
        if not addresses or any(
            address not in self.config.approved_addresses for address in addresses
        ):
            raise JsonWireFailure(JsonResponse(None, b"", ""))
        return Destination(host, port, addresses[0])

    async def post(
        self, body: bytes, *, max_bytes: int | None = None, timeout_seconds: float | None = None
    ) -> JsonResponse:
        return await self.request(
            "POST", body, max_bytes=max_bytes, timeout_seconds=timeout_seconds
        )

    async def request(
        self,
        method: Literal["GET", "POST", "PUT"],
        body: bytes = b"",
        *,
        endpoint_suffix: str = "",
        max_bytes: int | None = None,
        timeout_seconds: float | None = None,
    ) -> JsonResponse:
        """Same admitted origin; suffixes cannot expand authority or traverse paths."""
        if method not in {"GET", "POST", "PUT"} or (method == "GET" and body):
            raise ValueError("private JSON request method/body is invalid")
        if endpoint_suffix and not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", endpoint_suffix):
            raise ValueError("private JSON suffix must be bounded origin-relative segments")
        config = self.config
        if (max_bytes is not None and max_bytes <= 0) or (
            timeout_seconds is not None
            and (not math.isfinite(timeout_seconds) or timeout_seconds <= 0)
        ):
            raise ValueError("per-call private JSON limits must be positive")
        maximum = min(config.max_response_bytes, max_bytes or config.max_response_bytes)
        timeout = min(config.timeout_seconds, timeout_seconds or config.timeout_seconds)
        empty = JsonResponse(None, b"", "")
        if len(body) > config.max_request_bytes:
            raise JsonWireFailure(empty)
        buffer, headers = BoundedBody(maximum), BoundedHeaders(config.max_header_bytes)
        curl, multi = Curl(), AsyncCurl()
        manager: CurlMulti = multi
        response = empty
        try:
            async with asyncio.timeout(timeout):
                destination = await self._destination()
                endpoint = (
                    config.endpoint.rstrip("/") + endpoint_suffix
                    if endpoint_suffix
                    else config.endpoint
                )
                curl.setopt(CurlOpt.URL, endpoint)
                curl.setopt(CurlOpt.RESOLVE, [destination.curl_resolve])
                curl.setopt(CurlOpt.PROXY, "")
                curl.setopt(CurlOpt.NOPROXY, "")
                curl.setopt(CurlOpt.NETRC, 0)
                curl.setopt(CurlOpt.FOLLOWLOCATION, 0)
                curl.setopt(CurlOpt.PROTOCOLS_STR, "http,https")
                curl.setopt(CurlOpt.SSL_VERIFYPEER, 1)
                curl.setopt(CurlOpt.SSL_VERIFYHOST, 2)
                curl.setopt(CurlOpt.TIMEOUT_MS, max(1, int(timeout * 1000)))
                curl.setopt(CurlOpt.ACCEPT_ENCODING, "")
                if method != "GET":
                    curl.setopt(CurlOpt.POSTFIELDS, body)
                if method != "POST":
                    curl.setopt(CurlOpt.CUSTOMREQUEST, method)
                metadata = [b"Content-Type: application/json", b"Accept: application/json"]
                if self._credential is not None:
                    scheme = b"ApiKey " if config.authorization == "api_key" else b"Bearer "
                    metadata.append(
                        b"Authorization: " + scheme + self._credential.get_secret_value().encode()
                    )
                curl.setopt(CurlOpt.HTTPHEADER, metadata)
                curl.setopt(CurlOpt.WRITEFUNCTION, buffer.write)
                curl.setopt(CurlOpt.HEADERFUNCTION, headers.write)
                await manager.add_handle(curl)
                status = curl.getinfo(CurlInfo.RESPONSE_CODE)
                if not isinstance(status, int):
                    raise JsonWireFailure(empty)
                response = JsonResponse(
                    status,
                    bytes(buffer.data),
                    headers.values.get("content-type", "").split(";", 1)[0].strip().lower(),
                )
                if buffer.exhausted or headers.exhausted:
                    raise JsonWireFailure(response)
                return response
        except (CurlError, TimeoutError, JsonWireFailure):
            raise JsonWireFailure(
                JsonResponse(response.status, bytes(buffer.data), response.content_type)
            ) from None
        except asyncio.CancelledError:
            raise JsonWireCancelled(
                JsonResponse(response.status, bytes(buffer.data), response.content_type)
            ) from None
        finally:
            await manager.close()
            curl.close()
