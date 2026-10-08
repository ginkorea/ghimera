"""Run-bound invocation evidence; a port input hash is not an HTTP request hash."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.model_types import ModelCallEvidence

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
ModelPhase = Literal[
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
]


class ModelWorkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.model-work/1"] = Field(alias="schema")
    max_input_bytes: Annotated[int, Field(strict=True, gt=0)]
    max_unanswered_calls: Annotated[int, Field(strict=True, gt=0)]
    uncertain_policy: Literal["hold"]


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
        return self

    @property
    def uncertain(self) -> bool:
        # Ended locally does not mean a request was never received or billed.
        # Refused/cancelled ports can still have an unknown remote outcome.
        return self.outcome != "returned" and self.refused_call is None
