"""Intent → grounded discovery → shared collection → cited, reviewed answer.

Model ports never supply source URLs. Original intent and question identities
remain fixed across rounds; native-text citations are checked before review.
Neither a grade nor the synthesizer's confidence is a completion decision.
"""

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol, TypeVar
from urllib.parse import urlsplit

from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.continuation import CheckpointStore, ResearchCheckpoint, ResearchSuspended
from ghimera.corpus_evidence import CorpusEvidenceReader
from ghimera.corpus_search_config import CorpusSearchConfig
from ghimera.corpus_types import BoundCorpusDocument
from ghimera.discovery import DiscoveryProviders
from ghimera.discovery_config import DiscoveryProgress
from ghimera.graph_planning import build_context
from ghimera.graph_planning_types import PlanningGraph
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
from ghimera.research_reuse import RetainedResearchSession, RetainedSourceNotice
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
    SearchObservation,
    SearchQuery,
)
from ghimera.search import GroundedSearch
from ghimera.search_history import SearchHistory

T = TypeVar("T", bound=ResearchModelResult)
R = TypeVar("R", bound=ResearchRecord)
ModelEvent = Literal["plan", "assessment", "answer", "review"]


class UnsupportedReview(GhimeraRefused):
    """Preserve the actual rejected review while withholding the proposed answer."""

    def __init__(self, review: AnswerReview) -> None:
        self.review = review
        super().__init__(RefusalCode.UNSUPPORTED_ANSWER)


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
    return Citation.from_document(document, start, end)


class CitationValidator:
    def __init__(self, documents: tuple[Document, ...]) -> None:
        self._documents = documents

    def validate(self, citations: tuple[Citation, ...]) -> None:
        for citation in citations:
            if not any(citation.matches(doc) for doc in self._documents):
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

    @property
    def hosts(self) -> tuple[str, ...]:
        return tuple(sorted(self._hosts))

    def restore_hosts(self, checkpoint: ResearchCheckpoint) -> None:
        observed = {
            urlsplit(row.url).hostname
            for row in checkpoint.progress.harvest.ledger
            if row.event == "discovery" and row.url is not None
        } | {urlsplit(seed).hostname for seed in checkpoint.request.seeds}
        allowed = (
            set(self._policy.allowed_hosts) | observed | set(checkpoint.session.reference_hosts)
        )
        hosts = set(checkpoint.admitted_hosts)
        if (
            len(hosts) != len(checkpoint.admitted_hosts)
            or len(hosts) > self._policy.max_source_hosts
            or not hosts <= allowed
            or hosts & set(self._policy.denied_hosts)
            or (
                self._policy.source_policy == "configured_only"
                and not hosts <= set(self._policy.allowed_hosts)
            )
        ):
            raise ValueError("restored discovery scope requires original admitted hosts")
        self._hosts = hosts


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
                    planning_graph=request.graph_context
                    if isinstance(request, PlanningRequest)
                    else None,
                )
            )


