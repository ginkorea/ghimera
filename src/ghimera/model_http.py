"""Bounded private model-control POST. Never reuses crawl/Tor routing or auth."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from ghimera.model_config import EmbeddingServiceConfig, ModelServiceConfig, PrivateModelService
from ghimera.model_gateway_config import validate_model_gateway
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


class _PinnedModelJsonHttp(PinnedJsonHttp[PrivateModelService]):
    """Model control specializes admission; generic JSON consumers remain private."""

    def _validate_policy(self, config: PrivateModelService) -> None:
        # Recheck the concrete versioned boundary even for model_copy instances.
        type(config).model_validate(config.model_dump())
        if config.gateway is None:
            super()._validate_policy(config)
        elif isinstance(config, (ModelServiceConfig, EmbeddingServiceConfig)):
            validate_model_gateway(config, config.gateway)
        else:
            raise ValueError("gateway admission requires a versioned model or embedding service")

    @property
    def _protocols(self) -> str:
        return "https" if self.config.gateway is not None else super()._protocols


class PinnedModelHttp:
    def __init__(
        self,
        config: PrivateModelService,
        *,
        credential: SecretStr | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._http = _PinnedModelJsonHttp(config, credential=credential, resolver=resolver)

    @property
    def config(self) -> PrivateModelService:
        return self._http.config

    async def post(self, body: bytes) -> ModelHttpResponse:
        try:
            response = await self._http.post(body)
            if (
                self.config.gateway is not None
                and response.status is not None
                and (300 <= response.status < 400)
            ):
                raise ModelWireFailure(_model_response(response))
            return _model_response(response)
        except JsonWireFailure as exc:
            raise ModelWireFailure(_model_response(exc.response)) from None
        except JsonWireCancelled as exc:
            raise ModelWireCancelled(_model_response(exc.response)) from None


def _model_response(response: JsonResponse) -> ModelHttpResponse:
    return ModelHttpResponse(response.status, response.body, response.content_type)
