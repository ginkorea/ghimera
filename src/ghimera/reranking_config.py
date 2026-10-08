"""Operator-owned offline cross-encoder artifacts, runtime and bounded selection."""

import hashlib
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RerankingArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    sha256: Digest
    role: Literal["model", "tokenizer"]

    @field_validator("path")
    @classmethod
    def local_data(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or str(path) != value
            or ".." in path.parts
            or any(not part or part.startswith(".") for part in path.parts)
            or path.suffix not in {".json", ".txt", ".model", ".safetensors"}
        ):
            raise ValueError("declare canonical relative data files, never executable model code")
        return value


class OfflineRerankingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.offline-reranking/1"] = Field(alias="schema")
    runtime: Literal["transformers_sequence_classification/1"]
    model_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(min_length=1)]
    score_semantics: Literal["single_relevance_logit"]
    input_recipe: Literal["query_document_pair/1"]
    device: Literal["cpu"]
    model_directory: Path
    artifacts: Annotated[tuple[RerankingArtifact, ...], Field(min_length=1)]
    max_artifact_bytes: Positive
    worker_python: Path
    work_directory: Path
    torch_version: Annotated[str, Field(min_length=1)]
    transformers_version: Annotated[str, Field(min_length=1)]
    cpu_threads: Positive
    interop_threads: Positive
    batch_size: Positive
    max_workers: Positive
    max_pairs: Positive
    max_pair_tokens: Positive
    max_input_chars: Positive
    max_request_bytes: Positive
    max_response_bytes: Positive
    max_diagnostic_bytes: Positive
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    cleanup_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_passages_per_document: Positive
    max_source_documents: Positive

    @field_validator("model_directory", "worker_python", "work_directory")
    @classmethod
    def explicit_path(cls, value: Path) -> Path:
        if not value.is_absolute() or value == Path("/") or ".." in value.parts:
            raise ValueError("offline reranking needs explicit absolute non-root paths")
        return value

    @model_validator(mode="after")
    def coherent(self) -> "OfflineRerankingConfig":
        names = tuple(artifact.path for artifact in self.artifacts)
        if (
            len(set(names)) != len(names)
            or {artifact.role for artifact in self.artifacts} != {"model", "tokenizer"}
            or "config.json" not in names
            or not any(name.endswith(".safetensors") for name in names)
            or self.batch_size > self.max_pairs
            or self.max_passages_per_document > self.max_pairs
            or self.max_source_documents > self.max_pairs
            or not all(
                s.strip()
                for s in (
                    self.model_id,
                    self.revision,
                    self.torch_version,
                    self.transformers_version,
                )
            )
            or self.model_directory == self.work_directory
            or self.work_directory.is_relative_to(self.model_directory)
            or self.model_directory.is_relative_to(self.work_directory)
        ):
            raise ValueError(
                "offline reranking requires distinct complete artifacts and coherent bounds"
            )
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
