"""Client-authored parsing provenance beside preserved source metadata."""

import hashlib
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chimera.extraction_attempts import HtmlExtractionAttempt, validate_chain
from chimera.locator_types import LocatorEvent as LocatorEvent
from chimera.locator_types import LocatorHealth

if TYPE_CHECKING:
    from chimera.extraction_config import ExtractionConfig

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ExtractionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.extraction-evidence/1"] = Field(alias="schema")
    source_sha256: Digest
    source_url: str
    text_sha256: Digest
    config_digest: Digest
    parser_revision: str
    encoding: str
    decode_replacements: Annotated[int, Field(strict=True, ge=0)]
    selection: Literal["profile", "relocated", "generic"]
    profile_id: str | None
    locators: tuple[LocatorEvent, ...]
    missing_fields: tuple[str, ...]
    declared_language: str | None
    language_confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    language_margin: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    language_sample_chars: Annotated[int, Field(strict=True, ge=0)]
    language_hint_disagrees: bool
    raw_markdown_sha256: Digest
    omitted_links: Annotated[int, Field(strict=True, ge=0)]
    rendered_sha256: Digest | None = None
    locator_health: LocatorHealth | None = Field(default=None, exclude_if=lambda v: v is None)
    attempts: tuple[HtmlExtractionAttempt, ...] = Field(default=(), exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def attempt_binding(self) -> "ExtractionEvidence":
        if self.attempts:
            validate_chain(self.attempts)
            if self.attempts[-1].outcome != "success" or any(
                (
                    a.source_url,
                    a.source_sha256,
                    a.rendered_sha256,
                    a.config_digest,
                    a.parser_revision,
                )
                != (
                    self.source_url,
                    self.source_sha256,
                    self.rendered_sha256,
                    self.config_digest,
                    self.parser_revision,
                )
                for a in self.attempts
            ):
                raise ValueError(
                    "extraction must bind its successful, source-qualified parse chain"
                )
        return self

    def validate_policy(self, config: "ExtractionConfig") -> None:
        if self.config_digest != config.content_digest():
            raise ValueError("extraction must bind its effective policy")
        for attempt in self.attempts:
            attempt.validate_policy(config)
        health = self.locator_health
        if health is None:
            return
        profile = next((p for p in config.profiles if p.host == health.host), None)
        policy = config.locator_drift
        if (
            profile is None
            or policy is None
            or health.host != urlsplit(self.source_url).hostname
            or health.profile_id != self.profile_id
            or profile.profile_id != self.profile_id
            or health.profile_digest
            != hashlib.sha256(profile.model_dump_json().encode()).hexdigest()
            or health.policy_digest != hashlib.sha256(policy.model_dump_json().encode()).hexdigest()
            or health.miss_limit != policy.consecutive_miss_limit
        ):
            raise ValueError(
                "locator health must bind the exact configured publisher/profile/policy"
            )
