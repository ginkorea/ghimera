"""Non-secret references to conditional reuse of an immutable source version."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.source_refresh_config import SourceRefreshConfig, exact_http_url

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Stamp = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class SourceRefreshUse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.source-refresh-use/1"] = Field(alias="schema")
    policy_sha256: Digest
    record_sha256: Digest
    source_url: str
    source_sha256: Digest
    captured_at: Stamp
    checked_at: Stamp
    observation: Literal["conditional_304"]

    @model_validator(mode="after")
    def chronology(self) -> "SourceRefreshUse":
        exact_http_url(self.source_url)
        if self.checked_at < self.captured_at:
            raise ValueError("source revalidation cannot precede its original capture")
        return self

    def validate_policy(self, policy: SourceRefreshConfig | None, url: str, digest: str) -> None:
        if (
            policy is None
            or self.policy_sha256 != policy.identity
            or self.source_url != url
            or url not in policy.urls
            or self.source_sha256 != digest
            or self.checked_at - self.captured_at > policy.max_validator_age_seconds
        ):
            raise ValueError("conditional reuse must bind its exact policy, URL and original")
