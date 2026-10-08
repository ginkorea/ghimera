"""Visual evidence is not native page text or an unreviewed diagram claim."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(strict=True, gt=0)]


class VisualRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class ResponsiveSelection(VisualRecord):
    schema_version: Literal["ghimera.responsive-selection/1"] = Field(alias="schema")
    policy_sha256: Digest
    markup_sha256: Digest
    markup_encoding: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]+$")]
    attribute: Literal["src", "data-src", "srcset", "data-srcset"]
    attribute_sha256: Digest
    source_token: Annotated[str, Field(min_length=1)]
    width: Positive | None
    density: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None
    picture_source_index: Annotated[int, Field(strict=True, ge=0)] | None
    media: str
    sizes: str
    declared_type: str
    variants_seen: Positive
    omissions: tuple[str, ...]

    @model_validator(mode="after")
    def descriptor(self) -> ResponsiveSelection:
        if self.width is not None and self.density is not None:
            raise ValueError("one source cannot claim both width and density descriptors")
        if self.attribute in {"src", "data-src"} and any(
            value is not None for value in (self.width, self.density, self.picture_source_index)
        ):
            raise ValueError("fallback URLs cannot claim srcset or picture descriptors")
        return self


class ImageCandidate(VisualRecord):
    url: str
    parent_url: str
    parent_sha256: Digest
    element_index: Annotated[int, Field(strict=True, ge=0)]
    caption: str
    attributes: str
    declared_width: Annotated[int, Field(strict=True, ge=0)] | None
    declared_height: Annotated[int, Field(strict=True, ge=0)] | None
    responsive: ResponsiveSelection | None = Field(default=None, exclude_if=lambda v: v is None)
    pdf_crop: PdfFigureAnchor | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def selected_url(self) -> ImageCandidate:
        if self.pdf_crop is not None and (
            self.pdf_crop.source_sha256 != self.parent_sha256
            or self.responsive is not None
            or self.url != self.parent_url + "#ghimera-figure=" + str(self.element_index)
        ):
            raise ValueError("PDF figure candidates require exact source and figure locator")
        if self.responsive is not None:
            from ghimera.responsive_images import safe_image_url

            if safe_image_url(self.parent_url, self.responsive.source_token) != self.url:
                raise ValueError("selected responsive token must bind its exact absolute image URL")
        return self


class ImageRegion(VisualRecord):
    """Normalized coordinates in the original decoded image, not text offsets."""

    left: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    top: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    right: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    bottom: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]

    @model_validator(mode="after")
    def ordered(self) -> ImageRegion:
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("region must have positive area")
        return self


class PdfFigureAnchor(VisualRecord):
    schema_version: Literal["ghimera.pdf-figure-anchor/1"] = Field(alias="schema")
    source_sha256: Digest
    layout_sha256: Digest
    page_index: Annotated[int, Field(strict=True, ge=0)]
    region: ImageRegion
    page_image_sha256: Digest
    render_policy_sha256: Digest
    crop_policy_sha256: Digest


class OcrSpan(VisualRecord):
    text: Annotated[str, Field(min_length=1)]
    confidence: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    region: ImageRegion


class OcrResult(VisualRecord):
    image_sha256: Digest
    width: Positive
    height: Positive
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    spans: tuple[OcrSpan, ...]
    engine_revision: str
    language_pack_sha256: dict[str, Digest]

    @property
    def text(self) -> str:
        return " ".join(span.text for span in self.spans)


class VisualClaim(VisualRecord):
    text: Annotated[str, Field(min_length=1)]
    regions: Annotated[tuple[ImageRegion, ...], Field(min_length=1)]


class VisualInterpretation(VisualRecord):
    schema_version: Literal["ghimera.visual-interpretation/1"] = Field(alias="schema")
    image_sha256: Digest
    relevant: bool
    claims: tuple[VisualClaim, ...]
    model_id: str
    model_revision: str
    reviewer_model_id: str
    reviewer_model_revision: str
    request_sha256: Digest
    response_sha256: Digest
    review_request_sha256: Digest
    review_response_sha256: Digest


class ImageEvidence(VisualRecord):
    schema_version: Literal["ghimera.image-evidence/1"] = Field(alias="schema")
    candidate: ImageCandidate
    final_url: str
    sha256: Digest
    raw: bytes
    ocr: OcrResult
    config_sha256: Digest
    interpretation: VisualInterpretation | None = None
    relevance_reason: str

    @model_validator(mode="after")
    def bound(self) -> ImageEvidence:
        if (
            self.sha256 != hashlib.sha256(self.raw).hexdigest()
            or self.ocr.image_sha256 != self.sha256
        ):
            raise ValueError("visual evidence must retain its exact original image")
        if self.interpretation is not None and (
            self.interpretation.image_sha256 != self.sha256 or not self.interpretation.relevant
        ):
            raise ValueError("only relevant, reviewed interpretations may be retained")
        return self


ImageCandidate.model_rebuild()
