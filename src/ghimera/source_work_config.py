"""Explicit capacities for run-owned, durable source-operation evidence."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]


class SourceWorkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.source-work/1"] = Field(alias="schema")
    max_operations: Positive
    max_page_bytes: Positive
    max_result_bytes: Positive
    max_operation_bytes: Positive
    max_store_bytes: Positive
    database_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def capacity(self) -> "SourceWorkConfig":
        if (
            self.max_page_bytes + self.max_result_bytes >= self.max_operation_bytes
            or self.max_operation_bytes > self.max_store_bytes
        ):
            raise ValueError(
                "source-work capacity must admit the bounded original, result and envelope"
            )
        return self
