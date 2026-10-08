"""Learned relevance is an observed logit, never probability, entailment or coverage."""

import hashlib
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.reranking_config import Digest, OfflineRerankingConfig, Positive

Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Count = Annotated[int, Field(strict=True, ge=0)]


class RerankRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)


class RerankCandidate(RerankRecord):
    passage_id: Positive
    document_id: Digest
    passage_sha256: Digest
    text: Annotated[str, Field(min_length=1)]
    text_sha256: Digest
    cosine: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]
    language: str

    @model_validator(mode="after")
    def original_text(self) -> "RerankCandidate":
        if (
            not self.text.strip()
            or hashlib.sha256(self.text.encode()).hexdigest() != self.text_sha256
        ):
            raise ValueError("reranking candidate must preserve its exact native text")
        return self


class RerankRequest(RerankRecord):
    schema_version: Literal["ghimera.rerank-request/1"] = Field(alias="schema")
    policy: OfflineRerankingConfig
    corpus_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    config_sha256: Digest
    generation: Count
    query: Annotated[str, Field(min_length=1)]
    candidates: tuple[RerankCandidate, ...]

    @model_validator(mode="after")
    def bounded(self) -> "RerankRequest":
        if (
            not self.query.strip()
            or len(self.query) > self.policy.max_input_chars
            or len(self.candidates) > self.policy.max_pairs
            or len({row.passage_id for row in self.candidates}) != len(self.candidates)
            or sum(len(self.query) + len(row.text) for row in self.candidates)
            > self.policy.max_input_chars
            or len(self.model_dump_json().encode()) > self.policy.max_request_bytes
        ):
            raise ValueError(
                "reranking requires distinct complete candidates within its pair/char bound"
            )
        return self

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class PassageScore(RerankRecord):
    passage_id: Positive
    logit: Finite
    pair_tokens: Positive


class RerankScores(RerankRecord):
    schema_version: Literal["ghimera.rerank-scores/1"] = Field(alias="schema")
    request_sha256: Digest
    scores: tuple[PassageScore, ...]

    def validate_request(self, request: RerankRequest) -> None:
        if (
            self.request_sha256 != request.sha256
            or len(self.scores) != len(request.candidates)
            or {row.passage_id for row in self.scores}
            != {row.passage_id for row in request.candidates}
            or any(row.pair_tokens > request.policy.max_pair_tokens for row in self.scores)
            or len(self.model_dump_json().encode()) > request.policy.max_response_bytes
        ):
            raise ValueError(
                "learned scores must bind every exact candidate once without token truncation"
            )


SelectionReason = Literal[
    "selected", "minimum_cosine", "language", "top_k", "document_limit", "passages_per_document"
]


class RerankDecision(RerankRecord):
    passage_id: Positive
    reason: SelectionReason


def selection_decisions(
    request: RerankRequest,
    scores: RerankScores,
    *,
    top_k: int,
    minimum_cosine: float,
    languages: tuple[str, ...],
) -> tuple[RerankDecision, ...]:
    scores.validate_request(request)
    candidates = {candidate.passage_id: candidate for candidate in request.candidates}
    documents: dict[str, int] = {}
    selected = 0
    output: list[RerankDecision] = []
    for score in sorted(scores.scores, key=lambda row: (-row.logit, row.passage_id)):
        candidate = candidates[score.passage_id]
        reason: SelectionReason = "selected"
        if candidate.cosine < minimum_cosine:
            reason = "minimum_cosine"
        elif languages and candidate.language not in languages:
            reason = "language"
        elif documents.get(candidate.document_id, 0) >= request.policy.max_passages_per_document:
            reason = "passages_per_document"
        elif (
            candidate.document_id not in documents
            and len(documents) >= request.policy.max_source_documents
        ):
            reason = "document_limit"
        elif selected >= top_k:
            reason = "top_k"
        else:
            documents[candidate.document_id] = documents.get(candidate.document_id, 0) + 1
            selected += 1
        output.append(RerankDecision(passage_id=score.passage_id, reason=reason))
    return tuple(output)


class RerankingEvidence(RerankRecord):
    schema_version: Literal["ghimera.reranking-evidence/1"] = Field(alias="schema")
    request: RerankRequest
    scores: RerankScores
    top_k: Positive
    minimum_cosine: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]
    languages: tuple[str, ...]
    decisions: tuple[RerankDecision, ...]

    @model_validator(mode="after")
    def complete_selection(self) -> "RerankingEvidence":
        if self.decisions != selection_decisions(
            self.request,
            self.scores,
            top_k=self.top_k,
            minimum_cosine=self.minimum_cosine,
            languages=self.languages,
        ):
            raise ValueError("learned selection must replay every original candidate and exclusion")
        return self

    @property
    def selected_ids(self) -> tuple[int, ...]:
        return tuple(row.passage_id for row in self.decisions if row.reason == "selected")


class PassageReranker(Protocol):
    @property
    def config(self) -> OfflineRerankingConfig: ...

    async def prepare(self) -> None:
        """Admit artifacts/runtime before the query spends an encoding call."""
        ...

    async def score(self, request: RerankRequest) -> RerankScores: ...
