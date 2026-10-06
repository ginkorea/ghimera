"""Immutable model-call evidence, independent of source transport and credentials."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.model_config import ModelServiceConfig

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
ModelTask = Literal[
    "plan", "assessment", "answer", "review", "verdict", "grade", "semantic_extract"
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

    @property
    def total_tokens(self) -> int | None:
        return self.usage.total_tokens if self.usage is not None else None
