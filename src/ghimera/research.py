"""Intent → grounded discovery → shared collection → cited, reviewed answer.

Model ports never supply source URLs. Original intent and question identities
remain fixed across rounds; native-text citations are checked before review.
Neither a grade nor the synthesizer's confidence is a completion decision.
"""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol, TypeVar
from urllib.parse import urlsplit

from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.loop import CollectionSession, GoalLoop
from ghimera.model_types import ModelCallEvidence
from ghimera.models import (
    Document,
    Goal,
    LedgerRow,
    LinkCandidate,
    ModelIdentity,
    Scope,
    StopReason,
)
from ghimera.reference_types import ReferenceQuery, ReferenceSource, SearchReference
from ghimera.refusals import GhimeraRefused, ModelCancelled, ModelFailure, RefusalCode
from ghimera.research_config import ResearchConfig
from ghimera.research_types import (
    AnswerDraft,
    AnswerRequest,
    AnswerReview,
    Assessment,
    Citation,
    EvidenceRequest,
    PlanningRequest,
    Question,
    ResearchModelResult,
    ResearchPlan,
    ResearchRecord,
    ResearchRequest,
    ResearchResult,
    ResearchRound,
    ReviewRequest,
    SearchQuery,
    SearchResponse,
)
from ghimera.search import GroundedSearch
from ghimera.search_history import SearchHistory

T = TypeVar("T", bound=ResearchModelResult)
R = TypeVar("R", bound=ResearchRecord)
ModelEvent = Literal["plan", "assessment", "answer", "review"]


class IntentPlanner(Protocol):
    @property
    def model(self) -> ModelIdentity: ...

    async def plan(self, request: PlanningRequest) -> ResearchPlan: ...


class ResearchAnalyst(Protocol):
    @property
    def model(self) -> ModelIdentity: ...

    async def assess(self, request: EvidenceRequest) -> Assessment: ...

    async def answer(self, request: AnswerRequest) -> AnswerDraft: ...


class AnswerReviewer(Protocol):
    @property
    def model(self) -> ModelIdentity: ...

    async def review(self, request: ReviewRequest) -> AnswerReview: ...


def citation_for(document: Document, start: int, end: int) -> Citation:
    text = document.extracted.text
    return Citation(
        document_id="doc:" + document.sha256,
        source_url=document.url,
        document_sha256=document.sha256,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        start=start,
        end=end,
        quote=text[start:end],
    )


class CitationValidator:
    def __init__(self, documents: tuple[Document, ...]) -> None:
        self._documents = {(doc.sha256, doc.url): doc for doc in documents}

    def validate(self, citations: tuple[Citation, ...]) -> None:
        for citation in citations:
            doc = self._documents.get((citation.document_sha256, citation.source_url))
            if doc is None or not citation.matches(doc):
                raise GhimeraRefused(RefusalCode.UNSUPPORTED_ANSWER)


class ResearchScopeCompiler:
    """Owner policy authorizes discovery; search hits authorize individual URLs."""

    def __init__(self, policy: ResearchConfig) -> None:
        self._policy = policy
        self._hosts = set(policy.allowed_hosts)

    def accept(self, url: str) -> bool:
        try:
            host = urlsplit(url).hostname
            if host is None or host in self._policy.denied_hosts:
                return False
            if self._policy.source_policy == "configured_only" and host not in self._hosts:
                return False
            if host not in self._hosts and len(self._hosts) >= self._policy.max_source_hosts:
                return False
            candidate = Scope(
                allowed_hosts=(host,),
                max_depth=self._policy.max_depth,
                content_types=self._policy.content_types,
                allowed_ports=self._policy.allowed_ports,
            )
            if not candidate.permits(url):
                return False
            self._hosts.add(host)
            return True
        except (ValueError, ValidationError):
            return False

    def include_reference_hosts(self, hosts: tuple[str, ...]) -> None:
        """Reserve previously admitted reference hosts before new discovery.

        Reference admission already enforces this policy against the current
        discovery scope. This handoff prevents follow-up rounds spending the
        same remaining host capacity a second time.
        """
        combined = self._hosts | set(hosts)
        if len(combined) > self._policy.max_source_hosts:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        self._hosts = combined

    def scope(self) -> Scope | None:
        if not self._hosts:
            return None
        return Scope(
            allowed_hosts=tuple(sorted(self._hosts)),
            max_depth=self._policy.max_depth,
            content_types=self._policy.content_types,
            allowed_ports=self._policy.allowed_ports,
        )


