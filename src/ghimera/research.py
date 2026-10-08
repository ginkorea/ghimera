"""Intent → grounded discovery → shared collection → cited, reviewed answer.

Model ports never supply source URLs. Original intent and question identities
remain fixed across rounds; native-text citations are checked before review.
Neither a grade nor the synthesizer's confidence is a completion decision.
"""

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol, TypeVar, overload
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
from ghimera.model_work import (
    FatalModelWorkFailure,
    ModelInvocation,
    port_input,
    record_output,
)
from ghimera.model_work_types import ModelStoredOutput
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
from ghimera.research_recovery_store import ResearchRecoveryStore
from ghimera.research_recovery_types import (
    ResearchControlSnapshot,
    ResearchPendingModel,
    ResearchRecoveryModels,
    ResearchRecoveryRead,
)
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

    def restore_hosts(self, checkpoint: ResearchCheckpoint | ResearchControlSnapshot) -> None:
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

    def __init__(
        self,
        session: CollectionSession,
        policy: ResearchConfig,
        *,
        before_call: Callable[[ModelEvent, ModelIdentity, ResearchRecord], None] | None = None,
        recovery: ResearchRecoveryRead | None = None,
    ) -> None:
        self._session, self._policy = session, policy
        self._before_call, self._recovery = before_call, recovery

    @overload
    def replay(
        self,
        event: Literal["plan"],
        model: ModelIdentity,
        request: ResearchRecord,
        *,
        intent_sequence: int,
    ) -> ResearchPlan: ...

    @overload
    def replay(
        self,
        event: Literal["assessment"],
        model: ModelIdentity,
        request: ResearchRecord,
        *,
        intent_sequence: int,
    ) -> Assessment: ...

    @overload
    def replay(
        self,
        event: Literal["answer"],
        model: ModelIdentity,
        request: ResearchRecord,
        *,
        intent_sequence: int,
    ) -> AnswerDraft: ...

    @overload
    def replay(
        self,
        event: Literal["review"],
        model: ModelIdentity,
        request: ResearchRecord,
        *,
        intent_sequence: int,
    ) -> AnswerReview: ...

    def replay(
        self,
        event: ModelEvent,
        model: ModelIdentity,
        request: ResearchRecord,
        *,
        intent_sequence: int,
    ) -> ResearchPlan | Assessment | AnswerDraft | AnswerReview:
        """Read an original return through its owning schema, without a service collaborator.

        Callers retain their normal citation, ontology and independent-review
        checks. A replay record acknowledges a local read, not downstream apply.
        Explicit intent sequence distinguishes identical but separate requests.
        """
        if len(request.model_dump_json(exclude={"documents"})) > self._policy.max_model_input_chars:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        invocation = ModelInvocation(
            self._session.budget,
            self._session.ledger,
            phase=event,
            model=model,
            request=port_input(self._session.budget, request),
            replay_intent_sequence=intent_sequence,
        )

        def decode(
            stored: ModelStoredOutput,
        ) -> ResearchPlan | Assessment | AnswerDraft | AnswerReview:
            body = stored.body()
            if event == "plan":
                result: ResearchPlan | Assessment | AnswerDraft | AnswerReview = (
                    ResearchPlan.model_validate_json(body)
                )
            elif event == "assessment":
                result = Assessment.model_validate_json(body)
            elif event == "answer":
                result = AnswerDraft.model_validate_json(body)
            else:
                result = AnswerReview.model_validate_json(body)
            if result.model_call is not None and (
                result.model_call.service.model_id != model.model_id
                or result.model_call.service.revision != model.revision
            ):
                raise FatalModelWorkFailure("retained research answer changed its original model")
            return result

        return invocation.replay(decode)

    async def invoke(
        self,
        event: ModelEvent,
        model: ModelIdentity,
        request: R,
        call: Callable[[R], Awaitable[T]],
        *,
        result_type: type[T] | None = None,
    ) -> T:
        # Documents are retained objects, not the model's serialized context.
        # Concrete model ports apply the same limit to their bounded prompt.
        if len(request.model_dump_json(exclude={"documents"})) > self._policy.max_model_input_chars:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        budget, ledger = self._session.budget, self._session.ledger
        if model.location == "external":
            raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
        recovery = self._recovery
        if recovery is not None:
            saved = recovery.snapshot
            if (
                saved.phase != event
                or saved.pending_model.model != model
                or port_input(budget, request) != port_input(budget, saved.model_request)
            ):
                raise FatalModelWorkFailure("recovery must continue its exact original model phase")
            self._recovery = None
            if recovery.intent_sequence is not None:
                if result_type is None:
                    raise FatalModelWorkFailure("recovery requires its owning result schema")
                replaying = ModelInvocation(
                    budget,
                    ledger,
                    phase=event,
                    model=model,
                    request=port_input(budget, request),
                    replay_intent_sequence=recovery.intent_sequence,
                )

                def decode(stored: ModelStoredOutput) -> T:
                    return result_type.model_validate_json(stored.body())

                retained_result = replaying.replay(decode)
                # A crash may have happened before the ordinary phase event was
                # appended. Reuse that event if present; never invent another
                # remote invocation or quota reservation.
                if len(recovery.journal.rows) == len(saved.progress.harvest.ledger) + 2:
                    ledger.append(
                        LedgerRow(
                            sequence=ledger.next_sequence,
                            event=event,
                            model=model,
                            model_call=retained_result.model_call,
                            reason="model_response:" + retained_result.content_digest(),
                            planning_graph=request.graph_context
                            if isinstance(request, PlanningRequest)
                            else None,
                        )
                    )
                return retained_result
        if self._before_call is not None:
            self._before_call(event, model, request)
        invocation = ModelInvocation(
            budget,
            ledger,
            phase=event,
            model=model,
            request=port_input(budget, request),
        )
        started, code, result = budget.clock(), None, None
        model_call: ModelCallEvidence | None = None
        try:
            async with asyncio.timeout(budget.remaining_seconds):
                result = await invocation.invoke(lambda: call(request), record_output)
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
        if (
            self._config.continuation is not None or self._config.research_recovery is not None
        ) and run_id is None:
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
            session.close()

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
            session.close()

    async def recover(self, run_id: str, *, snapshot_sha256: str) -> ResearchResult:
        """Adopt one acknowledged model return at its saved native phase.

        Unknown calls and later source/graph effects require reconciliation;
        this is deliberately separate from legacy round-boundary resume.
        """
        policy = self._config.research_recovery
        if policy is None:
            raise ValueError("model-boundary recovery requires explicit configured policy")
        recovery = ResearchRecoveryStore(self._config, run_id, policy).read(
            snapshot_sha256,
            expected_models=ResearchRecoveryModels(
                planner=self._planner.model,
                analyst=self._analyst.model,
                reviewer=self._reviewer.model,
                search_provider=self._search.identity[0],
                search_revision=self._search.identity[1],
            ),
        )
        saved = recovery.snapshot
        downtime = time.time() - saved.saved_at
        if downtime < 0:
            raise ValueError("wall clock moved backwards since the control snapshot")
        ResearchScopeCompiler(self._policy).restore_hosts(saved)
        session = await self._collector.restore(
            run_id,
            saved.progress.harvest,
            saved.session,
            search_calls=saved.progress.search_calls,
            downtime_seconds=downtime,
            model_return=recovery,
        )
        try:
            return await self._drive(saved.request, session, run_id=run_id, recovery=recovery)
        finally:
            session.close()

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
        *,
        calls: ModelCalls | None = None,
    ) -> Assessment:
        documents = self._documents(session, reuse)
        evidence = EvidenceRequest(
            intent=request.intent,
            questions=questions,
            documents=documents,
            retained_sources=self._notices(reuse),
        )
        result = await (calls or ModelCalls(session, self._policy)).invoke(
            "assessment",
            self._analyst.model,
            evidence,
            self._analyst.assess,
            result_type=Assessment,
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
        *,
        calls: ModelCalls | None = None,
        recovered_candidate: AnswerDraft | None = None,
    ) -> tuple[AnswerDraft, AnswerReview]:
        calls = calls or ModelCalls(session, self._policy)
        answering = AnswerRequest(
            intent=request.intent,
            questions=questions,
            documents=self._documents(session, reuse),
            retained_sources=self._notices(reuse),
            assessment=assessment,
        )
        candidate = recovered_candidate or await calls.invoke(
            "answer",
            self._analyst.model,
            answering,
            self._analyst.answer,
            result_type=AnswerDraft,
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
            "review",
            self._reviewer.model,
            reviewing,
            self._reviewer.review,
            result_type=AnswerReview,
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
        recovery: ResearchRecoveryRead | None = None,
        suspend_after_rounds: int | None = None,
    ) -> ResearchResult:
        if checkpoint is not None and recovery is not None:
            raise ValueError("choose one native continuation boundary")
        saved = recovery.snapshot if recovery is not None else None
        progress = (
            checkpoint.progress if checkpoint is not None else saved.progress if saved else None
        )
        compiler = ResearchScopeCompiler(self._policy)
        reuse = (
            RetainedResearchSession(
                self._policy.retained_evidence,
                self._retained_reader,
                request.intent,
                restored=progress.retrieval if progress is not None else None,
            )
            if self._policy.retained_evidence is not None and self._retained_reader is not None
            else None
        )
        history = SearchHistory(
            self._search,
            session.budget,
            session.ledger,
            restored=progress.search_observations if progress is not None else (),
        )
        questions = progress.questions if progress is not None else ()
        rounds = list(progress.rounds) if progress is not None else []
        history.set_rounds(tuple(rounds))
        initial_rounds = len(rounds)
        assessment = (
            checkpoint.assessment if checkpoint is not None else saved.assessment if saved else None
        )
        # A rejected retained-only answer must seek new evidence next, not use
        # the same apparently complete assessment to skip discovery again.
        allow_retained_completion = saved.allow_retained_completion if saved else True
        plan = saved.current_plan if saved else None
        number = saved.round_number if saved else max(1, len(rounds))
        urls = saved.discovered_urls if saved else ()
        collection_stop = saved.collection_stop if saved else "frontier_empty"
        before_documents = set(saved.before_documents) if saved else set()
        before_answers = set(saved.before_answers) if saved else set()
        assessment_context: Literal["post_collection", "retained_first"] = (
            saved.assessment_context if saved else "post_collection"
        )

        def before_model(event: ModelEvent, model: ModelIdentity, inputs: ResearchRecord) -> None:
            policy = self._config.research_recovery
            if policy is None:
                return
            if run_id is None or not isinstance(
                inputs, (PlanningRequest, EvidenceRequest, AnswerRequest, ReviewRequest)
            ):
                raise ValueError("recovery needs an explicit run and exact native phase request")
            wire = port_input(session.budget, inputs)
            current = ResearchResult(
                schema="chimera.research-result/3"
                if reuse is not None
                else "chimera.research-result/2",
                status="partial",
                stop_reason="rounds_exhausted",
                harvest=self._collector.snapshot(session),
                questions=questions,
                rounds=tuple(rounds),
                unresolved=tuple(q.id for q in questions),
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
            snapshot = ResearchControlSnapshot(
                schema="ghimera.research-control-snapshot/1",
                run_id=run_id,
                saved_at=time.time(),
                max_snapshot_bytes=policy.max_snapshot_bytes,
                request=request,
                progress=current,
                session=session.checkpoint_state(),
                admitted_hosts=compiler.hosts,
                phase=event,
                round_number=number,
                model_request=inputs,
                pending_model=ResearchPendingModel(
                    phase=event,
                    model=model,
                    input_sha256=hashlib.sha256(wire).hexdigest(),
                    input_bytes=len(wire),
                ),
                current_plan=plan,
                assessment=assessment,
                current_answer=inputs.answer if isinstance(inputs, ReviewRequest) else None,
                assessment_context=assessment_context,
                discovered_urls=urls,
                collection_stop=collection_stop,
                before_documents=tuple(sorted(before_documents)),
                before_answers=tuple(sorted(before_answers)),
                allow_retained_completion=allow_retained_completion,
            )
            ResearchRecoveryStore(self._config, run_id, policy).write(snapshot)

        calls = ModelCalls(session, self._policy, before_call=before_model, recovery=recovery)
        answer: AnswerDraft | None = None
        review: AnswerReview | None = None
        reason: Literal["answered", "rounds_exhausted", "budget_exhausted", "failed"] = (
            "rounds_exhausted"
        )
        restored_control = checkpoint if checkpoint is not None else saved
        if restored_control is not None:
            compiler.restore_hosts(restored_control)
            answer_ready = (checkpoint is not None and checkpoint.next_action == "answer") or (
                saved is not None and saved.phase in {"answer", "review"}
            )
            if answer_ready and assessment is not None:
                try:
                    answer, review = await self._answer(
                        session,
                        request,
                        questions,
                        assessment,
                        reuse,
                        calls=calls,
                        recovered_candidate=saved.current_answer
                        if saved is not None and saved.phase == "review"
                        else None,
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
                resuming_assessment = (
                    saved is not None
                    and saved.phase == "assessment"
                    and number == saved.round_number
                )
                if not resuming_assessment:
                    planning = (
                        saved.model_request
                        if (
                            saved is not None
                            and saved.phase == "plan"
                            and number == saved.round_number
                            and isinstance(saved.model_request, PlanningRequest)
                        )
                        else PlanningRequest(
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
                    )
                    plan = await calls.invoke(
                        "plan",
                        self._planner.model,
                        planning,
                        self._planner.plan,
                        result_type=ResearchPlan,
                    )
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
                else:
                    if plan is None:
                        raise ValueError("assessment recovery requires its original round plan")
                    # Trace was committed before this boundary. Derive native
                    # identities without appending graph nodes/edges again.
                    trace = (
                        {
                            query.content_digest(): session.graph.node(
                                "query",
                                session.graph.intent_id + ":" + query.content_digest(),
                                query.text,
                                self._planner.model.revision,
                            ).id
                            for query in plan.queries
                        }
                        if session.graph is not None
                        else {}
                    )
                if plan is None:
                    raise ValueError("research round requires its validated plan")
                if (
                    reuse is not None
                    and reuse.report.documents
                    and reuse.report.policy.assess_before_discovery
                    and allow_retained_completion
                    and not request.seeds
                    and (not resuming_assessment or assessment_context == "retained_first")
                ):
                    assessment_context = "retained_first"
                    assessment = await self._assess(session, reuse, request, questions, calls=calls)
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
                            session, request, questions, assessment, reuse, calls=calls
                        )
                        reason = "answered"
                        break
                if not resuming_assessment or assessment_context == "retained_first":
                    urls = await self._discover(session, plan.queries, compiler, trace, history)
                if number == 1 and not resuming_assessment:
                    allowed_seeds: list[str] = []
                    for seed in request.seeds:
                        if compiler.accept(seed):
                            allowed_seeds.append(seed)
                        else:
                            self._refuse(session, RefusalCode.OUT_OF_SCOPE, url=seed)
                    seeds = tuple(allowed_seeds)
                    urls = tuple(dict.fromkeys(seeds + urls))
                scope = compiler.scope()
                if not resuming_assessment or assessment_context == "retained_first":
                    collection_stop = "frontier_empty"
                quantum_start = session.budget.fetches
                if scope is not None and (
                    not resuming_assessment or assessment_context == "retained_first"
                ):
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
                assessment_context = "post_collection"
                assessment = await self._assess(session, reuse, request, questions, calls=calls)
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
                answer, review = await self._answer(
                    session, request, questions, assessment, reuse, calls=calls
                )
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
