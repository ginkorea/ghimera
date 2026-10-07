"""Explicit source backpressure and independent expensive-stage capacities."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Capacity = Annotated[int, Field(strict=True, gt=0)]


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.execution/1"] = Field(alias="schema")
    active_sources: Capacity
    extraction_workers: Capacity
    scoring_workers: Capacity
    judge_workers: Capacity
    semantic_workers: Capacity
    visual_workers: Capacity
