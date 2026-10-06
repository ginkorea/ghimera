"""Bounded private model-control POST. Never reuses crawl/Tor routing or auth."""

import asyncio
import ipaddress
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from curl_cffi import AsyncCurl, Curl, CurlError, CurlInfo, CurlOpt
from pydantic import SecretStr

from ghimera.http import BoundedBody, BoundedHeaders, CurlMulti
from ghimera.model_config import PrivateModelService
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.transport import Destination, Resolver, SystemResolver


@dataclass(frozen=True)
class ModelHttpResponse:
    status: int | None
    body: bytes
    content_type: str


class ModelWireFailure(GhimeraRefused):
    def __init__(self, response: ModelHttpResponse) -> None:
        self.response = response
        super().__init__(RefusalCode.MODEL_UNAVAILABLE)


class ModelWireCancelled(asyncio.CancelledError):
    def __init__(self, response: ModelHttpResponse) -> None:
        self.response = response
        super().__init__()


class ModelHttpPort(Protocol):
    @property
    def config(self) -> PrivateModelService: ...

    async def post(self, body: bytes) -> ModelHttpResponse: ...


class PinnedModelHttp:
    def __init__(
        self,
        config: PrivateModelService,
        *,
        credential: SecretStr | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        if (config.authorization == "bearer") != (credential is not None):
            raise ValueError("model credential must match the configured authorization mode")
        if credential is not None:
            text = credential.get_secret_value()
            if not text.strip() or any(ord(char) < 33 for char in text):
                raise ValueError("model credential must be a single safe bearer value")
            if (
                urlsplit(config.endpoint).scheme != "https"
                and not config.allow_plaintext_credentials
            ):
                raise ValueError(
                    "model credential requires HTTPS unless exact plaintext access is approved"
                )
        self._config, self._credential = config, credential
        self._resolver = resolver or SystemResolver()

    @property
    def config(self) -> PrivateModelService:
        return self._config

    async def _destination(self) -> Destination:
        parsed = urlsplit(self._config.endpoint)
        host = parsed.hostname
        if host is None:
            raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses: tuple[str, ...]
        try:
            addresses = (str(ipaddress.ip_address(host)),)
        except ValueError:
            addresses = await self._resolver.resolve(host, port)
        if not addresses or any(
            address not in self._config.approved_addresses for address in addresses
        ):
            raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
        return Destination(host, port, addresses[0])

    async def post(self, body: bytes) -> ModelHttpResponse:
        config = self._config
        empty = ModelHttpResponse(None, b"", "")
        if len(body) > config.max_request_bytes:
            raise ModelWireFailure(empty)
        buffer, headers = (
            BoundedBody(config.max_response_bytes),
            BoundedHeaders(config.max_header_bytes),
        )
        curl, multi = Curl(), AsyncCurl()
        manager: CurlMulti = multi
        response = empty
        try:
            async with asyncio.timeout(config.timeout_seconds):
                destination = await self._destination()
                curl.setopt(CurlOpt.URL, config.endpoint)
                curl.setopt(CurlOpt.RESOLVE, [destination.curl_resolve])
                curl.setopt(CurlOpt.PROXY, "")
                curl.setopt(CurlOpt.NOPROXY, "")
                curl.setopt(CurlOpt.NETRC, 0)
                curl.setopt(CurlOpt.FOLLOWLOCATION, 0)
                curl.setopt(CurlOpt.PROTOCOLS_STR, "http,https")
                curl.setopt(CurlOpt.SSL_VERIFYPEER, 1)
                curl.setopt(CurlOpt.SSL_VERIFYHOST, 2)
                curl.setopt(CurlOpt.TIMEOUT_MS, max(1, int(config.timeout_seconds * 1000)))
                curl.setopt(CurlOpt.ACCEPT_ENCODING, "")
                curl.setopt(CurlOpt.POSTFIELDS, body)
                metadata = [b"Content-Type: application/json", b"Accept: application/json"]
                if self._credential is not None:
                    metadata.append(
                        b"Authorization: Bearer " + self._credential.get_secret_value().encode()
                    )
                curl.setopt(CurlOpt.HTTPHEADER, metadata)
                curl.setopt(CurlOpt.WRITEFUNCTION, buffer.write)
                curl.setopt(CurlOpt.HEADERFUNCTION, headers.write)
                await manager.add_handle(curl)
                status = curl.getinfo(CurlInfo.RESPONSE_CODE)
                if not isinstance(status, int):
                    raise ModelWireFailure(empty)
                response = ModelHttpResponse(
                    status,
                    bytes(buffer.data),
                    headers.values.get("content-type", "").split(";", 1)[0].strip().lower(),
                )
                if buffer.exhausted or headers.exhausted:
                    raise ModelWireFailure(response)
                return response
        except (CurlError, TimeoutError, GhimeraRefused):
            raise ModelWireFailure(
                ModelHttpResponse(response.status, bytes(buffer.data), response.content_type)
            ) from None
        except asyncio.CancelledError:
            raise ModelWireCancelled(
                ModelHttpResponse(response.status, bytes(buffer.data), response.content_type)
            ) from None
        finally:
            await manager.close()
            curl.close()
