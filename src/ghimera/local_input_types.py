"""Explicit owned-file admission and immutable, path-free source provenance."""

import hashlib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(strict=True, gt=0)]
DocumentMime = Literal[
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]


class InputRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)


class LocalDocumentSeed(InputRecord):
    path: Path
    sha256: Digest
    content_type: DocumentMime

    @model_validator(mode="after")
    def explicit(self) -> "LocalDocumentSeed":
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise ValueError("local document paths must be explicit absolute paths")
        return self

    @property
    def source_id(self) -> str:
        return "urn:ghimera:local:" + self.sha256


class LocalInputConfig(InputRecord):
    schema_version: Literal["ghimera.local-inputs/1"] = Field(alias="schema")
    allowed_roots: Annotated[tuple[Path, ...], Field(min_length=1)]
    max_files_per_run: Positive
    max_input_bytes: Positive
    max_total_bytes: Positive

    @model_validator(mode="after")
    def explicit(self) -> "LocalInputConfig":
        if any(
            not root.is_absolute() or root == Path(root.anchor) or ".." in root.parts
            for root in self.allowed_roots
        ) or len(set(self.allowed_roots)) != len(self.allowed_roots):
            raise ValueError("declare distinct, absolute, non-root input directories")
        if self.max_input_bytes > self.max_total_bytes:
            raise ValueError("the per-file allowance cannot exceed the total input allowance")
        return self

    def permits(self, path: Path) -> bool:
        return (
            path.is_absolute()
            and ".." not in path.parts
            and any(path != root and path.is_relative_to(root) for root in self.allowed_roots)
        )

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()


class LocalInputEvidence(InputRecord):
    schema_version: Literal["ghimera.local-input-evidence/1"] = Field(alias="schema")
    source_id: str
    sha256: Digest
    size_bytes: Positive
    content_type: DocumentMime
    policy_digest: Digest
    reader_revision: Literal["bounded-local-file/1"]

    @model_validator(mode="after")
    def source_bound(self) -> "LocalInputEvidence":
        if self.source_id != "urn:ghimera:local:" + self.sha256:
            raise ValueError("local source identity must bind the pinned original bytes")
        return self

    def validate_policy(self, policy: LocalInputConfig | None) -> None:
        if (
            policy is None
            or self.policy_digest != policy.content_digest()
            or self.size_bytes > policy.max_input_bytes
        ):
            raise ValueError("local input must bind the effective admission policy")
