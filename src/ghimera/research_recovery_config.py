"""Explicit policy for native model-boundary research recovery."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceCompletionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.source-completion-recovery/1"] = Field(alias="schema")
    execution: Literal["serial"]
    max_capsule_bytes: Annotated[int, Field(strict=True, gt=0)]


class ResearchRecoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.research-recovery/1", "ghimera.research-recovery/2"] = Field(
        alias="schema"
    )
    max_snapshot_bytes: Annotated[int, Field(strict=True, gt=0)]
    clock_policy: Literal["include_downtime"]
    tail_policy: Literal["acknowledged_model_return"]
    source_completion: SourceCompletionPolicy | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def versioned(self) -> "ResearchRecoveryConfig":
        if (self.schema_version == "ghimera.research-recovery/2") != (
            self.source_completion is not None
        ):
            raise ValueError("source completion requires explicit recovery /2 policy")
        return self
