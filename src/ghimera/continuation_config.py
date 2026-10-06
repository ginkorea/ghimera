"""One explicit policy for round-boundary research persistence."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContinuationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.continuation/1"] = Field(alias="schema")
    max_checkpoint_bytes: Annotated[int, Field(strict=True, gt=0)]
    # A restart never grants free time. Pauses count against the original run.
    clock_policy: Literal["include_downtime"]
