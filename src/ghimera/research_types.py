"""Evidence-bearing research records. Source snippets are discovery, never citations."""

import hashlib
import json
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ghimera.ahmia_config import AhmiaConfig
from ghimera.ahmia_wire import AhmiaHitEvidence, decode_ahmia
from ghimera.corpus_search_config import CorpusSearchConfig
from ghimera.corpus_search_wire import CorpusSearchWire
from ghimera.corpus_types import BoundCorpusDocument
from ghimera.discovery_config import DiscoveryProgress
from ghimera.graph_planning_types import PlanningGraph
from ghimera.graph_types import GraphVisualAnchor
from ghimera.local_input_types import LocalDocumentSeed
from ghimera.model_types import ModelCallEvidence
from ghimera.models import Document, Harvest, LedgerRow, ModelIdentity, Record
from ghimera.reference_types import SearchReference
from ghimera.research_reuse import ResearchRetrievalReport, RetainedSourceNotice, validate_notices
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


LanguageCode = Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")]


class ResearchRequest(ResearchRecord):
    intent: Text
    seeds: tuple[str, ...] = ()
    local_documents: tuple[LocalDocumentSeed, ...] = Field(default=(), exclude_if=lambda v: not v)
    # Preferred source languages: every question gets at least one query per language.
    languages: tuple[LanguageCode, ...] = Field(default=(), exclude_if=lambda v: not v)

    @field_validator("languages")
    @classmethod
    def distinct_languages(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("preferred languages cannot repeat")
        return value

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
    graph_refs: tuple[Text, ...] = Field(default=(), exclude_if=lambda v: not v)
    # The query's language; a provider that can filter by language honours it.
    language: LanguageCode | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def unique_graph_refs(self) -> "SearchQuery":
        if len(set(self.graph_refs)) != len(self.graph_refs):
            raise ValueError("search query graph references must be unique")
        return self


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
    basis: Literal["native", "reviewed_pdf_transcription", "image_ocr", "reviewed_visual_claim"] = (
        Field(default="native", exclude_if=lambda value: value == "native")
    )
    page_indices: tuple[Index, ...] = Field(default=(), exclude_if=lambda value: not value)
    visual_anchor: GraphVisualAnchor | None = Field(default=None, exclude_if=lambda v: v is None)

    @classmethod
    def from_image(cls, document: Document, image_index: int, span_index: int) -> "Citation":
        from ghimera.graph_types import GraphEvidence
        from ghimera.visual_evidence import image_reading

        if not 0 <= image_index < len(document.images):
            raise ValueError("image citation requires a retained original")
        image = document.images[image_index]
        if (
            hashlib.sha256(document.raw).hexdigest() != document.sha256
            or image.candidate.parent_url != document.url
            or image.candidate.parent_sha256 != document.sha256
        ):
            raise ValueError("image citation must bind its exact retained parent source")
        reading = image_reading(image)
        if reading is None:
            raise ValueError("image has no accepted derived reading")
        evidence = GraphEvidence.from_visual("doc:" + document.sha256, reading, span_index)
        return cls(
            document_id=evidence.document_id,
            source_url=document.url,
            document_sha256=document.sha256,
            text_sha256=evidence.text_sha256,
            start=evidence.start,
            end=evidence.end,
            quote=evidence.quote,
            basis=evidence.basis,
            page_indices=evidence.page_indices,
            visual_anchor=evidence.visual_anchor,
        )

    @classmethod
    def from_document(cls, document: Document, start: int, end: int) -> "Citation":
        text = document.extracted.text
        transcription = document.extracted.pdf_transcription
        return cls(
            document_id="doc:" + document.sha256,
            source_url=document.url,
            document_sha256=document.sha256,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            start=start,
            end=end,
            quote=text[start:end],
            basis="reviewed_pdf_transcription" if transcription is not None else "native",
            page_indices=transcription.cited_pages(start, end) if transcription is not None else (),
        )

    @model_validator(mode="after")
    def nonempty(self) -> "Citation":
        if self.end <= self.start:
            raise ValueError("citation needs a nonempty character span")
        if (self.basis in {"image_ocr", "reviewed_visual_claim"}) != (
            self.visual_anchor is not None
        ):
            raise ValueError("visual citations require retained image-region anchors")
        return self

    def matches(self, document: Document) -> bool:
        if self.visual_anchor is not None:
            for image_index, image in enumerate(document.images):
                if image.sha256 != self.visual_anchor.image_sha256:
                    continue
                try:
                    return self == self.from_image(
                        document, image_index, self.visual_anchor.span_index
                    )
                except ValueError:
                    return False
            return False
        text = document.extracted.text
        transcription = document.extracted.pdf_transcription
        return (
            self.basis == ("reviewed_pdf_transcription" if transcription is not None else "native")
            and self.page_indices
            == (
                transcription.cited_pages(self.start, self.end) if transcription is not None else ()
            )
            and self.document_id == "doc:" + document.sha256
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
    index_evidence: AhmiaHitEvidence | None = Field(default=None, exclude_if=lambda v: v is None)


class SearchRequest(ResearchRecord):
    query: SearchQuery
    limit: Annotated[int, Field(strict=True, gt=0)]
    max_bytes: Annotated[int, Field(strict=True, gt=0)]
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class SearchResponse(ResearchRecord):
    raw: bytes
    hits: tuple[SearchHit, ...]
    transport: TransportEvidence | None = None
    index_retrieved_at: AwareDatetime | None = Field(default=None, exclude_if=lambda v: v is None)


class SearchObservation(ResearchRecord):
    schema_version: Literal["chimera.search-observation/1"] = Field(alias="schema")
    sequence: Index
    provider: Text
    provider_revision: Text
    query: SearchQuery
    response: SearchResponse


MAX_PLANNING_READINGS = 32


class ReadingNote(ResearchRecord):
    """What one kept page's reading tells the planner: no page text, only guidance."""

    url: Text
    language: Text
    relevance: Text
    informs: tuple[Text, ...] = Field(default=(), exclude_if=lambda v: not v)
    contribution: str | None = Field(default=None, exclude_if=lambda v: not v)
    queries: tuple[Text, ...] = Field(default=(), exclude_if=lambda v: not v)
    names: tuple[Text, ...] = Field(default=(), exclude_if=lambda v: not v)


def answer_order(documents: tuple[Document, ...]) -> tuple[Document, ...]:
    """Grade order for answers (direct, supporting, background, unjudged); stable,
    so a run of legacy ungraded pages keeps its original order. Unrelated is left out."""
    ranked = [(doc.verdict.answer_rank, index, doc) for index, doc in enumerate(documents)]
    return tuple(doc for rank, _, doc in sorted(item for item in ranked if item[0] is not None))


def planning_readings(documents: tuple[Document, ...]) -> tuple[ReadingNote, ...]:
    """The readings the next plan steers by: sub-questions informed and leads to query."""
    notes: list[ReadingNote] = []
    for doc in answer_order(documents):
        reading = doc.verdict
        queries = tuple(lead.value for lead in reading.follow if lead.kind == "query")
        if not (reading.informs or reading.contribution or queries or reading.names):
            continue
        notes.append(
            ReadingNote(
                url=doc.url,
                language=reading.language,
                relevance=reading.grade,
                informs=reading.informs,
                contribution=reading.contribution,
                queries=queries,
                names=tuple(f"{name.kind}: {name.text}" for name in reading.names),
            )
        )
        if len(notes) == MAX_PLANNING_READINGS:
            break
    return tuple(notes)


def round_readings(
    documents: tuple[Document, ...],
    questions: tuple["Question", ...],
    followed: tuple[str, ...],
) -> tuple[dict[str, int], dict[str, int], tuple[str, ...]]:
    """A round's kept pages by grade, sub-question coverage by readings, leads queued."""
    grades: dict[str, int] = {}
    informed: dict[str, int] = {}
    known = {question.id for question in questions}
    for doc in documents:
        grade = doc.verdict.grade
        grades[grade] = grades.get(grade, 0) + 1
        for question_id in dict.fromkeys(doc.verdict.informs):
            if question_id in known:
                informed[question_id] = informed.get(question_id, 0) + 1
    return grades, informed, followed


class PlanningRequest(ResearchRecord):
    intent: Text
    questions: tuple[Question, ...]
    documents: tuple[Document, ...]
    assessment: Assessment | None
    max_questions: Annotated[int, Field(strict=True, gt=0)]
    max_queries: Annotated[int, Field(strict=True, gt=0)]
    max_query_chars: Annotated[int, Field(strict=True, gt=0)]
    graph_context: PlanningGraph | None = Field(default=None, exclude_if=lambda v: v is None)
    retained_sources: tuple[RetainedSourceNotice, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    languages: tuple[LanguageCode, ...] = Field(default=(), exclude_if=lambda v: not v)
    # Guidance from the judge's readings of kept pages (see ReadingNote).
    readings: tuple[ReadingNote, ...] = Field(default=(), exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def source_notices(self) -> "PlanningRequest":
        validate_notices(self.retained_sources, self.documents)
        return self


class EvidenceRequest(ResearchRecord):
    intent: Text
    questions: tuple[Question, ...]
    documents: tuple[Document, ...]
    retained_sources: tuple[RetainedSourceNotice, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def source_notices(self) -> "EvidenceRequest":
        validate_notices(self.retained_sources, self.documents)
        return self


class AnswerRequest(EvidenceRequest):
    assessment: Assessment


class ReviewRequest(EvidenceRequest):
    answer: AnswerDraft


PLAN_FITTED = "plan_fitted: raw="


def plan_fitted_reason(raw: str, fitted: str, omitted: int, truncated: int) -> str:
    """The journaled binding of a model's raw plan digest to the fitted plan run."""
    return f"{PLAN_FITTED}{raw} fitted={fitted} omitted={omitted} truncated={truncated}"


def fitted_plans(rows: tuple["LedgerRow", ...]) -> dict[str, set[str]]:
    """fitted plan digest -> raw plan digests it was fitted from."""
    bound: dict[str, set[str]] = {}
    for row in rows:
        if row.event == "policy" and row.reason and row.reason.startswith(PLAN_FITTED):
            fields = row.reason[len(PLAN_FITTED) :].split()
            if len(fields) >= 2 and fields[1].startswith("fitted="):
                bound.setdefault(fields[1][len("fitted=") :], set()).add(fields[0])
    return bound


def planned_digests(digest: str, fitted: dict[str, set[str]]) -> set[str]:
    """The plan-row digests a round's queries may bind: its own, or the raw it came from."""
    return {digest} | fitted.get(digest, set())


class QueryTruncation(ResearchRecord):
    """A planned query cut to ``max_query_chars`` at a word boundary, never refused."""

    original_text: Text
    text: Text
    language: LanguageCode | None = Field(default=None, exclude_if=lambda v: v is None)


class ResearchRound(ResearchRecord):
    number: Annotated[int, Field(strict=True, gt=0)]
    queries: tuple[SearchQuery, ...]
    discovered_urls: tuple[str, ...]
    assessment: Assessment | None
    collection_stop: str
    discovery_progress: DiscoveryProgress | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    # Deterministic per-language queries added for (question, language) pairs the
    # plan left uncovered, and the round's new evidence documents per language.
    language_queries: tuple[SearchQuery, ...] = Field(default=(), exclude_if=lambda v: not v)
    pages_by_language: dict[str, Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=dict, exclude_if=lambda v: not v
    )
    # Every new page is kept; these say how the judge's readings graded them, how
    # many readings informed each sub-question, and which link leads were queued.
    pages_by_grade: dict[str, Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=dict, exclude_if=lambda v: not v
    )
    informed_questions: dict[str, Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=dict, exclude_if=lambda v: not v
    )
    followed_leads: tuple[str, ...] = Field(default=(), exclude_if=lambda v: not v)
    # A plan is never refused for its size: queries over max_queries_per_round
    # (planner's plus language copies) are omitted, and overlong texts are cut.
    omitted_queries: tuple[SearchQuery, ...] = Field(default=(), exclude_if=lambda v: not v)
    truncated_queries: tuple[QueryTruncation, ...] = Field(default=(), exclude_if=lambda v: not v)


def truncate_query_text(text: str, limit: int) -> str:
    """Cut at the last word boundary within ``limit``; a single long word is hard-cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if not text[limit].isspace():
        boundary = max(cut.rfind(" "), cut.rfind("\t"), cut.rfind("\n"))
        if boundary > 0 and cut[:boundary].strip():
            cut = cut[:boundary]
    return cut.rstrip() or text[:limit]


def truncate_queries(
    queries: tuple[SearchQuery, ...], limit: int
) -> tuple[tuple[SearchQuery, ...], tuple[QueryTruncation, ...]]:
    kept: list[SearchQuery] = []
    cut: list[QueryTruncation] = []
    for query in queries:
        text = truncate_query_text(query.text, limit)
        if text != query.text:
            cut.append(
                QueryTruncation(original_text=query.text, text=text, language=query.language)
            )
            query = query.model_copy(update={"text": text})
        kept.append(query)
    return tuple(kept), tuple(cut)


def balance_queries(
    queries: tuple[SearchQuery, ...], bound: int, languages: tuple[str, ...] = ()
) -> tuple[tuple[SearchQuery, ...], tuple[SearchQuery, ...]]:
    """Keep at most ``bound`` queries, balanced round-robin over languages, then over
    questions within a language, in the plan's order within each. Returns the kept
    queries and the omitted ones, both in the plan's original order."""
    if len(queries) <= bound:
        return queries, ()
    by_language: dict[str | None, dict[str, list[int]]] = {}
    for index, query in enumerate(queries):
        groups = by_language.setdefault(query.language, {})
        groups.setdefault(query.question_ids[0], []).append(index)
    order = [code for code in languages if code in by_language] + [
        code for code in by_language if code not in languages
    ]

    def interleave(lanes: list[list[int]]) -> list[int]:
        merged: list[int] = []
        for depth in range(max((len(lane) for lane in lanes), default=0)):
            merged.extend(lane[depth] for lane in lanes if depth < len(lane))
        return merged

    per_language = [interleave(list(by_language[code].values())) for code in order]
    chosen = set(interleave(per_language)[:bound])
    return (
        tuple(query for index, query in enumerate(queries) if index in chosen),
        tuple(query for index, query in enumerate(queries) if index not in chosen),
    )


def language_queries(
    questions: tuple[Question, ...],
    queries: tuple[SearchQuery, ...],
    languages: tuple[str, ...],
    max_query_chars: int,
) -> tuple[SearchQuery, ...]:
    """One query per (question, preferred language) the plan did not cover.

    The query reuses the plan's first query for that question (or the question
    text) with the language set, so a language-filtering provider retrieves
    sources in that language. Nothing is translated here; translation is the
    planner's job and its own per-language queries always take precedence.
    """
    added: list[SearchQuery] = []
    for question in questions:
        own = [query for query in queries if question.id in query.question_ids]
        for language in languages:
            if any(query.language == language for query in own):
                continue
            base = own[0] if own else None
            added.append(
                SearchQuery(
                    text=base.text if base is not None else question.text[:max_query_chars],
                    question_ids=(question.id,),
                    graph_refs=base.graph_refs if base is not None else (),
                    language=language,
                )
            )
    return tuple(dict.fromkeys(added))


class ResearchResult(ResearchRecord):
    schema_version: Literal[
        "chimera.research-result/1", "chimera.research-result/2", "chimera.research-result/3"
    ] = Field(alias="schema")
    status: Literal["answered", "partial", "failed"]
    stop_reason: Literal["answered", "rounds_exhausted", "failed", "saturated"]
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
    retrieval: ResearchRetrievalReport | None = Field(default=None, exclude_if=lambda v: v is None)

    @property
    def evidence_documents(self) -> tuple[Document, ...]:
        # Raw bytes or URL alone cannot distinguish different retained readings.
        retained = self.retrieval.documents if self.retrieval is not None else ()
        originals = {
            BoundCorpusDocument(doc).identity: doc
            for doc in self.harvest.source_documents + retained
        }
        return tuple(originals.values())

    @model_validator(mode="after")
    def bound_retrieval(self) -> "ResearchResult":
        research = self.harvest.receipt.effective_config.research
        configured = research.retained_evidence if research is not None else None
        if (
            (configured is not None) != (self.retrieval is not None)
            or (self.retrieval is not None) != (self.schema_version == "chimera.research-result/3")
            or (
                self.retrieval is not None
                and (
                    self.retrieval.policy != configured
                    or self.retrieval.intent != self.harvest.goal.text
                )
            )
        ):
            raise ValueError(
                "retained research requires its exact intent, policy and result schema"
            )
        if self.retrieval is not None:
            self.retrieval.validate_run(self.harvest.receipt.effective_config, self.harvest.ledger)
            originals = {
                item.origin.document_sha256: item for item in self.retrieval.graph_originals
            }
            admitted = self.harvest.retained_sources
            if any(originals.get(item.origin.document_sha256) != item for item in admitted):
                raise ValueError("graph origins must bind the exact actual retrieval snapshot")
            if (
                self.harvest.graph is not None
                and self.status == "answered"
                and tuple(originals.values()) != admitted
            ):
                raise ValueError("answered graph research cannot silently omit retained originals")
            ids = {question.id for question in self.questions}
            for round_ in self.rounds:
                if round_.assessment is None:
                    continue
                coverage = round_.assessment.coverage
                if (
                    {item.question_id for item in coverage} != ids
                    or len(coverage) != len(ids)
                    or any(
                        not any(citation.matches(doc) for doc in self.evidence_documents)
                        for item in coverage
                        for citation in item.citations
                    )
                ):
                    raise ValueError("retained research assessments require source-bound coverage")
        return self

    @model_validator(mode="after")
    def retained_discovery(self) -> "ResearchResult":
        if self.schema_version == "chimera.research-result/1":
            if self.harvest.receipt.effective_config.discovery is not None or isinstance(
                self.harvest.receipt.effective_config.search, (AhmiaConfig, CorpusSearchConfig)
            ):
                raise ValueError("bound discovery results require retained discovery responses")
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
        discovery = self.harvest.receipt.effective_config.discovery
        identities = (
            {provider.identity for provider in discovery.providers}
            if discovery
            else {(self.search_provider, self.search_revision)}
        )
        if (
            discovery is not None
            and (self.search_provider, self.search_revision) != discovery.identity
        ):
            raise ValueError("discovery result requires its original strategy identity")
        single_binding = self.harvest.receipt.effective_config.search
        if (
            isinstance(single_binding, (AhmiaConfig, CorpusSearchConfig))
            and (self.search_provider, self.search_revision) != single_binding.identity
        ):
            raise ValueError("retained index result requires its recorded binding identity")
        if discovery is not None and any(
            row.route not in {f"search:{name}@{revision}" for name, revision in identities}
            for row in attempts
        ):
            raise ValueError("every discovery attempt must bind a configured provider")
        for observation in self.search_observations:
            row = rows[observation.sequence]
            response = observation.response
            if (
                (observation.provider, observation.provider_revision) not in identities
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
            binding = (
                next(
                    (
                        p.binding
                        for p in discovery.providers
                        if p.identity == (observation.provider, observation.provider_revision)
                    ),
                    None,
                )
                if discovery is not None
                else self.harvest.receipt.effective_config.search
            )
            if isinstance(binding, AhmiaConfig):
                if any(hit.index_evidence is None for hit in response.hits):
                    raise ValueError("retained Ahmia hits require their index observations")
                # The original response and binding, not a model, own normalized leads.
                if response.index_retrieved_at is None:
                    raise ValueError(
                        "Ahmia response requires its retrieval timestamp, even when empty"
                    )
                native = decode_ahmia(
                    response.raw,
                    binding,
                    limit=policy.results_per_query,
                    retrieved_at=response.index_retrieved_at,
                )
                expected = tuple(
                    SearchHit(
                        url=lead.url,
                        title=lead.title,
                        snippet=lead.snippet,
                        index_evidence=lead.index_evidence,
                    )
                    for lead in native
                )
                if expected != response.hits:
                    raise ValueError("retained Ahmia hits must match the native index response")
            if isinstance(binding, CorpusSearchConfig):
                from ghimera.corpus_search import corpus_leads

                if len(response.raw) > binding.max_response_bytes or response.transport is not None:
                    raise ValueError(
                        "corpus discovery is bounded local evidence, not HTTP source transport"
                    )
                native_corpus = CorpusSearchWire.model_validate_json(response.raw)
                native_corpus.validate_policy(binding, observation.query.text)
                if native_corpus.query.reranking is not None:
                    from ghimera.research_reranking import validate_run_evidence

                    learned = native_corpus.query.reranking
                    validate_run_evidence(
                        self.harvest.receipt.effective_config,
                        self.harvest.ledger,
                        learned.request,
                        learned.scores,
                        native_corpus.query.reranking_run,
                        channel="discovery",
                        before_sequence=observation.sequence,
                    )
                expected_corpus = corpus_leads(native_corpus, binding)
                if discovery is not None:
                    selected_provider = next(
                        p
                        for p in discovery.providers
                        if p.identity == (observation.provider, observation.provider_revision)
                    )
                    domains = set(selected_provider.domains) & set(discovery.target_domains)
                    expected_corpus = tuple(
                        hit
                        for hit in expected_corpus
                        if (
                            "onion"
                            if (urlsplit(hit.url).hostname or "").endswith(".onion")
                            else "open_web"
                        )
                        in domains
                    )
                if response.index_retrieved_at is not None or response.hits != expected_corpus:
                    raise ValueError(
                        "corpus leads must match their retained original sources and query"
                    )
        if discovery is not None:
            for provider in discovery.providers:
                provider_rows = tuple(
                    row
                    for row in attempts
                    if row.route == f"search:{provider.identity[0]}@{provider.identity[1]}"
                )
                if any(
                    row.bytes_read > provider.max_response_bytes for row in provider_rows
                ) or any(
                    len(obs.response.hits) > provider.max_results
                    for obs in self.search_observations
                    if (obs.provider, obs.provider_revision) == provider.identity
                ):
                    raise ValueError("discovery evidence exceeds its provider limits")
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
        if policy.graph_context is not None:
            plans = tuple(row for row in self.harvest.ledger if row.event == "plan")
            # A plan fitted to the round bounds keeps the raw response digest on its
            # plan row; the journaled plan_fitted row binds raw -> fitted.
            fitted = fitted_plans(self.harvest.ledger)
            for round_ in self.rounds:
                if not any(
                    row.planning_graph is not None
                    and all(
                        set(query.graph_refs) <= row.planning_graph.references
                        for query in round_.queries
                    )
                    and row.reason is not None
                    and row.reason.removeprefix("model_response:")
                    in planned_digests(
                        ResearchPlan(
                            questions=self.questions,
                            queries=round_.queries,
                            model_call=row.model_call,
                        ).content_digest(),
                        fitted,
                    )
                    for row in plans
                ):
                    raise ValueError(
                        "research queries must bind the observed graph-aware planning response"
                    )
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
            for claim in self.answer.claims:
                for citation in claim.citations:
                    if not any(citation.matches(doc) for doc in self.evidence_documents):
                        raise ValueError("answer citations must match retained native evidence")
        elif self.answer is not None:
            raise ValueError("partial or failed research must not expose a completed answer")
        return self
