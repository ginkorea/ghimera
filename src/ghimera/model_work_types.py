"""Run-bound invocation evidence; a port input hash is not an HTTP request hash."""

import base64
import binascii
import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from ghimera.model_types import ModelCallEvidence

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
ModelPhase = (
    Literal[
        "plan",
        "assessment",
        "answer",
        "review",
        "verdict",
        "grade",
        "semantic_extract",
        "semantic_review",
        "visual_model",
        "transcription_model",
        "identity_propose",
        "identity_review",
    ]
    | SkipJsonSchema[Literal["reranking"]]
)


class ModelResultsConfig(BaseModel):
    """Optional private result retention, owned by the existing run journal."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.model-results/1"] = Field(alias="schema")
    max_result_bytes: Annotated[int, Field(strict=True, gt=0)]
    max_total_result_bytes: Annotated[int, Field(strict=True, gt=0)]

    @model_validator(mode="after")
    def bounded(self) -> "ModelResultsConfig":
        if self.max_result_bytes > self.max_total_result_bytes:
            raise ValueError("one retained result cannot exceed the total result budget")
        return self


class ModelWorkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.model-work/1"] = Field(alias="schema")
    max_input_bytes: Annotated[int, Field(strict=True, gt=0)]
    max_unanswered_calls: Annotated[int, Field(strict=True, gt=0)]
    uncertain_policy: Literal["hold"]
    results: ModelResultsConfig | None = Field(default=None, exclude_if=lambda v: v is None)


class ModelStoredWire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    status: Annotated[int, Field(strict=True, ge=100, le=599)]
    content_type: str


class ModelStoredOutput(BaseModel):
    """The exact observed bytes, not a rewritten wire envelope or approved claim."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.model-stored-output/1"] = Field(alias="schema")
    body_base64: str
    wire: ModelStoredWire | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def canonical_bytes(self) -> "ModelStoredOutput":
        data = self.body()
        if base64.b64encode(data).decode("ascii") != self.body_base64:
            raise ValueError("retained model bytes require canonical base64")
        return self

    def body(self) -> bytes:
        try:
            return base64.b64decode(self.body_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid retained model bytes") from exc


class ModelReplay(BaseModel):
    """A local read of one acknowledged result, never another remote reservation."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.model-replay/1"] = Field(alias="schema")
    intent_sequence: Count
    ack_sequence: Count
    output_scope: Literal["port_output", "wire_response"]
    output_sha256: Digest
    output_bytes: Count


class ModelIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.model-intent/1"] = Field(alias="schema")
    phase: ModelPhase
    input_scope: Literal["port_input", "wire_request"]
    input_sha256: Digest
    input_bytes: Count
    judge_reservation: Annotated[int, Field(strict=True, gt=0)]


class ModelAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.model-ack/1"] = Field(alias="schema")
    intent_sequence: Count
    outcome: Literal["returned", "refused", "cancelled", "failed"]
    output_scope: Literal["port_output", "wire_response"] | None
    output_sha256: Digest | None
    output_bytes: Count | None
    refused_call: ModelCallEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    stored_output: ModelStoredOutput | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def observed_output(self) -> "ModelAcknowledgement":
        shape = (self.output_scope, self.output_sha256, self.output_bytes)
        if any(value is None for value in shape) and any(value is not None for value in shape):
            raise ValueError("an output observation requires its exact scope, hash and size")
        if self.outcome == "returned" and self.output_sha256 is None:
            raise ValueError("a returned port result needs an observed output")
        if self.refused_call is not None and (
            self.outcome != "refused"
            or self.output_scope != "wire_response"
            or self.refused_call.status is None
            or self.refused_call.completion is None
            or self.refused_call.outcome != "refused"
            or self.output_sha256 != self.refused_call.response_sha256
            or self.output_bytes != self.refused_call.response_bytes
        ):
            raise ValueError("known refused output needs its observed completion evidence")
        if self.stored_output is not None:
            body = self.stored_output.body()
            if (
                self.outcome != "returned"
                or len(body) != self.output_bytes
                or hashlib.sha256(body).hexdigest() != self.output_sha256
                or (self.output_scope == "wire_response") != (self.stored_output.wire is not None)
            ):
                raise ValueError("retained result must match its original observation and scope")
        return self

    @property
    def uncertain(self) -> bool:
        # Ended locally does not mean a request was never received or billed.
        # Refused/cancelled ports can still have an unknown remote outcome.
        return self.outcome != "returned" and self.refused_call is None