class ModelCalls:
    """One accounting owner for all research model phases, including failures."""

    def __init__(self, session: CollectionSession, policy: ResearchConfig) -> None:
        self._session, self._policy = session, policy

    async def invoke(
        self,
        event: ModelEvent,
        model: ModelIdentity,
        request: R,
        call: Callable[[R], Awaitable[T]],
    ) -> T:
        # Documents are retained objects, not the model's serialized context.
        # Concrete model ports apply the same limit to their bounded prompt.
        if len(request.model_dump_json(exclude={"documents"})) > self._policy.max_model_input_chars:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        budget, ledger = self._session.budget, self._session.ledger
        if model.location == "external":
            raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
        budget.reserve_judge()
        started, code, result = budget.clock(), None, None
        model_call: ModelCallEvidence | None = None
        try:
            async with asyncio.timeout(budget.remaining_seconds):
                result = await call(request)
            model_call = result.model_call
            return result
        except TimeoutError:
            code = RefusalCode.BUDGET_EXHAUSTED
            raise GhimeraRefused(code) from None
        except GhimeraRefused as exc:
            code = exc.code
            if isinstance(exc, ModelFailure):
                model_call = exc.model_call
            raise
        except ValidationError:
            code = RefusalCode.MODEL_UNAVAILABLE
            raise GhimeraRefused(code) from None
        except asyncio.CancelledError as exc:
            code = RefusalCode.MODEL_UNAVAILABLE
            if isinstance(exc, ModelCancelled):
                model_call = exc.model_call
            raise
        finally:
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event=event,
                    model=model,
                    model_call=model_call,
                    refusal=code,
                    reason="model_failed"
                    if code
                    else "model_response:" + result.content_digest()
                    if result is not None
                    else "model_cancelled",
                    latency_seconds=max(0.0, budget.clock() - started),
                )
            )


