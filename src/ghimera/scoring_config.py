"""Operator-owned semantic scoring policy; no endpoint or tuning in code."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.model_config import EmbeddingServiceConfig
from ghimera.run_encoding_types import RunEncodingRecoveryConfig

Positive = Annotated[int, Field(strict=True, gt=0)]


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.scoring/1", "ghimera.scoring/2", "ghimera.scoring/3"] = Field(
        alias="schema"
    )
    encoder: EmbeddingServiceConfig
    query_encoder: EmbeddingServiceConfig | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
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
    run_encoding_recovery: RunEncodingRecoveryConfig | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def coherent(self) -> "ScoringConfig":
        if self.schema_version == "ghimera.scoring/3":
            if self.run_encoding_recovery is None:
                raise ValueError("scoring/3 requires explicit run-bound encoding recovery")
            if self.reference_source == "intent" and self.query_encoder is None:
                raise ValueError("scoring/3 intent mode requires an explicit query encoder")
        elif "run_encoding_recovery" in self.model_fields_set:
            raise ValueError("legacy scoring profiles forbid run encoding recovery, even null")
        if self.query_encoder is not None and self.reference_source != "intent":
            raise ValueError("a query encoder requires intent reference mode")
        if self.schema_version == "chimera.scoring/1" and self.query_encoder is not None:
            raise ValueError("a distinct query encoder requires scoring/2")
        if self.schema_version == "ghimera.scoring/2" and (
            self.reference_source != "intent" or self.query_encoder is None
        ):
            raise ValueError("scoring/2 requires intent references and an explicit query encoder")
        if self.query_encoder is not None and (
            self.query_encoder.model_id,
            self.query_encoder.revision,
            self.query_encoder.dimensions,
        ) != (self.encoder.model_id, self.encoder.revision, self.encoder.dimensions):
            raise ValueError("query and passage encoders must declare the same vector space")
        if (self.reference_source == "pinned") != (self.references_sha256 is not None):
            raise ValueError(
                "pinned scoring requires a reference digest; intent scoring forbids it"
            )
        if self.overlap_chars >= self.window_chars:
            raise ValueError("scoring windows require bounded overlap")
        if self.window_chars + len(self.encoder.text_prefix) > self.encoder.max_text_chars:
            raise ValueError("native windows and prefix must fit the encoder's text limit")
        return self

    @property
    def intent_encoder(self) -> EmbeddingServiceConfig:
        return self.query_encoder or self.encoder
