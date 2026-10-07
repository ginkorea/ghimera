"""Visual evidence is not native page text or an unreviewed diagram claim."""

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


class ImageCandidate(VisualRecord):
    url: str
    parent_url: str
    parent_sha256: Digest
    element_index: Annotated[int, Field(strict=True, ge=0)]
    caption: str
    attributes: str
    declared_width: Annotated[int, Field(strict=True, ge=0)] | None
    declared_height: Annotated[int, Field(strict=True, ge=0)] | None


class ImageRegion(VisualRecord):
    """Normalized coordinates in the original decoded image, not text offsets."""

    left: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    top: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    right: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    bottom: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]

    @model_validator(mode="after")
    def ordered(self) -> "ImageRegion":
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("region must have positive area")
        return self


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
    def bound(self) -> "ImageEvidence":
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
