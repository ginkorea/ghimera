"""Publisher-profile health contracts, independent of the parsing vendors."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LocatorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field: Literal["body", "title", "byline", "date"]
    status: Literal["direct", "relocated", "missing"]
    selector: str


class LocatorDriftPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.locator-drift-policy/1"] = Field(alias="schema")
    consecutive_miss_limit: Annotated[int, Field(strict=True, gt=0)]
    state_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class LocatorHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.locator-health/1"] = Field(alias="schema")
    host: str
    profile_id: str
    profile_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    policy_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    consecutive_misses: Annotated[int, Field(strict=True, ge=0)]
    completed_observations: Annotated[int, Field(strict=True, ge=0)]
    miss_limit: Annotated[int, Field(strict=True, gt=0)]
    generic_only: bool
    last_source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def coherent(self) -> "LocatorHealth":
        if self.generic_only != (self.consecutive_misses >= self.miss_limit):
            raise ValueError("locator drift must match the configured consecutive-miss bar")
        if self.completed_observations < self.consecutive_misses:
            raise ValueError("misses cannot exceed completed locator observations")
        if (self.completed_observations == 0) != (self.last_source_sha256 is None):
            raise ValueError("observed profile health must name its last retained source")
        return self

    @property
    def finding(self) -> Literal["ok", "locator_drift"]:
        return "locator_drift" if self.generic_only else "ok"
