"""Non-secret challenge observations; cookies and tokens never enter evidence."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ghimera.challenge_config import ChallengeConfig, ChallengeProvider, exact_origin


class ChallengeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.challenge-evidence/1", "ghimera.challenge-evidence/2"] = Field(
        alias="schema"
    )
    origin: str
    provider: ChallengeProvider
    provider_version: str
    policy_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    cookie_names: tuple[str, ...]
    expires_at: Annotated[float, Field(gt=0, allow_inf_nan=False)]

    def validate_policy(self, policy: ChallengeConfig | None, url: str) -> None:
        if (
            policy is None
            or exact_origin(url) != self.origin
            or self.origin not in policy.allowed_origins
            or self.policy_digest != policy.content_digest()
            or self.provider != policy.provider
            or self.provider_version != policy.provider_version
            or self.schema_version
            != (
                "ghimera.challenge-evidence/1"
                if policy.schema_version == "ghimera.challenges/1"
                else "ghimera.challenge-evidence/2"
            )
            or not self.cookie_names
            or len(set(self.cookie_names)) != len(self.cookie_names)
            or not set(self.cookie_names) <= set(policy.allowed_cookie_names)
        ):
            raise ValueError("clearance evidence must bind its policy and exact origin")
