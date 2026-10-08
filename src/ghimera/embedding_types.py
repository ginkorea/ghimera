"""Audited embedding batches and version-bound shelf reference vectors."""

import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.model_config import EmbeddingServiceConfig

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
Vector = Annotated[tuple[Annotated[float, Field(allow_inf_nan=False)], ...], Field(min_length=1)]


def encoding_request(service: EmbeddingServiceConfig, texts: tuple[str, ...]) -> bytes:
    """The canonical native wire request, shared with durable invocation admission."""
    inputs = tuple(service.text_prefix + text for text in texts)
    request: dict[str, str | list[str] | int] = {
        "model": service.served_model,
        "input": list(inputs),
        "encoding_format": "float",
    }
    if service.request_dimensions:
        request["dimensions"] = service.dimensions
    return json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


class EncodingIntent(BaseModel):
    """Exact pre-contact inputs; unknown intent is never evidence of a remote ACK."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.encoding-intent/1"] = Field(alias="schema")
    corpus_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    generation: Count
    purpose: Literal["passage", "query"]
    service: EmbeddingServiceConfig
    texts: Annotated[tuple[str, ...], Field(min_length=1)]
    request_sha256: Digest

    @model_validator(mode="after")
    def exact_request(self) -> "EncodingIntent":
        inputs = tuple(self.service.text_prefix + text for text in self.texts)
        request = encoding_request(self.service, self.texts)
        if (
            any(not text.strip() for text in self.texts)
            or len(inputs) > self.service.max_batch_texts
            or sum(map(len, inputs)) > self.service.max_input_chars
            or any(len(text) > self.service.max_text_chars for text in inputs)
            or len(request) > self.service.max_request_bytes
        ):
            raise ValueError("encoding intent exceeds the configured input allowance")
        if hashlib.sha256(request).hexdigest() != self.request_sha256:
            raise ValueError("encoding intent does not bind the exact request")
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    @property
    def input_chars(self) -> int:
        return sum(len(self.service.text_prefix) + len(text) for text in self.texts)

    def validate_call(self, call: "EncodingCall") -> None:
        if (
            call.service != self.service
            or call.request_sha256 != self.request_sha256
            or call.input_sha256
            != tuple(
                hashlib.sha256((self.service.text_prefix + text).encode()).hexdigest()
                for text in self.texts
            )
            or call.input_chars != self.input_chars
        ):
            raise ValueError("encoding acknowledgement does not match its reserved intent")


class EncodingRecoveryState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.encoding-recovery-state/1"] = Field(alias="schema")
    reserved_calls: Count
    reserved_input_chars: Count
    stored_bytes: Count
    acknowledged: Count
    unresolved: Count


class EncodingRecoveryEvidence(BaseModel):
    """Original audit lineage and whether this operation reused its acknowledged vectors."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.encoding-recovery-evidence/1"] = Field(alias="schema")
    invocation_sha256: Digest
    original_call_id: Annotated[int, Field(strict=True, gt=0)]
    generation: Count
    reused: bool


def unit_vector(vector: tuple[float, ...]) -> tuple[float, ...]:
    """Scale first: finite large components must not overflow their norm."""
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("embedding vectors must be nonempty and finite")
    scale = max(abs(value) for value in vector)
    if scale == 0:
        raise ValueError("zero vectors have no cosine similarity")
    scaled = tuple(value / scale for value in vector)
    norm = math.sqrt(math.fsum(value * value for value in scaled))
    return tuple(value / norm for value in scaled)


class EmbeddingUsage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)
    prompt_tokens: Count
    total_tokens: Count
    completion_tokens: Literal[0] = 0

    @model_validator(mode="after")
    def reconciled(self) -> "EmbeddingUsage":
        if self.total_tokens != self.prompt_tokens:
            raise ValueError("embedding usage must reconcile without generation")
        return self


class EncodingCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.encoding-call/1"] = Field(alias="schema")
    service: EmbeddingServiceConfig
    request_sha256: Digest
    response_sha256: Digest
    response_bytes: Count
    input_sha256: Annotated[tuple[Digest, ...], Field(min_length=1)]
    input_chars: Count
    status: int | None
    latency_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    usage: EmbeddingUsage | None
    outcome: Literal["success", "refused", "cancelled"]
    telemetry: Literal["observed", "unavailable"] = "observed"

    @model_validator(mode="after")
    def missing_telemetry(self) -> "EncodingCall":
        if self.telemetry == "unavailable" and (
            self.outcome == "success"
            or self.usage is not None
            or self.status is not None
            or self.response_bytes != 0
        ):
            raise ValueError("an unavailable adapter cannot claim successful response spend")
        return self


class EncodingBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    vectors: tuple[Vector, ...]
    call: EncodingCall

    @model_validator(mode="after")
    def bound(self) -> "EncodingBatch":
        if self.call.outcome != "success" or len(self.vectors) != len(self.call.input_sha256):
            raise ValueError("successful encoding must bind every input exactly once")
        if any(len(vector) != self.call.service.dimensions for vector in self.vectors):
            raise ValueError("encoding dimensions must match the configured model")
        for vector in self.vectors:
            unit_vector(vector)
        return self


class ReferenceChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: Annotated[str, Field(min_length=1)]
    text_sha256: Digest
    vector: Vector


class EmbeddingReferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.embedding-references/1"] = Field(alias="schema")
    model_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(min_length=1)]
    text_prefix: str
    dimensions: Annotated[int, Field(strict=True, gt=0)]
    chunks: Annotated[tuple[ReferenceChunk, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def shape(self) -> "EmbeddingReferences":
        if not self.model_id.strip() or not self.revision.strip():
            raise ValueError("reference model identity must not be blank")
        if len({(item.source_id, item.text_sha256) for item in self.chunks}) != len(self.chunks):
            raise ValueError("reference chunks must be distinct")
        for chunk in self.chunks:
            if len(chunk.vector) != self.dimensions:
                raise ValueError("reference dimensions must match their model")
            unit_vector(chunk.vector)
        return self

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class IntentReferenceEvidence(BaseModel):
    """A run's original intent, encoded by its configured model with recorded spend."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.intent-reference/1"] = Field(alias="schema")
    goal_sha256: Digest
    encoding_sequence: Count
    references: EmbeddingReferences

    def validate_binding(
        self, goal_text: str, service: EmbeddingServiceConfig, call: EncodingCall
    ) -> None:
        goal_hash = hashlib.sha256(goal_text.encode()).hexdigest()
        references = self.references
        if (
            self.goal_sha256 != goal_hash
            or len(references.chunks) != 1
            or references.chunks[0].source_id != "intent:" + goal_hash
            or references.chunks[0].text_sha256 != goal_hash
            or (
                references.model_id,
                references.revision,
                references.dimensions,
                references.text_prefix,
            )
            != (service.model_id, service.revision, service.dimensions, service.text_prefix)
            or call.service != service
            or call.outcome != "success"
            or call.input_sha256
            != (hashlib.sha256((service.text_prefix + goal_text).encode()).hexdigest(),)
            or call.input_chars != len(service.text_prefix) + len(goal_text)
        ):
            raise ValueError(
                "intent references must bind the original goal and its successful encoding"
            )
