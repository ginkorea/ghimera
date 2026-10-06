"""One parse boundary for operator configuration; no environment or hidden defaults."""

import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveInt = Annotated[int, Field(strict=True, gt=0)]
PositiveFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class ChimeraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    schema_version: Literal["chimera.config/1"] = Field(alias="schema")
    page_budget: PositiveInt
    byte_budget: PositiveInt
    wall_seconds: PositiveFloat
    judge_budget: PositiveInt
    grade_interval: PositiveInt
    grade_threshold: Probability
    saturation_window: PositiveInt
    saturation_min_new: PositiveInt
    max_links_per_page: PositiveInt
    min_link_score: Probability
    request_timeout_seconds: PositiveFloat
    per_host_delay_seconds: PositiveFloat
    per_host_concurrency: PositiveInt
    global_concurrency: PositiveInt
    global_requests_per_second: PositiveFloat
    retry_budget: Annotated[int, Field(strict=True, ge=0)]
    impersonation_profile: Annotated[str, Field(min_length=1)]
    user_agent: Annotated[str, Field(min_length=1)]
    egress_feature: Literal["crawl_egress"]
    model_policy: Literal["self_hosted_only"]

    @model_validator(mode="after")
    def consistent(self) -> "ChimeraConfig":
        if self.saturation_min_new > self.saturation_window:
            raise ValueError("saturation_min_new cannot exceed saturation_window")
        if self.per_host_concurrency > self.global_concurrency:
            raise ValueError("per_host_concurrency cannot exceed global_concurrency")
        if "\n" in self.user_agent or "\r" in self.user_agent:
            raise ValueError("user_agent must be one HTTP header line")
        return self

    @classmethod
    def from_toml(cls, path: Path) -> "ChimeraConfig":
        with path.open("rb") as stream:
            return cls.model_validate(tomllib.load(stream))
