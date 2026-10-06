"""Evidence-bearing research records. Source snippets are discovery, never citations."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from chimera.models import Document, Harvest, ModelIdentity, Record
from chimera.transport_types import TransportEvidence

Text = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
QuestionId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]
Index = Annotated[int, Field(strict=True, ge=0)]


class ResearchRecord(Record):
    def content_digest(self) -> str:
        raw = json.dumps(
            self.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(raw.encode()).hexdigest()


class ResearchRequest(ResearchRecord):
    intent: Text
    seeds: tuple[str, ...] = ()

    @field_validator("intent")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("intent cannot be blank")
        return value


class Question(ResearchRecord):
    id: QuestionId
    text: Text


class SearchQuery(ResearchRecord):
    text: Text
    question_ids: Annotated[tuple[QuestionId, ...], Field(min_length=1)]


class ResearchPlan(ResearchRecord):
    questions: Annotated[tuple[Question, ...], Field(min_length=1)]
    queries: tuple[SearchQuery, ...]


class Citation(ResearchRecord):
    document_id: Annotated[str, Field(pattern=r"^doc:[0-9a-f]{64}$")]
    source_url: Text
    document_sha256: Digest
    text_sha256: Digest
    start: Index
    end: Annotated[int, Field(strict=True, gt=0)]
    quote: Text

    @model_validator(mode="after")
    def nonempty(self) -> "Citation":
        if self.end <= self.start:
            raise ValueError("citation needs a nonempty character span")
        return self

    def matches(self, document: Document) -> bool:
        text = document.extracted.text
        return (
            self.document_id == "doc:" + document.sha256
            and self.document_sha256 == document.sha256
            and hashlib.sha256(document.raw).hexdigest() == document.sha256
            and self.source_url == document.url
            and self.text_sha256 == hashlib.sha256(text.encode()).hexdigest()
            and 0 <= self.start < self.end <= len(text)
            and self.quote == text[self.start : self.end]
        )


class Coverage(ResearchRecord):
    question_id: QuestionId
    status: Literal["answered", "unresolved", "contradicted"]
    reason: Text
    citations: tuple[Citation, ...] = ()

    @model_validator(mode="after")
    def supported_status(self) -> "Coverage":
        if self.status in {"answered", "contradicted"} and not self.citations:
            raise ValueError("answered/contradicted coverage needs retained evidence")
        return self


class Assessment(ResearchRecord):
    coverage: tuple[Coverage, ...]


class Claim(ResearchRecord):
    text: Text
    question_ids: Annotated[tuple[QuestionId, ...], Field(min_length=1)]
    citations: Annotated[tuple[Citation, ...], Field(min_length=1)]


class AnswerDraft(ResearchRecord):
    claims: Annotated[tuple[Claim, ...], Field(min_length=1)]
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class ClaimReview(ResearchRecord):
    index: Index
    verdict: Literal["supported", "unsupported", "ambiguous"]
    reason: Text


class AnswerReview(ResearchRecord):
    answer_digest: Digest
    intent_covered: bool
    reason: Text
    claims: tuple[ClaimReview, ...]


class SearchHit(ResearchRecord):
    url: Text
    title: str
    snippet: str


class SearchRequest(ResearchRecord):
    query: SearchQuery
    limit: Annotated[int, Field(strict=True, gt=0)]
    max_bytes: Annotated[int, Field(strict=True, gt=0)]
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class SearchResponse(ResearchRecord):
    raw: bytes
    hits: tuple[SearchHit, ...]
    transport: TransportEvidence | None = None


class PlanningRequest(ResearchRecord):
    intent: Text
    questions: tuple[Question, ...]
    documents: tuple[Document, ...]
    assessment: Assessment | None
    max_questions: Annotated[int, Field(strict=True, gt=0)]
    max_queries: Annotated[int, Field(strict=True, gt=0)]
    max_query_chars: Annotated[int, Field(strict=True, gt=0)]


class EvidenceRequest(ResearchRecord):
    intent: Text
    questions: tuple[Question, ...]
    documents: tuple[Document, ...]


class AnswerRequest(EvidenceRequest):
    assessment: Assessment


class ReviewRequest(EvidenceRequest):
    answer: AnswerDraft


class ResearchRound(ResearchRecord):
    number: Annotated[int, Field(strict=True, gt=0)]
    queries: tuple[SearchQuery, ...]
    discovered_urls: tuple[str, ...]
    assessment: Assessment | None
    collection_stop: str


class ResearchResult(ResearchRecord):
    schema_version: Literal["chimera.research-result/1"] = Field(alias="schema")
    status: Literal["answered", "partial", "failed"]
    stop_reason: Literal["answered", "rounds_exhausted", "budget_exhausted", "failed"]
    harvest: Harvest
    questions: tuple[Question, ...]
    rounds: tuple[ResearchRound, ...]
    unresolved: tuple[QuestionId, ...]
    answer: AnswerDraft | None
    review: AnswerReview | None
    planner: ModelIdentity
    analyst: ModelIdentity
    reviewer: ModelIdentity
    search_provider: str
    search_revision: str
    search_calls: Index

    @model_validator(mode="after")
    def complete_status(self) -> "ResearchResult":
        policy = self.harvest.receipt.effective_config.research
        ids = {question.id for question in self.questions}
        if policy is None or len(ids) != len(self.questions) or not set(self.unresolved) <= ids:
            raise ValueError("research result must bind a valid question pack and research policy")
        if self.search_calls > policy.query_budget:
            raise ValueError("search count exceeds the recorded policy")
        if self.status == "answered":
            if (
                self.stop_reason != "answered"
                or self.answer is None
                or self.review is None
                or self.unresolved
                or not self.review.intent_covered
                or self.review.answer_digest != self.answer.content_digest()
                or any(claim.verdict != "supported" for claim in self.review.claims)
                or {claim.index for claim in self.review.claims}
                != set(range(len(self.answer.claims)))
                or len(self.review.claims) != len(self.answer.claims)
                or self.harvest.receipt.stop_reason != "goal_satisfied"
                or self.answer.confidence < policy.min_answer_confidence
                or {qid for claim in self.answer.claims for qid in claim.question_ids} != ids
            ):
                raise ValueError("answered research requires a complete bound support review")
            documents = {doc.sha256: doc for doc in self.harvest.documents}
            for claim in self.answer.claims:
                for citation in claim.citations:
                    document = documents.get(citation.document_sha256)
                    if document is None or not citation.matches(document):
                        raise ValueError("answer citations must match retained native evidence")
        elif self.answer is not None:
            raise ValueError("partial or failed research must not expose a completed answer")
        return self
