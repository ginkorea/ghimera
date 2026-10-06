"""Similarity observations are machine scores, never relevance probabilities."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Cosine = Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]
Count = Annotated[int, Field(strict=True, ge=0)]


class WindowSimilarity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start: Count
    end: Count
    text_sha256: Digest
    cosine: Cosine
    reference_source_id: Annotated[str, Field(min_length=1)]
    reference_text_sha256: Digest

    @model_validator(mode="after")
    def nonempty(self) -> "WindowSimilarity":
        if self.end <= self.start:
            raise ValueError("similarity windows must retain a nonempty native span")
        return self


class LinkSimilarity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: Annotated[str, Field(min_length=1)]
    input_sha256: Digest
    cosine: Cosine
    keyword_score: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    score: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    omitted_anchor_chars: Count

    @model_validator(mode="after")
    def binary_keyword(self) -> "LinkSimilarity":
        if self.keyword_score not in {0.0, 1.0}:
            raise ValueError("keyword presence is binary, not a model probability")
        return self


class SimilarityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.similarity/1"] = Field(alias="schema")
    text_sha256: Digest
    references_sha256: Digest
    goal_sha256: Digest
    total_chars: Count
    selected_chars: Count
    omitted_chars: Count
    windows: Annotated[tuple[WindowSimilarity, ...], Field(min_length=1)]
    links: tuple[LinkSimilarity, ...]
    omitted_links: Count
    document_cosine: Cosine

    @model_validator(mode="after")
    def coherent(self) -> "SimilarityEvidence":
        if self.total_chars != self.selected_chars + self.omitted_chars:
            raise ValueError("native context coverage must reconcile")
        if any(window.end > self.total_chars for window in self.windows):
            raise ValueError("similarity windows must lie inside native text")
        spans = tuple((window.start, window.end) for window in self.windows)
        if tuple(sorted(set(spans))) != spans:
            raise ValueError("similarity windows must be distinct and in native order")
        end, covered = 0, 0
        for start, stop in spans:
            covered += max(0, stop - max(start, end))
            end = max(end, stop)
        if covered != self.selected_chars:
            raise ValueError("selected coverage must be the union of native windows")
        if self.document_cosine != max(window.cosine for window in self.windows):
            raise ValueError("document similarity is the maximum selected native-window similarity")
        return self
