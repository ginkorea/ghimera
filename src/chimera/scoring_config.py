"""Operator-owned semantic scoring policy; no endpoint or tuning in code."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chimera.model_config import EmbeddingServiceConfig

Positive = Annotated[int, Field(strict=True, gt=0)]


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.scoring/1"] = Field(alias="schema")
    encoder: EmbeddingServiceConfig
    reference_source: Literal["pinned", "intent"] = Field(
        default="pinned", exclude_if=lambda value: value == "pinned"
    )
    references_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    encoding_call_budget: Positive
    encoding_char_budget: Positive
    window_chars: Positive
    overlap_chars: Annotated[int, Field(strict=True, ge=0)]
    max_windows: Positive
    max_links: Positive
    max_reference_chunks: Positive
    max_anchor_chars: Positive
    keyword_weight: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]

    @model_validator(mode="after")
    def coherent(self) -> "ScoringConfig":
        if (self.reference_source == "pinned") != (self.references_sha256 is not None):
            raise ValueError(
                "pinned scoring requires a reference digest; intent scoring forbids it"
            )
        if self.overlap_chars >= self.window_chars:
            raise ValueError("scoring windows require bounded overlap")
        if self.window_chars + len(self.encoder.text_prefix) > self.encoder.max_text_chars:
            raise ValueError("native windows and prefix must fit the encoder's text limit")
        return self
