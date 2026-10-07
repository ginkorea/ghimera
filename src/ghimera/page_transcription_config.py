"""Explicit page rendering and private transcription policy, never model defaults."""

import hashlib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.model_config import ModelServiceConfig

Positive = Annotated[int, Field(strict=True, gt=0)]
Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class PageRenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.page-render/1"] = Field(alias="schema")
    worker_python: Path
    work_directory: Path
    renderer_package_version: str = Field(min_length=1)
    image_package_version: str = Field(min_length=1)
    scale: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_input_bytes: Positive
    max_pages: Positive
    max_pixels_per_page: Positive
    max_image_bytes: Positive
    max_total_image_bytes: Positive
    max_workers: Positive
    timeout_seconds: Seconds
    cleanup_timeout_seconds: Seconds
    max_output_bytes: Positive
    max_diagnostic_bytes: Positive

    @model_validator(mode="after")
    def bounded(self) -> "PageRenderConfig":
        if not self.worker_python.is_absolute() or not self.work_directory.is_absolute():
            raise ValueError("render worker and scratch paths must be explicit and absolute")
        if self.max_image_bytes > self.max_total_image_bytes:
            raise ValueError("each image must fit the aggregate image bound")
        return self

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()


class PageTranscriptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.page-transcription/1"] = Field(alias="schema")
    renderer: PageRenderConfig
    transcriber: ModelServiceConfig
    reviewer: ModelServiceConfig
    languages: Annotated[tuple[str, ...], Field(min_length=1)]
    max_lines: Positive
    max_text_chars: Positive
    max_uncertain_regions: Positive
    max_concurrent_pages: Positive

    @model_validator(mode="after")
    def explicit(self) -> "PageTranscriptionConfig":
        if len(set(self.languages)) != len(self.languages) or any(
            not value.strip() for value in self.languages
        ):
            raise ValueError("transcription language/script hints must be unique and nonblank")
        if any(
            service.response_format != "json_object"
            for service in (self.transcriber, self.reviewer)
        ):
            raise ValueError("page transcription/1 requires the explicit JSON object dialect")
        if (self.transcriber.model_id, self.transcriber.revision) == (
            self.reviewer.model_id,
            self.reviewer.revision,
        ):
            raise ValueError("transcription requires a separately identified review model")
        return self

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()


class PdfTranscriptionConfig(BaseModel):
    """Caller-selected fallback or full PDF review; never infer a script from a URL."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.pdf-transcription/1"] = Field(alias="schema")
    pages: PageTranscriptionConfig
    mode: Literal["always", "native_refused"]
    language_hint: str = Field(min_length=1)
    max_document_text_chars: Positive

    @model_validator(mode="after")
    def declared_language(self) -> "PdfTranscriptionConfig":
        if self.language_hint not in self.pages.languages:
            raise ValueError("PDF transcription requires a declared language/script hint")
        return self

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()
