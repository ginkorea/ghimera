"""Explicit policy for native model-boundary research recovery."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchRecoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.research-recovery/1"] = Field(alias="schema")
    max_snapshot_bytes: Annotated[int, Field(strict=True, gt=0)]
    clock_policy: Literal["include_downtime"]
    tail_policy: Literal["acknowledged_model_return"]
