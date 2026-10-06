"""Offline document conversion policy; native PDF and full layout are explicit."""

import hashlib
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chimera.document_models import PdfModels

Positive = Annotated[int, Field(strict=True, gt=0)]


class ParserArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Positive

    @model_validator(mode="after")
    def relative_file(self) -> "ParserArtifact":
        path = PurePosixPath(self.path)
        if not self.path or path.is_absolute() or ".." in path.parts or "\\" in self.path:
            raise ValueError(
                "artifact names must be relative paths under the configured model root"
            )
        return self


class DocumentExtractionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.document-extraction/1"] = Field(alias="schema")
    worker_python: Path
    work_directory: Path
    max_input_bytes: Positive
    max_output_bytes: Positive
    max_diagnostic_bytes: Positive
    max_text_chars: Positive
    max_links: Positive
    max_workers: Positive
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    cleanup_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_pages: Positive
    max_archive_entries: Positive
    max_expanded_bytes: Positive
    max_compression_ratio: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    cpu_threads: Positive
    pdf_pipeline: Literal["native", "standard"]
    pdf_models: PdfModels | None = Field(default=None, exclude_if=lambda value: value is None)
    artifacts_directory: Path | None = None
    artifacts: tuple[ParserArtifact, ...]
    do_ocr: bool
    do_table_structure: bool
    languages: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[a-z]{2}$")], ...], Field(min_length=2)
    ]
    language_max_chars: Positive
    language_min_chars: Positive
    language_min_confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    language_min_margin: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]

    @model_validator(mode="after")
    def coherent(self) -> "DocumentExtractionConfig":
        if not self.worker_python.is_absolute() or not self.work_directory.is_absolute():
            raise ValueError(
                "worker interpreter and scratch directory must be explicit absolute paths"
            )
        if self.language_min_chars > self.language_max_chars or len(set(self.languages)) != len(
            self.languages
        ):
            raise ValueError("language bounds and unique candidates are required")
        if self.pdf_pipeline == "standard" and (
            not self.artifacts or self.artifacts_directory is None or self.pdf_models is None
        ):
            raise ValueError(
                "standard PDF needs explicit model choices and an offline artifact manifest"
            )
        if self.pdf_pipeline == "native" and (
            self.artifacts
            or self.artifacts_directory is not None
            or self.do_ocr
            or self.pdf_models is not None
        ):
            raise ValueError(
                "native PDF conversion has no layout/OCR models; select standard explicitly"
            )
        if self.artifacts_directory is not None and not self.artifacts_directory.is_absolute():
            raise ValueError("artifact root must be absolute")
        if len({artifact.path for artifact in self.artifacts}) != len(self.artifacts):
            raise ValueError("artifact paths cannot repeat")
        if self.pdf_models is not None:
            if self.do_ocr != (self.pdf_models.ocr is not None):
                raise ValueError("enabled OCR requires exactly one explicit offline recognizer")
            expected = set(
                self.pdf_models.required_artifact_paths(with_tables=self.do_table_structure)
            )
            if not expected <= {artifact.path for artifact in self.artifacts}:
                raise ValueError("every selected PDF model/config file must be in the manifest")
        return self

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()

    def artifact_digest(self) -> str | None:
        if not self.artifacts:
            return None
        canonical = "\n".join(artifact.model_dump_json() for artifact in self.artifacts)
        return hashlib.sha256(canonical.encode()).hexdigest()
