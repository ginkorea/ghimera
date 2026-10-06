"""Evidence-bearing research records. Source snippets are discovery, never citations."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from ghimera.local_input_types import LocalDocumentSeed
from ghimera.model_types import ModelCallEvidence
from ghimera.models import Document, Harvest, ModelIdentity, Record
from ghimera.reference_types import SearchReference
from ghimera.transport_types import TransportEvidence

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
    local_documents: tuple[LocalDocumentSeed, ...] = Field(default=(), exclude_if=lambda v: not v)

    @field_validator("intent")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("intent cannot be blank")
        return value


class ResearchModelResult(ResearchRecord):
    model_call: ModelCallEvidence | None = None


class Question(ResearchRecord):
    id: QuestionId
    text: Text


class SearchQuery(ResearchRecord):
    text: Text
    question_ids: Annotated[tuple[QuestionId, ...], Field(min_length=1)]


class ResearchPlan(ResearchModelResult):
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


class Assessment(ResearchModelResult):
    coverage: tuple[Coverage, ...]


class Claim(ResearchRecord):
    text: Text
    question_ids: Annotated[tuple[QuestionId, ...], Field(min_length=1)]
    citations: Annotated[tuple[Citation, ...], Field(min_length=1)]


class AnswerDraft(ResearchModelResult):
    claims: Annotated[tuple[Claim, ...], Field(min_length=1)]
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class ClaimReview(ResearchRecord):
    index: Index
    verdict: Literal["supported", "unsupported", "ambiguous"]
    reason: Text


class AnswerReview(ResearchModelResult):
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


class SearchObservation(ResearchRecord):
    schema_version: Literal["chimera.search-observation/1"] = Field(alias="schema")
    sequence: Index
    provider: Text
    provider_revision: Text
    query: SearchQuery
    response: SearchResponse


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
    schema_version: Literal["chimera.research-result/1", "chimera.research-result/2"] = Field(
        alias="schema"
    )
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
    search_observations: tuple[SearchObservation, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def retained_discovery(self) -> "ResearchResult":
        if self.schema_version == "chimera.research-result/1":
            if self.search_observations:
                raise ValueError("legacy results do not claim retained search responses")
            return self
        attempts = tuple(
            row
            for row in self.harvest.ledger
            if row.event == "fetch" and row.route is not None and row.route.startswith("search:")
        )
        rows = {row.sequence: row for row in attempts if row.refusal is None}
        if {observation.sequence for observation in self.search_observations} != rows.keys() or len(
            self.search_observations
        ) != len(rows):
            raise ValueError("every successful search fetch requires exactly one retained response")
        policy = self.harvest.receipt.effective_config.research
        if policy is None or len(attempts) > self.search_calls:
            raise ValueError("retained discovery requires its research policy and spent calls")
        ids = {question.id for question in self.questions}
        for observation in self.search_observations:
            row = rows[observation.sequence]
            response = observation.response
            if (
                observation.provider != self.search_provider
                or observation.provider_revision != self.search_revision
                or row.route != f"search:{observation.provider}@{observation.provider_revision}"
                or row.query != observation.query.text
                or not set(observation.query.question_ids) <= ids
                or not observation.query.text.strip()
                or len(observation.query.text) > policy.max_query_chars
                or len(response.hits) > policy.results_per_query
                or row.bytes_read != len(response.raw)
                or row.reason != "grounded_search:" + hashlib.sha256(response.raw).hexdigest()
                or row.search_response_sha256 != response.content_digest()
                or row.transport != response.transport
            ):
                raise ValueError("retained search response must bind its recorded fetch")
        for row in self.harvest.ledger:
            reference = row.reference.reference if row.reference is not None else None
            if not isinstance(reference, SearchReference):
                continue
            if not any(
                reference.query_sequence < observation.sequence < row.sequence
                and observation.provider == reference.provider
                and observation.provider_revision == reference.provider_revision
                and observation.query.text == reference.query
                and hashlib.sha256(observation.response.raw).hexdigest()
                == reference.response_sha256
                and any(
                    (hit.url, hit.title, hit.snippet)
                    == (reference.target_url, reference.anchor, reference.snippet)
                    for hit in observation.response.hits
                )
                for observation in self.search_observations
            ):
                raise ValueError("citing-source reference must bind a retained actual search hit")
        return self

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
            documents = {(doc.sha256, doc.url): doc for doc in self.harvest.source_documents}
            for claim in self.answer.claims:
                for citation in claim.citations:
                    document = documents.get((citation.document_sha256, citation.source_url))
                    if document is None or not citation.matches(document):
                        raise ValueError("answer citations must match retained native evidence")
        elif self.answer is not None:
            raise ValueError("partial or failed research must not expose a completed answer")
        return self
