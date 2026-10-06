"""Versioned operator policy for parsing retained bytes, not another crawler."""

import hashlib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ghimera.extraction_attempts import ExtractionRecoveryPolicy
from ghimera.locator_types import LocatorDriftPolicy

Positive = Annotated[int, Field(strict=True, gt=0)]
Text = Annotated[str, Field(min_length=1)]


class LocatorProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    host: Annotated[str, Field(pattern=r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")]
    profile_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$")]
    body: Text
    title: Text | None = None
    byline: Text | None = None
    date: Text | None = None

    @field_validator("body", "title", "byline", "date")
    @classmethod
    def selectors(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("locators must be nonblank, single-line CSS selectors")
        return value


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.extraction/1"] = Field(alias="schema")
    work_directory: Path
    locator_directory: Path
    max_input_bytes: Positive
    max_output_bytes: Positive
    max_diagnostic_bytes: Positive
    max_text_chars: Positive
    max_links: Positive
    max_workers: Positive
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    cleanup_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    default_encoding: Text
    pruning_threshold: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    pruning_threshold_type: Literal["fixed", "dynamic"]
    min_word_threshold: Annotated[int, Field(strict=True, ge=0)]
    locator_min_percent: Annotated[int, Field(strict=True, ge=0, le=100)]
    languages: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[a-z]{2}$")], ...], Field(min_length=2)
    ]
    language_max_chars: Positive
    language_min_chars: Positive
    language_min_confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    language_min_margin: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    generic_body_selectors: Annotated[tuple[Text, ...], Field(min_length=1)]
    excluded_tags: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*$")], ...], Field(min_length=1)
    ]
    profiles: tuple[LocatorProfile, ...] = ()
    locator_drift: LocatorDriftPolicy | None = Field(default=None, exclude_if=lambda v: v is None)
    recovery: ExtractionRecoveryPolicy | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def coherent(self) -> "ExtractionConfig":
        if not self.work_directory.is_absolute() or not self.locator_directory.is_absolute():
            raise ValueError("worker and locator directories must be explicit absolute paths")
        if self.work_directory == self.locator_directory:
            raise ValueError("worker scratch and persistent locator state are distinct")
        if self.language_min_chars > self.language_max_chars:
            raise ValueError("language sample cannot be smaller than the minimum evidence")
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("language candidates cannot repeat")
        if len({profile.host for profile in self.profiles}) != len(self.profiles):
            raise ValueError("configure one unambiguous profile per exact host")
        return self

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()
