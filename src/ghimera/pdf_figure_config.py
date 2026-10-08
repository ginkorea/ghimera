"""Explicit bounded PDF figure admission and offline rendering policy."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.page_transcription_config import PageRenderConfig
from ghimera.visual_types import Positive, VisualRecord


class PdfFigureConfig(VisualRecord):
    schema_version: Literal["ghimera.pdf-figures/1"] = Field(alias="schema")
    renderer: PageRenderConfig
    max_candidates: Positive
    max_figures: Positive
    max_layout_bytes: Positive
    max_crop_pixels: Positive
    max_crop_bytes: Positive
    candidate_terms: Annotated[tuple[str, ...], Field(min_length=1)]
    excluded_terms: tuple[str, ...]

    @model_validator(mode="after")
    def bounded(self) -> "PdfFigureConfig":
        if self.max_figures > self.max_candidates or any(
            not term.strip() or term != term.casefold()
            for term in self.candidate_terms + self.excluded_terms
        ):
            raise ValueError("PDF figure selection requires bounded, casefolded explicit terms")
        if self.max_crop_pixels > self.renderer.max_pixels_per_page:
            raise ValueError("crop pixels must fit the admitted page render")
        return self

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
