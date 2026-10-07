"""Operator-owned request pacing; randomness never lowers a site's delay floor."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CadenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.cadence/1"] = Field(alias="schema")
    jitter_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    throttle_statuses: Annotated[
        tuple[Annotated[int, Field(strict=True, ge=400, le=599)], ...], Field(min_length=1)
    ]
    throttle_base_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    throttle_multiplier: Annotated[float, Field(ge=1, allow_inf_nan=False)]
    max_backoff_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    respect_retry_after: bool

    @model_validator(mode="after")
    def coherent(self) -> "CadenceConfig":
        if len(set(self.throttle_statuses)) != len(self.throttle_statuses):
            raise ValueError("throttle statuses must be distinct")
        if self.throttle_base_seconds > self.max_backoff_seconds:
            raise ValueError("initial throttle delay exceeds the configured cap")
        return self
