"""Explicit passive image-variant policy, not an inferred browser viewport."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]


class ResponsiveImageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.responsive-images/1"] = Field(alias="schema")
    selection: Literal["largest_within_bounds"]
    srcset_attributes: Annotated[tuple[Literal["srcset", "data-srcset"], ...], Field(min_length=1)]
    url_attributes: Annotated[tuple[Literal["src", "data-src"], ...], Field(min_length=1)]
    picture_media: Literal["all_declared", "unconditional_only"]
    max_attribute_chars: Positive
    max_variants_per_attribute: Positive
    max_picture_sources: Positive
    max_declared_width: Positive
    max_declared_density: Annotated[float, Field(gt=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def distinct(self) -> "ResponsiveImageConfig":
        if len(set(self.srcset_attributes)) != len(self.srcset_attributes) or len(
            set(self.url_attributes)
        ) != len(self.url_attributes):
            raise ValueError("responsive attribute precedence must contain no duplicates")
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
