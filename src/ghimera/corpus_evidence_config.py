"""Explicit snapshot-context limits, separate from fresh source acquisition."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.model_config import EmbeddingServiceConfig

Positive = Annotated[int, Field(strict=True, gt=0)]


class CorpusEvidenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.corpus-evidence-config/1"] = Field(alias="schema")
    corpus_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    corpus_config_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    query_encoder: EmbeddingServiceConfig
    max_query_chars: Positive
    max_passage_hits: Positive
    max_documents: Positive
    max_original_bytes: Positive
    max_response_bytes: Positive
    minimum_cosine: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]
    languages: tuple[str, ...]
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    source_mode: Literal["retained_snapshot"]

    @model_validator(mode="after")
    def coherent(self) -> "CorpusEvidenceConfig":
        if (
            self.max_documents > self.max_passage_hits
            or len(set(self.languages)) != len(self.languages)
            or any(not language.strip() for language in self.languages)
            or self.max_query_chars + len(self.query_encoder.text_prefix)
            > min(self.query_encoder.max_text_chars, self.query_encoder.max_input_chars)
        ):
            raise ValueError("snapshot evidence requires coherent query and selection limits")
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
