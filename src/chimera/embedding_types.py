"""Audited embedding batches and version-bound shelf reference vectors."""

import hashlib
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chimera.model_config import EmbeddingServiceConfig

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
Vector = Annotated[tuple[Annotated[float, Field(allow_inf_nan=False)], ...], Field(min_length=1)]


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
