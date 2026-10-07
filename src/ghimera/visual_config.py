"""Operator-owned visual selection and offline OCR recipe."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.model_config import ModelServiceConfig

Positive = Annotated[int, Field(strict=True, gt=0)]


class VisualConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.visual/1"] = Field(alias="schema")
    max_candidates_per_page: Positive
    max_images_per_page: Positive
    max_image_bytes: Positive
    max_pixels: Positive
    min_width: Positive
    min_height: Positive
    max_ocr_chars: Positive
    max_ocr_spans: Positive
    min_word_confidence: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    excluded_tokens: Annotated[tuple[str, ...], Field(min_length=1)]
    candidate_terms: Annotated[tuple[str, ...], Field(min_length=1)]
    allowed_hosts: tuple[str, ...] = ()
    image_types: Annotated[
        tuple[Literal["image/png", "image/jpeg", "image/webp"], ...], Field(min_length=1)
    ]
    tesseract: Path
    tessdata_directory: Path
    languages: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[A-Za-z0-9_]+$")], ...], Field(min_length=1)
    ]
    language_routes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    fallback_languages: tuple[str, ...]
    worker_python: Path
    work_directory: Path
    max_workers: Positive
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    cleanup_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_output_bytes: Positive
    max_diagnostic_bytes: Positive
    page_segmentation: Annotated[int, Field(strict=True, ge=0, le=13)]
    cpu_threads: Positive
    vision: ModelServiceConfig | None = None
    reviewer: ModelServiceConfig | None = None

    @model_validator(mode="after")
    def coherent(self) -> "VisualConfig":
        if self.max_images_per_page > self.max_candidates_per_page:
            raise ValueError("image budget must fit candidate budget")
        if self.min_width * self.min_height > self.max_pixels:
            raise ValueError("minimum dimensions must fit decoded pixel limit")
        if any(
            not term.strip() or term != term.casefold()
            for term in self.excluded_tokens + self.candidate_terms
        ):
            raise ValueError("selection terms must be nonblank casefolded text")
        if any(
            not path.is_absolute()
            for path in (
                self.tesseract,
                self.tessdata_directory,
                self.worker_python,
                self.work_directory,
            )
        ):
            raise ValueError("OCR paths must be explicit absolute operator inputs")
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("OCR language packs must be unique")
        if not self.fallback_languages or any(
            not route or not set(route) <= set(self.languages)
            for route in (self.fallback_languages, *self.language_routes.values())
        ):
            raise ValueError("every OCR route must select declared language packs")
        if (self.vision is None) != (self.reviewer is None):
            raise ValueError("visual interpretation requires an explicit review service")
        if any(
            service.response_format != "json_object"
            for service in (self.vision, self.reviewer)
            if service is not None
        ):
            raise ValueError("visual service/1 requires the explicit json_object dialect")
        return self
