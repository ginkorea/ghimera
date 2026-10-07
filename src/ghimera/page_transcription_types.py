"""Generated transcription is distinct from native OCR observations and confidence."""

import hashlib
import struct
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.model_config import ModelServiceConfig
from ghimera.model_types import TokenUsage
from ghimera.page_transcription_config import PageTranscriptionConfig
from ghimera.visual_types import ImageRegion

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(strict=True, gt=0)]
Index = Annotated[int, Field(strict=True, ge=0)]


class TranscriptionRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class RenderedPdfPage(TranscriptionRecord):
    schema_version: Literal["ghimera.rendered-pdf-page/1"] = Field(alias="schema")
    source_sha256: Digest
    policy_sha256: Digest
    page_index: Index
    page_count: Positive
    width: Positive
    height: Positive
    scale: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    image_sha256: Digest
    png: bytes

    @model_validator(mode="after")
    def image_binding(self) -> "RenderedPdfPage":
        if self.page_index >= self.page_count:
            raise ValueError("rendered page index must be within the actual document")
        if hashlib.sha256(self.png).hexdigest() != self.image_sha256:
            raise ValueError("page render must bind its exact retained pixels")
        if (
            len(self.png) < 33
            or self.png[:8] != b"\x89PNG\r\n\x1a\n"
            or self.png[12:16] != b"IHDR"
            or struct.unpack(">II", self.png[16:24]) != (self.width, self.height)
        ):
            raise ValueError("page render must bind PNG dimensions, not source claims")
        return self


class RenderedPdf(TranscriptionRecord):
    source_sha256: Digest
    policy_sha256: Digest
    pages: Annotated[tuple[RenderedPdfPage, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def complete_pages(self) -> "RenderedPdf":
        if any(
            page.source_sha256 != self.source_sha256
            or page.policy_sha256 != self.policy_sha256
            or page.page_count != len(self.pages)
            or page.page_index != index
            for index, page in enumerate(self.pages)
        ):
            raise ValueError("renders must retain every source page once in source order")
        return self


class UncertainPageRegion(TranscriptionRecord):
    region: ImageRegion
    reason: str = Field(min_length=1)


class PageTranscriptionProposal(TranscriptionRecord):
    image_sha256: Digest
    lines: tuple[Annotated[str, Field(min_length=1)], ...]
    uncertain_regions: tuple[UncertainPageRegion, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class PageTranscriptionReview(TranscriptionRecord):
    image_sha256: Digest
    proposal_sha256: Digest
    accepted_line_indices: tuple[Index, ...]
    uncertain_line_indices: tuple[Index, ...]
    omitted_regions: tuple[UncertainPageRegion, ...]

    @model_validator(mode="after")
    def unique_lines(self) -> "PageTranscriptionReview":
        accepted, uncertain = self.accepted_line_indices, self.uncertain_line_indices
        if len(set(accepted)) != len(accepted) or len(set(uncertain)) != len(uncertain):
            raise ValueError("each line review must be unique")
        if set(accepted) & set(uncertain):
            raise ValueError("a line cannot be both accepted and uncertain")
        return self


class PageTranscriptionCall(TranscriptionRecord):
    role: Literal["transcription", "review"]
    service: ModelServiceConfig
    image_sha256: Digest
    request_sha256: Digest
    response_sha256: Digest | None
    response_bytes: Index
    usage: TokenUsage | None
    outcome: Literal["success", "refused", "cancelled"]
    finish_reason: str | None


class ReviewedPageTranscription(TranscriptionRecord):
    schema_version: Literal["ghimera.reviewed-page-transcription/1"] = Field(alias="schema")
    page: RenderedPdfPage
    config: PageTranscriptionConfig
    policy_sha256: Digest
    language_hint: str = Field(min_length=1)
    proposal: PageTranscriptionProposal
    review: PageTranscriptionReview
    calls: tuple[PageTranscriptionCall, PageTranscriptionCall]

    @model_validator(mode="after")
    def exact_review(self) -> "ReviewedPageTranscription":
        if (
            self.policy_sha256 != self.config.content_digest()
            or self.page.policy_sha256 != self.config.renderer.content_digest()
            or self.language_hint not in self.config.languages
            or self.page.page_count > self.config.renderer.max_pages
            or self.page.scale != self.config.renderer.scale
            or self.page.width * self.page.height > self.config.renderer.max_pixels_per_page
            or len(self.page.png) > self.config.renderer.max_image_bytes
            or self.calls[0].service != self.config.transcriber
            or self.calls[1].service != self.config.reviewer
            or len(self.proposal.lines) > self.config.max_lines
            or len(self.proposal.text) > self.config.max_text_chars
            or len(self.proposal.uncertain_regions) > self.config.max_uncertain_regions
            or len(self.review.omitted_regions) > self.config.max_uncertain_regions
            or self.proposal.image_sha256 != self.page.image_sha256
            or self.review.image_sha256 != self.page.image_sha256
            or self.review.proposal_sha256 != self.proposal.content_digest()
            or tuple(call.role for call in self.calls) != ("transcription", "review")
            or any(
                call.image_sha256 != self.page.image_sha256 or call.outcome != "success"
                for call in self.calls
            )
            or set(self.review.accepted_line_indices + self.review.uncertain_line_indices)
            != set(range(len(self.proposal.lines)))
        ):
            raise ValueError("transcription requires exact image, proposal and complete review")
        return self

    @property
    def accepted(self) -> bool:
        return bool(self.proposal.lines) and not (
            self.proposal.uncertain_regions
            or self.review.uncertain_line_indices
            or self.review.omitted_regions
        )

    @property
    def text(self) -> str | None:
        return self.proposal.text if self.accepted else None