class ResearchLoop:
    def __init__(
        self,
        *,
        config: GhimeraConfig,
        collector: GoalLoop,
        search: GroundedSearch,
        planner: IntentPlanner,
        analyst: ResearchAnalyst,
        reviewer: AnswerReviewer,
    ) -> None:
        policy = config.research
        if policy is None or collector.config != config:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        if any(
            model.location == "external"
            for model in (planner.model, analyst.model, reviewer.model, collector.judge_model)
        ):
            raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
        if policy.require_distinct_reviewer and (
            reviewer.model.model_id,
            reviewer.model.revision,
        ) == (analyst.model.model_id, analyst.model.revision):
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        self._config, self._policy = config, policy
        self._collector, self._search = collector, search
        self._planner, self._analyst, self._reviewer = planner, analyst, reviewer
        if config.graph is not None and config.graph.enabled:
            roles = {role.name for role in config.graph.roles}
            if not {"question", "query"} <= roles:
                raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
            by_name = {relation.name: relation for relation in config.graph.relations}
            for name, source, target in (
                ("question_for", "question", "intent"),
                ("query_for", "query", "question"),
                ("discovered", "source", "query"),
            ):
                relation = by_name.get(name)
                if (
                    relation is None
                    or relation.semantic
                    or source not in relation.source_roles
                    or target not in relation.target_roles
                ):
                    raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)

    async def _trace_plan(self, session: CollectionSession, plan: ResearchPlan) -> dict[str, str]:
        graph = session.graph
        result: dict[str, str] = {}
        if graph is None:
            return result
        questions = {
            question.id: graph.node(
                "question",
                f"{graph.intent_id}:{question.id}:{question.text}",
                question.text,
                self._planner.model.revision,
            )
            for question in plan.questions
        }
        await graph.append(
            nodes=tuple(questions.values()),
            edges=tuple(
                graph.edge("question_for", node.id, graph.intent_id, self._planner.model.revision)
                for node in questions.values()
            ),
        )
        for query in plan.queries:
            node = graph.node(
                "query",
                graph.intent_id + ":" + query.content_digest(),
                query.text,
                self._planner.model.revision,
            )
            await graph.append(
                nodes=(node,),
                edges=tuple(
                    graph.edge(
                        "query_for", node.id, questions[qid].id, self._planner.model.revision
                    )
                    for qid in query.question_ids
                ),
            )
            result[query.content_digest()] = node.id
        return result

    def _validate_plan(self, plan: ResearchPlan, questions: tuple[Question, ...]) -> None:
        ids = {question.id for question in plan.questions}
        if (
            len(ids) != len(plan.questions)
            or len(ids) > self._policy.max_questions
            or any(not question.text.strip() for question in plan.questions)
            or (questions and plan.questions != questions)
            or len(plan.queries) > self._policy.max_queries_per_round
            or any(
                len(query.text) > self._policy.max_query_chars
                or not query.text.strip()
                or not set(query.question_ids) <= ids
                or len(set(query.question_ids)) != len(query.question_ids)
                for query in plan.queries
            )
        ):
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)

    def _validate_assessment(
        self,
        assessment: Assessment,
        questions: tuple[Question, ...],
        documents: tuple[Document, ...],
    ) -> None:
        ids = {question.id for question in questions}
        if {item.question_id for item in assessment.coverage} != ids or len(
            assessment.coverage
        ) != len(ids):
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        validator = CitationValidator(documents)
        for item in assessment.coverage:
            validator.validate(item.citations)

    def _validate_answer(
        self,
        answer: AnswerDraft,
        questions: tuple[Question, ...],
        documents: tuple[Document, ...],
    ) -> None:
        ids = {question.id for question in questions}
        referenced = {qid for claim in answer.claims for qid in claim.question_ids}
        if (
            referenced != ids
            or sum(len(claim.text) for claim in answer.claims) > self._policy.max_answer_chars
        ):
            raise GhimeraRefused(RefusalCode.UNSUPPORTED_ANSWER)
        validator = CitationValidator(documents)
        for claim in answer.claims:
            validator.validate(claim.citations)

    def _refuse(
        self, session: CollectionSession, code: RefusalCode, *, url: str | None = None
    ) -> None:
        session.ledger.append(
            LedgerRow(
                sequence=session.ledger.next_sequence,
                event="refusal",
                refusal=code,
                url=url,
                reason=code.value,
            )
        )

    async def _discover(
        self,
        session: CollectionSession,
        queries: tuple[SearchQuery, ...],
        compiler: ResearchScopeCompiler,
        trace: dict[str, str],
        history: SearchHistory,
    ) -> tuple[str, ...]:
        semaphore = asyncio.Semaphore(self._policy.search_concurrency)

        async def search(query: SearchQuery) -> tuple[str, ...]:
            async with semaphore:
                try:
                    response = await history.discover(query)
                    return tuple(hit.url for hit in response.hits)
                except GhimeraRefused as exc:
                    self._refuse(session, exc.code)
                    return ()

        batches = await asyncio.gather(*(search(query) for query in queries))
        accepted: dict[str, None] = {}
        for query, batch in zip(queries, batches, strict=True):
            for url in batch:
                if compiler.accept(url):
                    accepted[url] = None
                    session.ledger.append(
                        LedgerRow(
                            sequence=session.ledger.next_sequence,
                            event="discovery",
                            url=url,
                            reason=f"grounded:{self._search.name}@{self._search.revision}",
                        )
                    )
                    if session.graph is not None:
                        await session.graph.discovered(url, trace[query.content_digest()])
                else:
                    self._refuse(session, RefusalCode.OUT_OF_SCOPE, url=url)
        return tuple(accepted)

    async def run(self, request: ResearchRequest, *, run_id: str | None = None) -> ResearchResult:
        request = ResearchRequest.model_validate(request.model_dump())
        session = await self._collector.open(
            Goal(text=request.intent, seeds=request.seeds), run_id=run_id
        )
        calls, compiler = ModelCalls(session, self._policy), ResearchScopeCompiler(self._policy)
        history = SearchHistory(self._search, session.budget, session.ledger)
        questions: tuple[Question, ...] = ()
        rounds: list[ResearchRound] = []
        assessment = None
        answer = None
        review = None
        reason: Literal["answered", "rounds_exhausted", "budget_exhausted", "failed"] = (
            "rounds_exhausted"
        )
        try:
            await self._collector.import_local(session, request.local_documents)
        except GhimeraRefused as exc:
            self._refuse(session, exc.code)
            reason = "budget_exhausted" if exc.code == RefusalCode.BUDGET_EXHAUSTED else "failed"
        for number in range(1, self._policy.max_rounds + 1):
            if reason in {"failed", "budget_exhausted"}:
                break
            try:
                planning = PlanningRequest(
                    intent=request.intent,
                    questions=questions,
                    documents=session.evidence_documents,
                    assessment=assessment,
                    max_questions=self._policy.max_questions,
                    max_queries=self._policy.max_queries_per_round,
                    max_query_chars=self._policy.max_query_chars,
                )
                plan = await calls.invoke("plan", self._planner.model, planning, self._planner.plan)
                self._validate_plan(plan, questions)
                questions = plan.questions
                trace = await self._trace_plan(session, plan)
                compiler.include_reference_hosts(session.reference_hosts)
                urls = await self._discover(session, plan.queries, compiler, trace, history)
                if number == 1:
                    allowed_seeds: list[str] = []
                    for seed in request.seeds:
                        if compiler.accept(seed):
                            allowed_seeds.append(seed)
                        else:
                            self._refuse(session, RefusalCode.OUT_OF_SCOPE, url=seed)
                    seeds = tuple(allowed_seeds)
                    urls = tuple(dict.fromkeys(seeds + urls))
                scope = compiler.scope()
                collection_stop = "frontier_empty"
                quantum_start = session.budget.fetches
                if scope is not None:
                    collection_stop = await self._collector.collect(
                        session,
                        scope,
                        urls,
                        fetch_limit=self._policy.max_pages_per_round,
                        allow_grade=False,
                    )
                    if collection_stop not in {"failed", "budget_exhausted"}:
                        cited_urls = await self._cited_by(session, scope, questions, history)
                        urls = tuple(dict.fromkeys(urls + cited_urls))
                        remaining = self._policy.max_pages_per_round - (
                            session.budget.fetches - quantum_start
                        )
                        if cited_urls and remaining > 0:
                            collection_stop = await self._collector.collect(
                                session,
                                scope,
                                (),
                                fetch_limit=remaining,
                                allow_grade=False,
                            )
                if collection_stop in {"failed", "budget_exhausted"}:
                    rounds.append(
                        ResearchRound(
                            number=number,
                            queries=plan.queries,
                            discovered_urls=urls,
                            assessment=None,
                            collection_stop=collection_stop,
                        )
                    )
                    reason = "failed" if collection_stop == "failed" else "budget_exhausted"
                    break
                evidence = EvidenceRequest(
                    intent=request.intent, questions=questions, documents=session.evidence_documents
                )
                assessment = await calls.invoke(
                    "assessment",
                    self._analyst.model,
                    evidence,
                    self._analyst.assess,
                )
                self._validate_assessment(assessment, questions, session.evidence_documents)
                rounds.append(
                    ResearchRound(
                        number=number,
                        queries=plan.queries,
                        discovered_urls=urls,
                        assessment=assessment,
                        collection_stop=collection_stop,
                    )
                )
                if not questions or any(item.status != "answered" for item in assessment.coverage):
                    continue
                answering = AnswerRequest(
                    intent=request.intent,
                    questions=questions,
                    documents=session.evidence_documents,
                    assessment=assessment,
                )
                candidate = await calls.invoke(
                    "answer",
                    self._analyst.model,
                    answering,
                    self._analyst.answer,
                )
                self._validate_answer(candidate, questions, session.evidence_documents)
                reviewing = ReviewRequest(
                    intent=request.intent,
                    questions=questions,
                    documents=session.evidence_documents,
                    answer=candidate,
                )
                checked = await calls.invoke(
                    "review",
                    self._reviewer.model,
                    reviewing,
                    self._reviewer.review,
                )
                review = checked
                if (
                    checked.answer_digest != candidate.content_digest()
                    or not checked.intent_covered
                    or len(checked.claims) != len(candidate.claims)
                    or {item.index for item in checked.claims} != set(range(len(candidate.claims)))
                    or any(item.verdict != "supported" for item in checked.claims)
                    or candidate.confidence < self._policy.min_answer_confidence
                ):
                    raise GhimeraRefused(RefusalCode.UNSUPPORTED_ANSWER)
                answer, reason = candidate, "answered"
                break
            except GhimeraRefused as exc:
                self._refuse(session, exc.code)
                if exc.code == RefusalCode.UNSUPPORTED_ANSWER:
                    # A subsequent plan sees a gap, not an apparently complete assessment.
                    assessment = None
                    continue
                reason = (
                    "budget_exhausted" if exc.code == RefusalCode.BUDGET_EXHAUSTED else "failed"
                )
                break
        unresolved = () if answer is not None else tuple(question.id for question in questions)
        stop: StopReason = (
            "goal_satisfied"
            if reason == "answered"
            else "failed"
            if reason == "failed"
            else "budget_exhausted"
            if reason == "budget_exhausted"
            else "frontier_empty"
        )
        harvest = self._collector.finish(session, stop)
        return ResearchResult(
            schema="chimera.research-result/2",
            status="answered"
            if answer is not None
            else "failed"
            if reason == "failed"
            else "partial",
            stop_reason=reason,
            harvest=harvest,
            questions=questions,
            rounds=tuple(rounds),
            unresolved=unresolved,
            answer=answer,
            review=review,
            planner=self._planner.model,
            analyst=self._analyst.model,
            reviewer=self._reviewer.model,
            search_provider=self._search.name,
            search_revision=self._search.revision,
            search_calls=session.budget.search_calls,
            search_observations=history.observations,
        )

    async def _cited_by(
        self,
        session: CollectionSession,
        scope: Scope,
        questions: tuple[Question, ...],
        history: SearchHistory,
    ) -> tuple[str, ...]:
        """Query observed sources, concurrently, then score using native parent context.

        A search result is a candidate citing source, not a verified citation.
        Queries and fetches charge the same run budget as ordinary discovery.
        """
        policy = self._config.references
        if policy is None or not policy.discover_cited_by:
            return ()
        parents: list[tuple[Document, SearchQuery, int]] = []
        for source in session.evidence_documents:
            text = policy.cited_by_query_template.format(
                title=source.extracted.title[: policy.max_query_title_chars],
                url=source.url,
            )
            if len(text) > self._policy.max_query_chars:
                self._refuse(session, RefusalCode.RESEARCH_CONTRACT, url=source.url)
                continue
            if not session.claim_cited_by(source):
                continue
            query = SearchQuery(
                text=text, question_ids=tuple(question.id for question in questions)
            )
            sequence = session.ledger.next_sequence
            session.ledger.append(
                LedgerRow(
                    sequence=sequence,
                    event="reference_query",
                    url=source.url,
                    query=text,
                    reason="candidate_citing_sources",
                    reference_query=ReferenceQuery(
                        schema="chimera.reference-query/1",
                        source=ReferenceSource(
                            url=source.url,
                            sha256=source.sha256,
                            text_sha256=hashlib.sha256(source.extracted.text.encode()).hexdigest(),
                        ),
                        parent_hops=session.reference_hops(source.url),
                        origin_url=session.reference_origin(source.url),
                        query=text,
                        provider=self._search.name,
                        provider_revision=self._search.revision,
                    ),
                )
            )
            parents.append((source, query, sequence))
        semaphore = asyncio.Semaphore(self._policy.search_concurrency)

        async def search(query: SearchQuery) -> SearchResponse | None:
            async with semaphore:
                try:
                    return await history.discover(query)
                except GhimeraRefused as exc:
                    self._refuse(session, exc.code)
                    return None

        batches = await asyncio.gather(*(search(query) for _, query, _ in parents))
        accepted: list[str] = []
        for (source, query, sequence), response in zip(parents, batches, strict=True):
            if response is None:
                continue
            proofs: dict[tuple[str, str], SearchReference] = {}
            for hit in response.hits:
                try:
                    proof = SearchReference(
                        schema="chimera.search-reference/1",
                        target_url=hit.url,
                        anchor=hit.title,
                        snippet=hit.snippet,
                        provider=self._search.name,
                        provider_revision=self._search.revision,
                        query=query.text,
                        response_sha256=hashlib.sha256(response.raw).hexdigest(),
                        query_sequence=sequence,
                    )
                except ValidationError:
                    self._refuse(session, RefusalCode.OUT_OF_SCOPE, url=hit.url)
                    continue
                proofs[(proof.target_url, proof.anchor)] = proof
            ranked = await self._collector.score_discovery(
                session,
                source,
                tuple(LinkCandidate(url=url, anchor=anchor) for url, anchor in proofs),
            )
            for link in ranked[: policy.max_candidates_per_parent]:
                queued = await self._collector.queue_reference(
                    session,
                    source,
                    proofs[(link.url, link.anchor)],
                    link,
                    scope,
                    session.reference_hops(source.url),
                    origin_url=session.reference_origin(source.url),
                )
                if queued:
                    accepted.append(link.url)
        return tuple(dict.fromkeys(accepted))
