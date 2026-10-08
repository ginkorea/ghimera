"""Run-scoped encoding proof; deliberately unrelated to corpus generations."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.embedding_types import EncodingBatch, EncodingCall, encoding_request
from ghimera.judgment_types import ScoringSourceBinding
from ghimera.model_config import EmbeddingServiceConfig

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
Positive = Annotated[int, Field(strict=True, gt=0)]


class RunEncodingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class RunEncodingRecoveryConfig(RunEncodingRecord):
    schema_version: Literal["ghimera.run-encoding-recovery/1"] = Field(alias="schema")
    max_result_bytes: Positive
    max_total_result_bytes: Positive
    unknown_policy: Literal["hold"]

    @model_validator(mode="after")
    def bounds(self) -> "RunEncodingRecoveryConfig":
        if self.max_result_bytes > self.max_total_result_bytes:
            raise ValueError("one vector result cannot exceed the total retained allowance")
        return self


class RunEncodingDecision(RunEncodingRecord):
    """Explicit invocation authority, never a text/hash-based replay lookup."""

    mode: Literal["fresh", "replay"]
    original_intent_sequence: Count | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def exact(self) -> "RunEncodingDecision":
        if (self.mode == "replay") != (self.original_intent_sequence is not None):
            raise ValueError("replay requires an original sequence; fresh forbids it")
        return self


class RunEncodingScope(RunEncodingRecord):
    """Complete ordered native scoring inputs and original parser/reading identity."""

    goal_text: Annotated[str, Field(min_length=1)]
    document_sha256: Digest
    canonical_url: str | None
    source: ScoringSourceBinding
    windows: Annotated[tuple[tuple[Count, Positive], ...], Field(min_length=1)]
    link_inputs: tuple[str, ...]


class RunEncodingIntent(RunEncodingRecord):
    schema_version: Literal["ghimera.run-encoding-intent/1"] = Field(alias="schema")
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    header_sha256: Digest
    scoring_sha256: Digest
    purpose: Literal["intent", "source"]
    scope: RunEncodingScope
    service: EmbeddingServiceConfig
    texts: Annotated[tuple[str, ...], Field(min_length=1)]
    batch_start: Count
    request_sha256: Digest
    reserved_call: Positive
    reserved_chars: Positive

    @model_validator(mode="after")
    def exact_request(self) -> "RunEncodingIntent":
        inputs = tuple(self.service.text_prefix + text for text in self.texts)
        body = encoding_request(self.service, self.texts)
        if (
            any(not text.strip() for text in self.texts)
            or len(inputs) > self.service.max_batch_texts
            or sum(map(len, inputs)) > self.service.max_input_chars
            or any(len(text) > self.service.max_text_chars for text in inputs)
            or len(body) > self.service.max_request_bytes
            or hashlib.sha256(body).hexdigest() != self.request_sha256
        ):
            raise ValueError("run encoding must bind a bounded exact original request")
        return self

    @property
    def input_chars(self) -> int:
        return sum(len(self.service.text_prefix) + len(text) for text in self.texts)

    def validate_call(self, call: EncodingCall) -> None:
        if (
            call.service != self.service
            or call.request_sha256 != self.request_sha256
            or call.input_chars != self.input_chars
            or call.input_sha256
            != tuple(
                hashlib.sha256((self.service.text_prefix + text).encode()).hexdigest()
                for text in self.texts
            )
        ):
            raise ValueError("vector acknowledgement changed its original run encoding request")


class RunEncodingAcknowledgement(RunEncodingRecord):
    schema_version: Literal["ghimera.run-encoding-ack/1"] = Field(alias="schema")
    original_intent_sequence: Count
    intent_sha256: Digest
    result: EncodingBatch
    result_sha256: Digest

    @model_validator(mode="after")
    def exact_result(self) -> "RunEncodingAcknowledgement":
        if hashlib.sha256(self.result.model_dump_json().encode()).hexdigest() != self.result_sha256:
            raise ValueError("vector ACK must retain its exact original validated batch")
        return self


class RunEncodingReplay(RunEncodingRecord):
    schema_version: Literal["ghimera.run-encoding-replay/1"] = Field(alias="schema")
    original_intent_sequence: Count
    original_ack_sequence: Count
    intent_sha256: Digest
    ack_sha256: Digest
