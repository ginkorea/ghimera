"""Versioned identity evidence: similarities are not calibrated probabilities."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ContentFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.content-fingerprint/1"] = Field(alias="schema")
    revision: Literal["unicode-char-simhash64@1"]
    source_sha256: Digest
    normalized_text_sha256: Digest
    simhash: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
    normalized_chars: Annotated[int, Field(strict=True, ge=0)]
    language: str
    canonical_url: str
    config_digest: Digest


class DedupEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.dedup-evidence/1"] = Field(alias="schema")
    reason: Literal["content_sha256", "normalized_text", "simhash"]
    representative_sha256: Digest
    current: ContentFingerprint
    hamming_distance: Annotated[int, Field(strict=True, ge=0, le=64)]
    config_digest: Digest


class ContentDrift(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.content-drift/1"] = Field(alias="schema")
    canonical_url: str
    previous_sha256: Digest
    current_sha256: Digest
    reason: Literal["changed_native_text", "changed_source_bytes"]
    config_digest: Digest
