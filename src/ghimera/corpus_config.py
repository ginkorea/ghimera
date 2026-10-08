"""Explicit standalone evidence-store and native-vector query policy."""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ghimera.model_config import EmbeddingServiceConfig

Positive = Annotated[int, Field(strict=True, gt=0)]


class EncodingRecoveryConfig(BaseModel):
    """Lifetime invocation allowance; reopening or failure never resets it."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.encoding-recovery/1"] = Field(alias="schema")
    max_calls: Positive
    max_input_chars: Positive
    max_stored_bytes: Positive


class CorpusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.corpus/1"] = Field(alias="schema")
    directory: Path
    encoder: EmbeddingServiceConfig
    query_encoder: EmbeddingServiceConfig
    chunk_chars: Positive
    overlap_chars: Annotated[int, Field(strict=True, ge=0)]
    max_documents: Positive
    max_chunks: Positive
    max_document_bytes: Positive
    max_stored_document_bytes: Positive
    max_vector_bytes: Positive
    max_audit_entries: Positive
    max_audit_bytes: Positive
    max_encoding_calls_per_append: Positive
    max_encoding_chars_per_append: Positive
    max_query_chars: Positive
    max_top_k: Positive
    search_candidates: Positive
    minimum_cosine: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]
    hnsw_neighbors: Positive
    hnsw_construction: Positive
    hnsw_search: Positive
    database_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    operation_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    encoding_recovery: EncodingRecoveryConfig | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("directory")
    @classmethod
    def exact_directory(cls, value: Path) -> Path:
        if not value.is_absolute() or value == Path("/") or ".." in value.parts:
            raise ValueError("corpus directory must be an explicit absolute non-root path")
        return value

    @model_validator(mode="after")
    def coherent(self) -> "CorpusConfig":
        document, query = self.encoder, self.query_encoder
        if (document.model_id, document.revision, document.dimensions) != (
            query.model_id,
            query.revision,
            query.dimensions,
        ):
            raise ValueError("passages and queries require the same pinned embedding space")
        if (
            self.overlap_chars >= self.chunk_chars
            or self.chunk_chars + len(document.text_prefix) > document.max_text_chars
            or self.max_query_chars + len(query.text_prefix) > query.max_text_chars
            or self.max_query_chars + len(query.text_prefix) > query.max_input_chars
            or self.search_candidates < self.max_top_k
            or self.hnsw_construction < self.hnsw_neighbors
            or self.hnsw_search < self.search_candidates
            or self.max_document_bytes > self.max_stored_document_bytes
        ):
            raise ValueError("corpus chunk, query, index and storage limits must be coherent")
        return self

    @property
    def identity(self) -> str:
        # Relocation is not a new model/recipe; every other effective choice is.
        excluded = {"directory"}
        if self.encoding_recovery is None:
            excluded.add("encoding_recovery")
        return hashlib.sha256(self.model_dump_json(exclude=excluded).encode()).hexdigest()

    def same_passage_space(self, service: EmbeddingServiceConfig) -> bool:
        current = self.encoder
        return (current.model_id, current.revision, current.dimensions, current.text_prefix) == (
            service.model_id,
            service.revision,
            service.dimensions,
            service.text_prefix,
        )

    @property
    def recipe_identity(self) -> str:
        """Mutable capacity/transport/ANN knobs do not orphan immutable passage vectors."""
        service = self.encoder
        recipe = {
            "schema": self.schema_version,
            "model_id": service.model_id,
            "revision": service.revision,
            "dimensions": service.dimensions,
            "text_prefix": service.text_prefix,
            "chunk_chars": self.chunk_chars,
            "overlap_chars": self.overlap_chars,
        }
        data = json.dumps(
            recipe, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
        return hashlib.sha256(data).hexdigest()