class ResearchLoop:
    def __init__(
        self,
        *,
        config: GhimeraConfig,
        collector: GoalLoop,
        search: GroundedSearch | DiscoveryProviders,
        planner: IntentPlanner,
        analyst: ResearchAnalyst,
        reviewer: AnswerReviewer,
        retained_reader: CorpusEvidenceReader | None = None,
    ) -> None:
        policy = config.research
        if policy is None or collector.config != config:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        if (policy.retained_evidence is not None) != (retained_reader is not None) or (
            policy.retained_evidence is not None
            and retained_reader is not None
            and retained_reader.policy != policy.retained_evidence.reader
        ):
            raise ValueError("retained research requires its exact configured corpus reader")
        if isinstance(search, DiscoveryProviders):
            if config.discovery != search.policy:
                raise ValueError("discovery ports require the exact effective strategy recipe")
        elif config.discovery is not None:
            raise ValueError("a discovery recipe requires its bound provider set")
        if (
            isinstance(config.search, CorpusSearchConfig)
            and search.identity != config.search.identity
        ):
            raise ValueError("corpus discovery requires its exact configured search identity")
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
        self._retained_reader = retained_reader
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

    def _validate_plan(
        self,
        plan: ResearchPlan,
        questions: tuple[Question, ...],
        context: PlanningGraph | None = None,
    ) -> None:
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
                or not set(query.graph_refs)
                <= (context.references if context is not None else frozenset())
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
                    observations = await history.discover_many(query)
                    return tuple(
                        hit.url for observation in observations for hit in observation.response.hits
                    )
                except GhimeraRefused as exc:
                    if exc.code == RefusalCode.BUDGET_EXHAUSTED:
                        raise
                    self._refuse(session, exc.code)
                    return ()

        completed = await asyncio.gather(
            *(search(query) for query in queries), return_exceptions=True
        )
        batches: list[tuple[str, ...]] = []
        for outcome in completed:
            if isinstance(outcome, BaseException):
                raise outcome
            batches.append(outcome)
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
                            reason=f"grounded:{self._search.identity[0]}@{self._search.identity[1]}",
                        )
                    )
                    if session.graph is not None:
                        await session.graph.discovered(url, trace[query.content_digest()])
                else:
                    self._refuse(session, RefusalCode.OUT_OF_SCOPE, url=url)
        return tuple(accepted)

    def _validate_suspend(self, suspend_after_rounds: int | None) -> None:
        if suspend_after_rounds is not None and (
            type(suspend_after_rounds) is not int
            or suspend_after_rounds <= 0
            or self._config.continuation is None
        ):
            raise ValueError("round suspension requires a positive count and continuation policy")

    async def run(
        self,
        request: ResearchRequest,
        *,
        run_id: str | None = None,
        suspend_after_rounds: int | None = None,
    ) -> ResearchResult:
        self._validate_suspend(suspend_after_rounds)
        if self._config.continuation is not None and run_id is None:
            raise ValueError("durable continuation requires an explicit run identity")
        request = ResearchRequest.model_validate(request.model_dump())
        session = await self._collector.open(
            Goal(text=request.intent, seeds=request.seeds), run_id=run_id
        )
        try:
            return await self._drive(
                request, session, run_id=run_id, suspend_after_rounds=suspend_after_rounds
            )
        finally:
            session.ledger.close()

    async def resume(
        self,
        run_id: str,
        *,
        checkpoint_sha256: str,
        suspend_after_rounds: int | None = None,
    ) -> ResearchResult:
        self._validate_suspend(suspend_after_rounds)
        checkpoint = CheckpointStore(self._config, run_id).read(checkpoint_sha256)
        progress = checkpoint.progress
        if (
            progress.planner,
            progress.analyst,
            progress.reviewer,
            progress.search_provider,
            progress.search_revision,
        ) != (
            self._planner.model,
            self._analyst.model,
            self._reviewer.model,
            self._search.identity[0],
            self._search.identity[1],
        ):
            raise ValueError("continuation requires the original bound model and search identities")
        downtime = time.time() - checkpoint.saved_at
        if downtime < 0:
            raise ValueError("wall clock moved backwards since the checkpoint")
        compiler = ResearchScopeCompiler(self._policy)
        compiler.restore_hosts(checkpoint)
        session = await self._collector.restore(
            run_id,
            progress.harvest,
            checkpoint.session,
            search_calls=progress.search_calls,
            downtime_seconds=downtime,
        )
        try:
            return await self._drive(
                checkpoint.request,
                session,
                run_id=run_id,
                checkpoint=checkpoint,
                suspend_after_rounds=suspend_after_rounds,
            )
        finally:
            session.ledger.close()

    @staticmethod
    def _documents(
        session: CollectionSession, reuse: RetainedResearchSession | None
    ) -> tuple[Document, ...]:
        if reuse is None:
            return session.evidence_documents
        originals = {
            BoundCorpusDocument(doc).identity: doc
            for doc in session.evidence_documents + reuse.report.documents
        }
        return tuple(originals.values())

    @staticmethod
    def _notices(reuse: RetainedResearchSession | None) -> tuple[RetainedSourceNotice, ...]:
        return reuse.report.notices if reuse is not None else ()

    async def _retrieve(
        self, session: CollectionSession, reuse: RetainedResearchSession | None, text: str
    ) -> None:
        if reuse is None:
            return
        # Exhausting the separate cache-search allowance does not exhaust source
        # discovery. A global wall-budget expiry still stops all further work.
        if session.budget.remaining_seconds <= 0:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        try:
            await reuse.query(text, remaining_seconds=session.budget.remaining_seconds)
        except GhimeraRefused as exc:
            self._refuse(session, exc.code)
            if session.budget.remaining_seconds <= 0:
                raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED) from exc
        if session.graph is not None:
            for original in reuse.report.graph_originals:
                await self._collector.admit_retained(session, original)

    async def _assess(
        self,
        session: CollectionSession,
        reuse: RetainedResearchSession | None,
        request: ResearchRequest,
        questions: tuple[Question, ...],
    ) -> Assessment:
        documents = self._documents(session, reuse)
        evidence = EvidenceRequest(
            intent=request.intent,
            questions=questions,
            documents=documents,
            retained_sources=self._notices(reuse),
        )
        result = await ModelCalls(session, self._policy).invoke(
            "assessment", self._analyst.model, evidence, self._analyst.assess
        )
        self._validate_assessment(result, questions, documents)
        return result

    async def _answer(
        self,
        session: CollectionSession,
        request: ResearchRequest,
        questions: tuple[Question, ...],
        assessment: Assessment,
        reuse: RetainedResearchSession | None = None,
    ) -> tuple[AnswerDraft, AnswerReview]:
        calls = ModelCalls(session, self._policy)
        answering = AnswerRequest(
            intent=request.intent,
            questions=questions,
            documents=self._documents(session, reuse),
            retained_sources=self._notices(reuse),
            assessment=assessment,
        )
        candidate = await calls.invoke(
            "answer", self._analyst.model, answering, self._analyst.answer
        )
        self._validate_answer(candidate, questions, self._documents(session, reuse))
        reviewing = ReviewRequest(
            intent=request.intent,
            questions=questions,
            documents=self._documents(session, reuse),
            retained_sources=self._notices(reuse),
            answer=candidate,
        )
        checked = await calls.invoke(
            "review", self._reviewer.model, reviewing, self._reviewer.review
        )
        if (
            checked.answer_digest != candidate.content_digest()
            or not checked.intent_covered
            or len(checked.claims) != len(candidate.claims)
            or {item.index for item in checked.claims} != set(range(len(candidate.claims)))
            or any(item.verdict != "supported" for item in checked.claims)
            or candidate.confidence < self._policy.min_answer_confidence
        ):
            raise UnsupportedReview(checked)
        return candidate, checked

    def _save(
        self,
        request: ResearchRequest,
        session: CollectionSession,
        run_id: str | None,
        compiler: ResearchScopeCompiler,
        history: SearchHistory,
        questions: tuple[Question, ...],
        rounds: list[ResearchRound],
        assessment: Assessment | None,
        *,
        suspend: bool,
        reuse: RetainedResearchSession | None = None,
    ) -> None:
        if self._config.continuation is None:
            return
        if run_id is None:
            raise ValueError("checkpoint requires its original run identity")
        saved_at = time.time()
        progress = ResearchResult(
            schema="chimera.research-result/3"
            if reuse is not None
            else "chimera.research-result/2",
            status="partial",
            stop_reason="rounds_exhausted",
            harvest=self._collector.snapshot(session),
            questions=questions,
            rounds=tuple(rounds),
            unresolved=tuple(question.id for question in questions),
            answer=None,
            review=None,
            planner=self._planner.model,
            analyst=self._analyst.model,
            reviewer=self._reviewer.model,
            search_provider=self._search.identity[0],
            search_revision=self._search.identity[1],
            search_calls=session.budget.search_calls,
            search_observations=history.observations,
            retrieval=reuse.report if reuse is not None else None,
        )
        saved = ResearchCheckpoint(
            schema="ghimera.research-checkpoint/1",
            run_id=run_id,
            saved_at=saved_at,
            request=request,
            progress=progress,
            session=session.checkpoint_state(),
            admitted_hosts=compiler.hosts,
            assessment=assessment,
            next_action="answer"
            if assessment is not None
            and all(item.status == "answered" for item in assessment.coverage)
            else "plan",
        )
        receipt = CheckpointStore(self._config, run_id).write(saved)
        if suspend:
            raise ResearchSuspended(receipt)

    async def _drive(
        self,
        request: ResearchRequest,
        session: CollectionSession,
        *,
        run_id: str | None,
        checkpoint: ResearchCheckpoint | None = None,
        suspend_after_rounds: int | None = None,
    ) -> ResearchResult:
        calls, compiler = ModelCalls(session, self._policy), ResearchScopeCompiler(self._policy)
        reuse = (
            RetainedResearchSession(
                self._policy.retained_evidence,
                self._retained_reader,
                request.intent,
                restored=checkpoint.progress.retrieval if checkpoint is not None else None,
            )
            if self._policy.retained_evidence is not None and self._retained_reader is not None
            else None
        )
        history = SearchHistory(
            self._search,
            session.budget,
            session.ledger,
            restored=checkpoint.progress.search_observations if checkpoint is not None else (),
        )
        questions = checkpoint.progress.questions if checkpoint is not None else ()
        rounds = list(checkpoint.progress.rounds) if checkpoint is not None else []
        initial_rounds = len(rounds)
        assessment = checkpoint.assessment if checkpoint is not None else None
        # A rejected retained-only answer must seek new evidence next, not use
        # the same apparently complete assessment to skip discovery again.
        allow_retained_completion = True
        answer: AnswerDraft | None = None
        review: AnswerReview | None = None
        reason: Literal["answered", "rounds_exhausted", "budget_exhausted", "failed"] = (
            "rounds_exhausted"
        )
        if checkpoint is not None:
            compiler.restore_hosts(checkpoint)
            if checkpoint.next_action == "answer" and assessment is not None:
                try:
                    answer, review = await self._answer(
                        session, request, questions, assessment, reuse
                    )
                    reason = "answered"
                except GhimeraRefused as exc:
                    self._refuse(session, exc.code)
                    if isinstance(exc, UnsupportedReview):
                        review = exc.review
                    if exc.code == RefusalCode.UNSUPPORTED_ANSWER:
                        assessment = None
                        allow_retained_completion = False
                    else:
                        reason = (
                            "budget_exhausted"
                            if exc.code == RefusalCode.BUDGET_EXHAUSTED
                            else "failed"
                        )
        else:
            try:
                await self._collector.import_local(session, request.local_documents)
                await self._retrieve(session, reuse, request.intent)
            except GhimeraRefused as exc:
                self._refuse(session, exc.code)
                reason = (
                    "budget_exhausted" if exc.code == RefusalCode.BUDGET_EXHAUSTED else "failed"
                )
        for number in range(initial_rounds + 1, self._policy.max_rounds + 1):
            if reason in {"failed", "budget_exhausted", "answered"}:
                break
            try:
                planning = PlanningRequest(
                    intent=request.intent,
                    questions=questions,
                    documents=self._documents(session, reuse),
                    retained_sources=self._notices(reuse),
                    assessment=assessment,
                    max_questions=self._policy.max_questions,
                    max_queries=self._policy.max_queries_per_round,
                    max_query_chars=self._policy.max_query_chars,
                    graph_context=build_context(self._config, session.ledger.snapshot()),
                )
                plan = await calls.invoke("plan", self._planner.model, planning, self._planner.plan)
                self._validate_plan(plan, questions, planning.graph_context)
                questions = plan.questions
                trace = await self._trace_plan(session, plan)
                compiler.include_reference_hosts(session.reference_hosts)
                history.set_rounds(tuple(rounds))
                before_documents = {doc.sha256 for doc in session.evidence_documents}
                prior_assessment = rounds[-1].assessment if rounds else assessment
                before_answers = (
                    {
                        item.question_id
                        for item in prior_assessment.coverage
                        if item.status == "answered"
                    }
                    if prior_assessment
                    else set()
                )
                for query in plan.queries:
                    await self._retrieve(session, reuse, query.text)
                if (
                    reuse is not None
                    and reuse.report.documents
                    and reuse.report.policy.assess_before_discovery
                    and allow_retained_completion
                    and not request.seeds
                ):
                    assessment = await self._assess(session, reuse, request, questions)
                    if questions and all(item.status == "answered" for item in assessment.coverage):
                        rounds.append(
                            ResearchRound(
                                number=number,
                                queries=plan.queries,
                                discovered_urls=(),
                                assessment=assessment,
                                collection_stop="retained_evidence",
                            )
                        )
                        self._save(
                            request,
                            session,
                            run_id,
                            compiler,
                            history,
                            questions,
                            rounds,
                            assessment,
                            suspend=suspend_after_rounds is not None
                            and len(rounds) - initial_rounds >= suspend_after_rounds,
                            reuse=reuse,
                        )
                        allow_retained_completion = False
                        answer, review = await self._answer(
                            session, request, questions, assessment, reuse
                        )
                        reason = "answered"
                        break
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
                assessment = await self._assess(session, reuse, request, questions)
                rounds.append(
                    ResearchRound(
                        number=number,
                        queries=plan.queries,
                        discovered_urls=urls,
                        assessment=assessment,
                        collection_stop=collection_stop,
                        discovery_progress=DiscoveryProgress(
                            new_documents=len(
                                {doc.sha256 for doc in session.evidence_documents}
                                - before_documents
                            ),
                            new_answers=len(
                                {
                                    item.question_id
                                    for item in assessment.coverage
                                    if item.status == "answered"
                                }
                                - before_answers
                            ),
                        )
                        if self._config.discovery is not None
                        else None,
                    )
                )
                self._save(
                    request,
                    session,
                    run_id,
                    compiler,
                    history,
                    questions,
                    rounds,
                    assessment,
                    suspend=suspend_after_rounds is not None
                    and len(rounds) - initial_rounds >= suspend_after_rounds,
                    reuse=reuse,
                )
                if not questions or any(item.status != "answered" for item in assessment.coverage):
                    continue
                answer, review = await self._answer(session, request, questions, assessment, reuse)
                reason = "answered"
                break
            except GhimeraRefused as exc:
                self._refuse(session, exc.code)
                if isinstance(exc, UnsupportedReview):
                    review = exc.review
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
            schema="chimera.research-result/3"
            if reuse is not None
            else "chimera.research-result/2",
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
            search_provider=self._search.identity[0],
            search_revision=self._search.identity[1],
            search_calls=session.budget.search_calls,
            search_observations=history.observations,
            retrieval=reuse.report if reuse is not None else None,
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
        parents: list[tuple[Document, SearchQuery]] = []
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
            parents.append((source, query))
        semaphore = asyncio.Semaphore(self._policy.search_concurrency)

        async def search(
            source: Document, query: SearchQuery
        ) -> tuple[tuple[SearchObservation, int], ...]:
            sequences: dict[tuple[str, str], int] = {}

            def before_call(provider: GroundedSearch) -> None:
                if (
                    sum(row.event == "reference_query" for row in session.ledger.snapshot())
                    >= policy.cited_by_query_budget
                ):
                    session.ledger.append(
                        LedgerRow(
                            sequence=session.ledger.next_sequence,
                            event="policy",
                            query=query.text,
                            reason="cited_by_query_budget_exhausted",
                        )
                    )
                    raise GhimeraRefused(RefusalCode.SEARCH_UNAVAILABLE)
                sequence = session.ledger.next_sequence
                sequences[provider.identity] = sequence
                session.ledger.append(
                    LedgerRow(
                        sequence=sequence,
                        event="reference_query",
                        url=source.url,
                        query=query.text,
                        reason="candidate_citing_sources",
                        reference_query=ReferenceQuery(
                            schema="chimera.reference-query/1",
                            source=ReferenceSource(
                                url=source.url,
                                sha256=source.sha256,
                                text_sha256=hashlib.sha256(
                                    source.extracted.text.encode()
                                ).hexdigest(),
                            ),
                            parent_hops=session.reference_hops(source.url),
                            origin_url=session.reference_origin(source.url),
                            query=query.text,
                            provider=provider.identity[0],
                            provider_revision=provider.identity[1],
                        ),
                    )
                )

            async with semaphore:
                try:
                    observations = await history.discover_many(query, before_call=before_call)
                    return tuple(
                        (obs, sequences[(obs.provider, obs.provider_revision)])
                        for obs in observations
                    )
                except GhimeraRefused as exc:
                    if exc.code == RefusalCode.BUDGET_EXHAUSTED:
                        raise
                    self._refuse(session, exc.code)
                    return ()

        completed = await asyncio.gather(
            *(search(source, query) for source, query in parents), return_exceptions=True
        )
        batches: list[tuple[tuple[SearchObservation, int], ...]] = []
        for outcome in completed:
            if isinstance(outcome, BaseException):
                raise outcome
            batches.append(outcome)
        accepted: list[str] = []
        for (source, query), observations in zip(parents, batches, strict=True):
            proofs: dict[tuple[str, str], SearchReference] = {}
            for observation, sequence in observations:
                for hit in observation.response.hits:
                    try:
                        proof = SearchReference(
                            schema="chimera.search-reference/1",
                            target_url=hit.url,
                            anchor=hit.title,
                            snippet=hit.snippet,
                            provider=observation.provider,
                            provider_revision=observation.provider_revision,
                            query=query.text,
                            response_sha256=hashlib.sha256(observation.response.raw).hexdigest(),
                            query_sequence=sequence,
                        )
                    except ValidationError:
                        self._refuse(session, RefusalCode.OUT_OF_SCOPE, url=hit.url)
                        continue
                    proofs.setdefault((proof.target_url, proof.anchor), proof)
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
