"""Immutable model-call evidence, independent of source transport and credentials."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from ghimera.model_config import ModelServiceConfig

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
ModelTask = Literal[
    "plan",
    "assessment",
    "answer",
    "review",
    "verdict",
    "grade",
    "semantic_extract",
    "semantic_review",
]


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)
    prompt_tokens: Count
    completion_tokens: Count
    total_tokens: Count

    @model_validator(mode="after")
    def reconciled(self) -> "TokenUsage":
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("token spend must reconcile")
        return self


FinishReason = Literal["stop", "length", "content_filter", "tool_calls", "function_call", "other"]


class CompletionShape(BaseModel):
    """Non-secret protocol observations, never model output or reasoning text."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.completion-shape/1"] = Field(alias="schema")
    choices: Count
    model_matches: bool
    finish_reason: FinishReason | None
    final_content: Literal["missing", "empty", "present"] | None
    refusal_present: bool | None
    reasoning_present: bool | None

    @model_validator(mode="after")
    def single_choice(self) -> "CompletionShape":
        observed = (
            self.finish_reason,
            self.final_content,
            self.refusal_present,
            self.reasoning_present,
        )
        if self.choices == 1:
            if any(item is None for item in observed):
                raise ValueError("one completion choice requires its observed shape")
        elif any(item is not None for item in observed):
            raise ValueError("multiple or absent choices have no single completion shape")
        return self


class ModelCallEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.model-call/1"] = Field(alias="schema")
    service: ModelServiceConfig
    task: ModelTask
    prompt_revision: str
    request_sha256: Digest
    response_sha256: Digest
    response_bytes: Count
    status: int | None
    latency_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    usage: TokenUsage | None
    input_chars: Count
    context_sha256: Digest
    selected_spans: tuple[tuple[str, int, int], ...]
    omitted_document_ids: tuple[str, ...]
    omitted_chars: Count
    outcome: Literal["success", "refused", "cancelled"]
    # Client-owned telemetry is validated and replayed, but must not alter the
    # frozen model-facing JSON schemas of historical extraction/review recipes.
    completion: SkipJsonSchema[CompletionShape | None] = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @property
    def total_tokens(self) -> int | None:
        return self.usage.total_tokens if self.usage is not None else None
