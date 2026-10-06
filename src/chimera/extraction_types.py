"""Client-authored parsing provenance beside preserved source metadata."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class LocatorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field: Literal["body", "title", "byline", "date"]
    status: Literal["direct", "relocated", "missing"]
    selector: str


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
