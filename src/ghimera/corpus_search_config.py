"""Explicit retained-corpus discovery binding; never an implicit local store."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.model_config import EmbeddingServiceConfig

Positive = Annotated[int, Field(strict=True, gt=0)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CorpusSearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.corpus-search/1"] = Field(alias="schema")
    corpus_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    corpus_config_sha256: Digest
    query_encoder: EmbeddingServiceConfig
    max_query_chars: Positive
    max_passage_hits: Positive
    max_results: Positive
    max_original_bytes: Positive
    max_response_bytes: Positive
    max_title_chars: Positive
    max_snippet_chars: Positive
    minimum_cosine: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]
    languages: tuple[str, ...]

    @model_validator(mode="after")
    def coherent(self) -> "CorpusSearchConfig":
        if (
            self.max_results > self.max_passage_hits
            or len(set(self.languages)) != len(self.languages)
            or any(not language.strip() for language in self.languages)
            or self.max_query_chars + len(self.query_encoder.text_prefix)
            > min(self.query_encoder.max_text_chars, self.query_encoder.max_input_chars)
        ):
            raise ValueError("corpus discovery needs coherent query, result and language limits")
        return self

    @property
    def identity(self) -> tuple[str, str]:
        return "evidence-corpus", "corpus-search/1:" + hashlib.sha256(
            self.model_dump_json().encode()
        ).hexdigest()
