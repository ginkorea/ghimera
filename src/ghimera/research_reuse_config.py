"""Explicit retained-snapshot research policy and its separate query allowance."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.corpus_evidence_config import CorpusEvidenceConfig

Positive = Annotated[int, Field(strict=True, gt=0)]


class ResearchReuseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.research-reuse/1"] = Field(alias="schema")
    reader: CorpusEvidenceConfig
    max_queries: Positive
    max_input_chars: Positive
    max_snapshot_bytes: Positive
    max_source_documents: Positive
    query_mode: Literal["intent_and_planned_queries"]
    assess_before_discovery: bool
    source_age_policy: Literal["explicit_unknown"]

    @model_validator(mode="after")
    def coherent(self) -> "ResearchReuseConfig":
        if (
            self.max_input_chars < len(self.reader.query_encoder.text_prefix) + 1
            or self.max_snapshot_bytes < self.reader.max_response_bytes
            or self.max_source_documents < self.reader.max_documents
        ):
            raise ValueError("research reuse requires coherent query and snapshot allowances")
        return self
