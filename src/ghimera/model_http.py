"""Bounded private model-control POST. Never reuses crawl/Tor routing or auth."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from ghimera.model_config import PrivateModelService
from ghimera.private_json import JsonResponse, JsonWireCancelled, JsonWireFailure, PinnedJsonHttp
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.transport import Resolver


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
        self._http = PinnedJsonHttp(config, credential=credential, resolver=resolver)

    @property
    def config(self) -> PrivateModelService:
        return self._http.config

    async def post(self, body: bytes) -> ModelHttpResponse:
        try:
            response = await self._http.post(body)
            return _model_response(response)
        except JsonWireFailure as exc:
            raise ModelWireFailure(_model_response(exc.response)) from None
        except JsonWireCancelled as exc:
            raise ModelWireCancelled(_model_response(exc.response)) from None


def _model_response(response: JsonResponse) -> ModelHttpResponse:
    return ModelHttpResponse(response.status, response.body, response.content_type)
