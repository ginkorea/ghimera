"""Run accounting is distinct from standalone CPU relevance and corpus encoding."""

import hashlib
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.corpus_config import CorpusConfig
from ghimera.reranking_types import PassageReranker, RerankRequest, RerankScores
from ghimera.retrieval import HybridRetrievalConfig, RetrievalEvidence

Count = Annotated[int, Field(strict=True, ge=0)]
Positive = Annotated[int, Field(strict=True, gt=0)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RerankRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)


class ResearchRerankingConfig(RerankRunRecord):
    schema_version: Literal["ghimera.research-reranking/1"] = Field(alias="schema")
    max_calls: Positive
    max_pairs: Positive
    max_input_chars: Positive
    uncertain_policy: Literal["hold"]
    replay_policy: Literal["explicit_original_ack"]


class RerankDecision(RerankRunRecord):
    """A caller selects fresh work or one exact original, never lookup by query."""

    schema_version: Literal["ghimera.rerank-decision/1"] = Field(alias="schema")
    action: Literal["fresh", "replay"]
    operation_key: Annotated[str, Field(min_length=1)]
    original_intent_sequence: Count | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def explicit(self) -> "RerankDecision":
        if not self.operation_key.strip() or (self.action == "replay") != (
            self.original_intent_sequence is not None
        ):
            raise ValueError("reranking needs explicit fresh work or one original intent")
        return self


class RerankReservation(RerankRunRecord):
    schema_version: Literal["ghimera.rerank-reservation/1"] = Field(alias="schema")
    channel: Literal["discovery", "retained"]
    operation_key: Annotated[str, Field(min_length=1)]
    corpus: CorpusConfig
    retrieval: RetrievalEvidence
    request: RerankRequest

    @model_validator(mode="after")
    def source_recipe(self) -> "RerankReservation":
        corpus, request = self.corpus, self.request
        if (
            not self.operation_key.strip()
            or corpus.reranking != request.policy
            or corpus.identity != request.config_sha256
            or corpus.encoding_recovery is None
            or tuple(candidate.passage_id for candidate in request.candidates)
            != tuple(row.passage_id for row in self.retrieval.ranking)
        ):
            raise ValueError("rerank reservation requires its exact native recipe and RRF union")
        return self

    @property
    def input_chars(self) -> int:
        return sum(len(self.request.query) + len(item.text) for item in self.request.candidates)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class RerankRunEvidence(RerankRunRecord):
    schema_version: Literal["ghimera.rerank-run/1"] = Field(alias="schema")
    reservation_sha256: Digest
    intent_sequence: Count
    ack_sequence: Count
    output_sha256: Digest
    output_bytes: Count
    replay_sequence: Count | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def order(self) -> "RerankRunEvidence":
        if self.ack_sequence <= self.intent_sequence or (
            self.replay_sequence is not None and self.replay_sequence <= self.ack_sequence
        ):
            raise ValueError(
                "rerank proof must preserve original intent, return and local read order"
            )
        return self


class RunRerankResult(RerankRunRecord):
    scores: RerankScores
    evidence: RerankRunEvidence


class RerankInvoker(Protocol):
    """Corpus owns native snapshot construction; the run owns only model contact."""

    @property
    def replaying(self) -> bool: ...

    def admit(
        self,
        corpus: CorpusConfig,
        corpus_id: str,
        *,
        query: str,
        retrieval: HybridRetrievalConfig | None,
        generation: int | None = None,
    ) -> None: ...

    async def score(
        self,
        request: RerankRequest,
        reranker: PassageReranker,
        *,
        corpus: CorpusConfig,
        retrieval: RetrievalEvidence,
    ) -> RunRerankResult: ...
