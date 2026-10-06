"""Bounded self-hosted embedding client over the shared private model transport."""

import asyncio
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from chimera.embedding_types import EmbeddingUsage, EncodingBatch, EncodingCall, Vector
from chimera.model_config import EmbeddingServiceConfig
from chimera.model_http import (
    ModelHttpPort,
    ModelHttpResponse,
    ModelWireCancelled,
    ModelWireFailure,
    PinnedModelHttp,
)
from chimera.models import ModelIdentity
from chimera.refusals import (
    ChimeraRefused,
    EncodingCancelled,
    EncodingFailure,
    RefusalCode,
)
from chimera.transport import Resolver


class WireEmbedding(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    index: Annotated[int, Field(strict=True, ge=0)]
    object: Literal["embedding"]
    embedding: Vector


class WireEmbeddings(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    object: Literal["list"]
    model: str
    data: tuple[WireEmbedding, ...]
    usage: EmbeddingUsage | None = None


class SelfHostedEncoder:
    def __init__(
        self,
        config: EmbeddingServiceConfig,
        *,
        credential: SecretStr | None = None,
        resolver: Resolver | None = None,
        http: ModelHttpPort | None = None,
    ) -> None:
        self._config = config
        self._http = http or PinnedModelHttp(config, credential=credential, resolver=resolver)
        if self._http.config != config:
            raise ValueError("encoder transport must share the exact recorded service policy")
        if http is not None and credential is not None:
            raise ValueError("injected encoder transports own their credential boundary")

    @property
    def config(self) -> EmbeddingServiceConfig:
        return self._config

    @property
    def model(self) -> ModelIdentity:
        return ModelIdentity(
            model_id=self.config.model_id, revision=self.config.revision, location="self_hosted"
        )

    async def encode(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Compatibility port. Run-owned scoring uses encode_batch for call evidence."""
        return (await self.encode_batch(texts)).vectors

    async def encode_batch(self, texts: tuple[str, ...]) -> EncodingBatch:
        service = self.config
        # Reject empty input before call evidence: there is no attempted call to account for.
        if not texts or any(not text.strip() for text in texts):
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        started = asyncio.get_running_loop().time()
        inputs = tuple(service.text_prefix + text for text in texts)
        input_chars = sum(map(len, inputs))
        body = b""
        response = ModelHttpResponse(None, b"", "")
        usage = None

        def evidence(outcome: Literal["success", "refused", "cancelled"]) -> EncodingCall:
            return EncodingCall(
                schema="chimera.encoding-call/1",
                service=service,
                request_sha256=hashlib.sha256(body).hexdigest(),
                response_sha256=hashlib.sha256(response.body).hexdigest(),
                response_bytes=len(response.body),
                input_sha256=tuple(hashlib.sha256(text.encode()).hexdigest() for text in inputs),
                input_chars=input_chars,
                status=response.status,
                latency_seconds=max(0.0, asyncio.get_running_loop().time() - started),
                usage=usage,
                outcome=outcome,
            )

        try:
            if (
                len(inputs) > service.max_batch_texts
                or input_chars > service.max_input_chars
                or any(len(text) > service.max_text_chars for text in inputs)
            ):
                raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            request: dict[str, str | list[str] | int] = {
                "model": service.served_model,
                "input": list(inputs),
                "encoding_format": "float",
            }
            if service.request_dimensions:
                request["dimensions"] = service.dimensions
            body = json.dumps(
                request, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode()
            if len(body) > service.max_request_bytes:
                raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            response = await self._http.post(body)
            if len(response.body) > service.max_response_bytes:
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            if response.status != 200 or response.content_type != "application/json":
                raise ChimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
            wire = WireEmbeddings.model_validate_json(response.body)
            usage = wire.usage
            if (
                wire.model != service.served_model
                or len(wire.data) != len(inputs)
                or {item.index for item in wire.data} != set(range(len(inputs)))
                or (service.require_usage and usage is None)
            ):
                raise ChimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
            # Wire entries may arrive out of order; index, never arrival order, binds text.
            vectors = tuple(
                item.embedding for item in sorted(wire.data, key=lambda item: item.index)
            )
            return EncodingBatch(vectors=vectors, call=evidence("success"))
        except ModelWireFailure as exc:
            response = exc.response
            raise EncodingFailure(RefusalCode.MODEL_UNAVAILABLE, evidence("refused")) from None
        except ModelWireCancelled as exc:
            response = exc.response
            raise EncodingCancelled(evidence("cancelled")) from None
        except asyncio.CancelledError:
            raise EncodingCancelled(evidence("cancelled")) from None
        except (ChimeraRefused, ValidationError, ValueError) as exc:
            code = exc.code if isinstance(exc, ChimeraRefused) else RefusalCode.MODEL_UNAVAILABLE
            raise EncodingFailure(code, evidence("refused")) from None
