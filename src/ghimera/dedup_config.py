"""Operator-owned content identity policy, not a source-code tuning constant."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DedupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.dedup/1"] = Field(alias="schema")
    tracking_parameters: tuple[str, ...]
    tracking_prefixes: tuple[str, ...]
    shingle_chars: Annotated[int, Field(strict=True, ge=1, le=32)]
    max_hamming_distance: Annotated[int, Field(strict=True, ge=0, le=8)]
    min_near_chars: Annotated[int, Field(strict=True, gt=0)]
    max_index_documents: Annotated[int, Field(strict=True, gt=0)]
    max_text_chars: Annotated[int, Field(strict=True, gt=0)]
    min_length_ratio: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]

    @model_validator(mode="after")
    def explicit_parameters(self) -> "DedupConfig":
        for values in (self.tracking_parameters, self.tracking_prefixes):
            if len(set(values)) != len(values) or any(
                not value
                or value != value.lower()
                or not value.strip() == value
                or any(char in value for char in "&=?#")
                for value in values
            ):
                raise ValueError("tracking rules must be nonblank unique lowercase names")
        return self

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()
