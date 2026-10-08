"""GoalSpider's priority frontier and self-grade, without its clients or hidden fallback."""

import asyncio
import hashlib
import heapq
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.content_dedup import ContentIndex
from ghimera.execution import StageSlots
from ghimera.extraction_attempts import (
    ExtractionCancelled,
    ExtractionFailure,
    HtmlExtractionAttempt,
)
from ghimera.fetch import FetchLadder
from ghimera.graph import DirectoryGraphSink, GraphSink, ResearchGraph
from ghimera.identity_automation import IdentityProposer, IdentityReviewer, IdentityStage
from ghimera.ledger import Ledger
from ghimera.local_input_types import LocalDocumentSeed
from ghimera.local_inputs import (
    LocalInputAcknowledgementLost,
    LocalInputFailure,
    LocalInputLoader,
    LocalInputSnapshot,
)
from ghimera.model_work import ModelInvocation, port_input, record_output, validate_model_rows
from ghimera.models import (
    Document,
    DuplicateOccurrence,
    Extracted,
    Goal,
    Harvest,
    LedgerRow,
    LinkCandidate,
    ModelIdentity,
    Page,
    Receipt,
    RetainedOriginal,
    Scope,
    StopReason,
    Verdict,
)
from ghimera.pdf_transcription import PdfTranscriptionStage
from ghimera.ports import Extractor, Judge
from ghimera.reference_types import DocumentReference, SearchReference
from ghimera.references import ReferenceBook
from ghimera.refusals import GhimeraRefused, ModelCancelled, ModelFailure, RefusalCode
from ghimera.scoring import Scorer
from ghimera.semantic_graph import SemanticExtractor, SemanticReviewer, SemanticStage
from ghimera.semantic_recovery import SemanticRecoveryStopped
from ghimera.session_state import SessionState
from ghimera.source_completion import SourceCompletionRuntime
from ghimera.source_work_types import LocalSourceRequest, SourceCoordinates, SourceRequest
from ghimera.visual_evidence import graph_visual_readings, project_visuals
from ghimera.visual_stage import VisualStage
from ghimera.visual_types import ImageEvidence

CollectionStop = StopReason | Literal["round_limit"]

if TYPE_CHECKING:
    from ghimera.research_recovery_types import ResearchRecoveryRead
    from ghimera.source_completion import SourceCompletionSnapshot
    from ghimera.source_work import SourceWorkStore, SourceWorkToken


class CollectionSession:
    """One run's state, reused by research rounds without resetting its budget."""

    def __init__(
        self,
        goal: Goal,
        budget: RunBudget,
        ledger: Ledger,
        graph: ResearchGraph | None,
        source_work: "SourceWorkStore | None" = None,
    ) -> None:
        self.goal, self.budget, self.ledger, self.graph = goal, budget, ledger, graph
        self.source_work = source_work
        self._documents: dict[str, Document] = {}
        self._retained_sources: dict[str, RetainedOriginal] = {}
        self._frontier: list[tuple[float, str, int]] = []
        self._local_frontier: list[LocalSourceRequest] = []
        self._visited: set[str] = set()
        self._reference_book = ReferenceBook(budget.config)
        self._reference_scopes: dict[str, Scope] = {}
        self._reference_hops: dict[str, int] = {}
        self._reference_origins: dict[str, str] = {}
        self._window_start, self._window_new, self._last_grade = 0, 0, 0
        self._driving = False
        self._closed = False
        self._operating = False
        self._slots = StageSlots(budget.config.execution, budget)
        self._semantic_locks: dict[str, asyncio.Lock] = {}
        self._semantic_sources: set[str] = set()
        self._content = (
            ContentIndex(budget.config.dedup) if budget.config.dedup is not None else None
        )

    @property
    def documents(self) -> tuple[Document, ...]:
        return tuple(self._documents.values())

    def close(self) -> None:
        """Release this run's persistence owners even after cancelled collection."""
        if self._operating:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self._closed = True
        try:
            if self.source_work is not None:
                self.source_work.close()
        finally:
            self.ledger.close()

    @property
    def evidence_documents(self) -> tuple[Document, ...]:
        return tuple(item for doc in self.documents for item in doc.evidence_sources())

    def reference_hops(self, url: str) -> int:
        return self._reference_hops.get(url, 0)

    @property
    def reference_hosts(self) -> tuple[str, ...]:
        return self._reference_book.extra_hosts

    def reference_origin(self, url: str) -> str | None:
        return self._reference_origins.get(url)

    def source_coordinates(self, scope: Scope, url: str, depth: int) -> SourceCoordinates:
        return SourceCoordinates(
            url=url,
            scope=self._reference_scopes.get(url, scope),
            depth=depth,
            reference_hops=self.reference_hops(url),
            reference_origin=self.reference_origin(url),
        )

    def queue_source(self, scope: Scope, url: str, depth: int, priority: float) -> None:
        """Acknowledge scheduler intent before mutating the in-memory frontier."""
        if self.source_work is not None:
            self.source_work.enqueue(
                self.source_coordinates(scope, url, depth), priority, self.ledger.next_sequence
            )
        heapq.heappush(self._frontier, (priority, url, depth))

    def discard_source(self, scope: Scope, url: str, depth: int, reason: str) -> None:
        if self.source_work is not None:
            self.source_work.discard_queued(
                self.source_coordinates(scope, url, depth), reason, self.ledger.next_sequence
            )

    def claim_cited_by(self, document: Document) -> bool:
        """Reserve a source-derived query once, before I/O, across research rounds."""
        policy = self.budget.config.references
        if (
            policy is None
            or not policy.discover_cited_by
            or self.reference_hops(document.url) >= policy.max_hops
        ):
            return False
        return self._reference_book.claim_query(document)

    def checkpoint_state(self) -> SessionState:
        if self._closed or self._operating or not self.budget.quiescent:
            raise ValueError("checkpoint requires a quiescent live collection session")
        if self.source_work is not None:
            self.source_work.assert_quiescent()
        state = SessionState(
            frontier=tuple(self._frontier),
            visited=tuple(sorted(self._visited)),
            reference_hosts=self.reference_hosts,
            reference_scopes=self._reference_scopes,
            reference_hops=self._reference_hops,
            reference_origins=self._reference_origins,
            window_start=self._window_start,
            window_new=self._window_new,
            last_grade=self._last_grade,
            semantic_sources=tuple(sorted(self._semantic_sources)),
            content_revisions=self._content.revisions if self._content is not None else (),
            local_frontier=tuple(self._local_frontier),
        )
        if self.source_work is not None:
            self.source_work.verify_frontier(state)
        return state

    @contextmanager
    def serial_driver(self) -> Iterator[None]:
        """Own one serial driver, independently from its naturally ended I/O."""
        if self._closed or self._operating or self._driving:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self._driving = True
        try:
            yield
        finally:
            self._driving = False

    @contextmanager
    def operation(self) -> Iterator[None]:
        """One driver per run; no checkpoint/finish over unacknowledged tasks."""
        if self._closed or self._operating:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self._operating = True
        try:
            yield
        finally:
            self._operating = False

    def restore_state(self, state: SessionState, harvest: Harvest) -> None:
        if state.local_frontier and self.source_work is None:
            raise ValueError("local pending work requires its owning source store")
        if self.source_work is not None:
            self.source_work.verify_frontier(state)
        self._documents = {doc.sha256: doc for doc in harvest.documents}
        self._retained_sources = {
            item.origin.document_sha256: item for item in harvest.retained_sources
        }
        self._frontier = list(state.frontier)
        self._local_frontier = list(state.local_frontier)
        heapq.heapify(self._frontier)
        self._visited = set(state.visited)
        self._reference_book.restore(harvest.ledger, state.reference_hosts)
        self._reference_scopes = dict(state.reference_scopes)
        self._reference_hops = dict(state.reference_hops)
        self._reference_origins = dict(state.reference_origins)
        self._window_start, self._window_new = state.window_start, state.window_new
        self._last_grade = state.last_grade
        observed = {
            row.semantic_window.graph_document_id
            for row in harvest.ledger
            if row.semantic_window is not None
        }
        observed.update(
            row.semantic_refusal.graph_document_id
            for row in harvest.ledger
            if row.semantic_refusal is not None and row.semantic_refusal.continued
        )
        if set(state.semantic_sources) != observed:
            raise ValueError("restored semantic work requires acknowledged observations")
        self._semantic_sources = set(state.semantic_sources)
        if self._content is not None:
            for doc in harvest.documents:
                self._content.add(doc)
            self._content.restore_revisions(state.content_revisions, harvest.source_documents)
        elif state.content_revisions:
            raise ValueError("restored content index requires its original recipe")


class GoalLoop:
    def __init__(
        self,
        *,
        config: GhimeraConfig,
        fetcher: FetchLadder,
        extractor: Extractor,
        scorer: Scorer,
        judge: Judge,
        semantic_extractor: SemanticExtractor | None = None,
        semantic_reviewer: SemanticReviewer | None = None,
        identity_proposer: IdentityProposer | None = None,
        identity_reviewer: IdentityReviewer | None = None,
        graph_sink: GraphSink | None = None,
        visual_stage: VisualStage | None = None,
        pdf_transcription: PdfTranscriptionStage | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._fetcher = fetcher
        self._extractor = extractor
        extractor.validate_config(config)
        if (config.pdf_transcription is None) != (pdf_transcription is None) or (
            pdf_transcription is not None and pdf_transcription.config != config.pdf_transcription
        ):
            raise ValueError("PDF transcription recipe and bound stage must be supplied together")
        self._pdf_transcription = pdf_transcription
        self._scorer = scorer
        scorer.validate_config(config)
        self._judge = judge
        self._graph_sink = graph_sink
        self._visuals = visual_stage
        if (config.visuals is None) != (visual_stage is None) or (
            visual_stage is not None and visual_stage.config != config.visuals
        ):
            raise ValueError("visual recipe and bound stage must be supplied together")
        if (config.semantics is not None) != (semantic_extractor is not None):
            raise ValueError("semantic policy and extractor must be supplied together")
        if semantic_extractor is None and semantic_reviewer is not None:
            raise ValueError("semantic reviewer requires its extractor")
        self._semantics = (
            SemanticStage(config, semantic_extractor, reviewer=semantic_reviewer)
            if semantic_extractor is not None
            else None
        )
        if (config.identity_automation is not None) != (
            identity_proposer is not None and identity_reviewer is not None
        ):
            raise ValueError("identity automation requires both separately bound native ports")
        if config.identity_automation is None and (
            identity_proposer is not None or identity_reviewer is not None
        ):
            raise ValueError("identity ports require an explicit automation policy")
        self._identity = (
            IdentityStage(config, identity_proposer, identity_reviewer)
            if identity_proposer is not None and identity_reviewer is not None
            else None
        )
        if judge.model.location == "external":
            raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
        self._clock = clock

    @property
    def config(self) -> GhimeraConfig:
        return self._config

    @property
    def judge_model(self) -> ModelIdentity:
        return self._judge.model

    async def open(self, goal: Goal, *, run_id: str | None = None) -> CollectionSession:
        budget = RunBudget(self._config, self._clock)
        if self._config.journal is not None:
            if run_id is None:
                raise GhimeraRefused(RefusalCode.LEDGER_SINK_FAILED)
            # Load the storage adapter only when selected. Importing it in the
            # package initializer also executes it before `python -m` inspection.
            from ghimera.journal import DirectoryLedgerSink

            ledger = Ledger(sink=DirectoryLedgerSink(self._config, run_id, goal, self._judge.model))
        else:
            ledger = Ledger()
        source_work = None
        try:
            if self._config.source_work is not None:
                from ghimera.source_work import SourceWorkStore

                if run_id is None:
                    raise ValueError("source work requires an explicit run identity")
                source_work = SourceWorkStore.open(self._config, run_id, goal, self._judge.model)
            graph = None
            if self._config.graph is not None and self._config.graph.enabled:
                if run_id is None:
                    raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
                sink = (
                    self._graph_sink
                    if self._graph_sink is not None
                    else DirectoryGraphSink(self._config.graph, run_id)
                )
                graph = ResearchGraph(
                    self._config.graph,
                    run_id,
                    sink,
                    identity_config=self._config
                    if self._config.identity_automation is not None
                    else None,
                    identity_ledger=ledger
                    if self._config.identity_automation is not None
                    else None,
                )
                await graph.start(goal.text)
                for seed in goal.seeds:
                    await graph.discovered(seed, graph.intent_id)
            return CollectionSession(goal, budget, ledger, graph, source_work)
        except BaseException:
            if source_work is not None:
                source_work.close()
            ledger.close()
            raise

    async def restore(
        self,
        run_id: str,
        harvest: Harvest,
        state: SessionState,
        *,
        search_calls: int,
        downtime_seconds: float,
        model_return: "ResearchRecoveryRead | None" = None,
    ) -> CollectionSession:
        """Resume the exact durable run; no new identity, calls or budget reset."""
        from ghimera.journal import DirectoryLedgerSink

        if (
            harvest.receipt.effective_config != self._config
            or harvest.receipt.judge != self._judge.model
        ):
            raise ValueError("continuation requires the original collection recipe and judge")
        rows, receipt = harvest.ledger, harvest.receipt
        if model_return is not None:
            from ghimera.journal_types import canonical
            from ghimera.research_recovery_store import ResearchRecoveryStore

            policy = self._config.research_recovery
            if policy is None or model_return.snapshot.progress.harvest != harvest:
                raise ValueError("model return must preserve its exact collection snapshot")
            # Revalidate the original private snapshot and its admitted tail,
            # then let the native writer lease fence changes since this read.
            verified = ResearchRecoveryStore(self._config, run_id, policy).read(
                hashlib.sha256(canonical(model_return.snapshot)).hexdigest(),
                expected_models=model_return.snapshot.models,
                decision=model_return.decision,
                attempt=model_return.attempt,
                observe_unknown=model_return.observation is not None
                and model_return.decision is None,
            )
            if verified != model_return:
                raise ValueError("model return changed before native session restoration")
            rows = verified.journal.rows
            receipt = receipt.model_copy(
                update={
                    "judge_calls": validate_model_rows(
                        self._config.model_work, self._config.judge_budget, rows
                    )
                }
            )
        sink = DirectoryLedgerSink(
            self._config, run_id, harvest.goal, self._judge.model, resume_rows=rows
        )
        ledger = Ledger(sink=sink, restored_rows=rows)
        source_work = None
        try:
            if self._config.source_work is not None:
                from ghimera.source_work import SourceWorkStore

                source_work = SourceWorkStore.resume(self._config, run_id, len(rows))
            budget = RunBudget(self._config, self._clock)
            admission = (
                (model_return.decision or model_return.attempt)
                if model_return is not None
                else None
            )
            budget.restore(receipt, rows, search_calls, downtime_seconds, admission)
            graph = None
            if self._config.graph is not None and self._config.graph.enabled:
                graph_sink = self._graph_sink or DirectoryGraphSink(self._config.graph, run_id)
                graph = ResearchGraph(
                    self._config.graph,
                    run_id,
                    graph_sink,
                    identity_config=self._config
                    if self._config.identity_automation is not None
                    else None,
                    identity_ledger=ledger
                    if self._config.identity_automation is not None
                    else None,
                )
                await graph.start(harvest.goal.text, expected=harvest.graph)
                if graph.snapshot() != harvest.graph:
                    raise ValueError("graph changed after the research checkpoint; reconcile first")
            session = CollectionSession(harvest.goal, budget, ledger, graph, source_work)
            session.restore_state(state, harvest)
            return session
        except BaseException:
            if source_work is not None:
                source_work.close()
            ledger.close()
            raise

    def source_runtime(self) -> SourceCompletionRuntime:
        return SourceCompletionRuntime(
            extractor_revision=self._extractor.revision,
            scorer_name=self._scorer.name,
            scorer_cost=self._scorer.cost,
            judge=self._judge.model,
        )

    async def run(self, goal: Goal, scope: Scope, *, run_id: str | None = None) -> Harvest:
        session = await self.open(goal, run_id=run_id)
        try:
            stop = await self.collect(session, scope, goal.seeds)
            if stop == "round_limit":
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            return self.finish(session, stop)
        finally:
            session.close()

    async def import_local(
        self, session: CollectionSession, seeds: tuple[LocalDocumentSeed, ...]
    ) -> None:
        with session.operation():
            await self._import_local(session, seeds)
            await self._resolve_identity(session)

    async def _resolve_identity(self, session: CollectionSession) -> None:
        if self._identity is not None:
            if session.graph is None or session.budget.config != self._config:
                raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
            await self._identity.run(
                session.goal.text, session.graph, session.budget, session.ledger
            )

    async def resolve_identity(self, session: CollectionSession) -> None:
        """Research boundary: all source tasks have finished before cross-document work."""
        with session.operation():
            await self._resolve_identity(session)

    async def _import_local(
        self, session: CollectionSession, seeds: tuple[LocalDocumentSeed, ...]
    ) -> None:
        """Admit local snapshots before planning, through the shared document pipeline."""
        if session._closed or session.budget.config != self._config:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        policy = self._config.local_inputs
        if seeds and policy is None:
            raise GhimeraRefused(RefusalCode.LOCAL_INPUT_FAILED)
        if policy is None:
            return
        loader = LocalInputLoader(policy)
        budget, ledger = session.budget, session.ledger
        requests = tuple(
            LocalSourceRequest(
                schema="ghimera.local-source-request/1",
                seed=LocalDocumentSeed.model_validate(item.model_dump()),
                policy_digest=policy.content_digest(),
            )
            for item in seeds
        )
        queued = session.source_work is not None and (
            self._config.source_work is not None and self._config.source_work.frontier is not None
        )
        if queued and session.source_work is not None:
            session.source_work.enqueue_local_batch(requests, ledger.next_sequence)
            for request in requests:
                if request not in session._local_frontier:
                    session._local_frontier.append(request)
            requests = tuple(session._local_frontier)
        for request in requests:
            seed = request.seed
            work, token = session.source_work, None
            try:
                if work is not None:
                    if not policy.permits(seed.path):
                        raise GhimeraRefused(RefusalCode.LOCAL_INPUT_FAILED)
                    token = work.begin_local(request, ledger.next_sequence)
                    if queued:
                        session._local_frontier.remove(request)
                snapshot, code, cancelled = await self._read_local(session, loader, seed)
                page = None
                if snapshot is not None:
                    # Existing parser byte envelope, never a fictitious HTTP fetch.
                    page = Page(
                        url=seed.source_id,
                        final_url=seed.source_id,
                        status=200,
                        content_type=seed.content_type,
                        body=snapshot.raw,
                        local_input=snapshot.evidence,
                    )
                    if work is not None and token is not None:
                        # A drained cancelled read still acquired these original bytes.
                        work.acquired(token, page)
                if cancelled:
                    raise asyncio.CancelledError
                if code is not None:
                    raise GhimeraRefused(code)
                if page is None:
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                if work is not None and token is not None:
                    work.processing(token)
                if session.graph is not None:
                    await session.graph.discovered(seed.source_id, session.graph.intent_id)
                budget.check_time()
                result = await self._process_page(session, page, seed.source_id, 0, None, 0)
                if work is not None and token is not None:
                    work.processed(token, result, ledger.next_sequence)
            except asyncio.CancelledError:
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="refusal",
                        url=seed.source_id,
                        refusal=RefusalCode.LOCAL_INPUT_FAILED,
                        reason="local_document_processing_cancelled",
                    )
                )
                if work is not None and token is not None:
                    work.refused(
                        token,
                        "local_document_processing_cancelled",
                        ledger.next_sequence,
                        cancelled=True,
                    )
                raise
            except (GhimeraRefused, TimeoutError) as exc:
                if isinstance(exc, ExtractionFailure):
                    self._record_parse_attempts(ledger, exc.attempts)
                code = exc.code if isinstance(exc, GhimeraRefused) else RefusalCode.BUDGET_EXHAUSTED
                if code in {RefusalCode.GRAPH_CONTRACT, RefusalCode.GRAPH_SINK_FAILED}:
                    # A failed graph acknowledgement is unresolved, not a known refusal.
                    raise
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="refusal",
                        url=seed.source_id,
                        refusal=code,
                        reason="local_document_processing_refused",
                    )
                )
                if work is not None and token is not None:
                    work.refused(token, code.value, ledger.next_sequence)
                raise GhimeraRefused(code) from None

    async def _read_local(
        self, session: CollectionSession, loader: LocalInputLoader, seed: LocalDocumentSeed
    ) -> tuple[LocalInputSnapshot | None, RefusalCode | None, bool]:
        """Account and drain the bounded reader before acknowledging its outcome."""
        budget, ledger = session.budget, session.ledger
        allowance = budget.reserve_local_input()
        started = self._clock()
        snapshot, failure, code = None, None, None
        cancelled = False
        task = asyncio.create_task(asyncio.to_thread(loader.read, seed, max_bytes=allowance))
        try:
            try:
                async with asyncio.timeout(budget.remaining_seconds):
                    snapshot = await asyncio.shield(task)
            except TimeoutError:
                code = RefusalCode.BUDGET_EXHAUSTED
            except asyncio.CancelledError:
                cancelled, code = True, RefusalCode.LOCAL_INPUT_FAILED
            except LocalInputFailure as exc:
                failure, code = exc, exc.code
            if code is not None and failure is None:
                # Preserve physical spend even when the caller cancels or times out.
                while True:
                    try:
                        snapshot = await asyncio.shield(task)
                        break
                    except asyncio.CancelledError:
                        if task.cancelled():
                            # Do not turn a lost reader acknowledgement into a zero-byte
                            # terminal cancellation. Its physical thread may still run.
                            raise LocalInputAcknowledgementLost(
                                "local read requires acknowledgement before reconciliation"
                            ) from None
                        cancelled = True
                    except LocalInputFailure as exc:
                        failure = exc
                        break
            read = (
                len(snapshot.raw)
                if snapshot is not None
                else (failure.bytes_read if failure is not None else 0)
            )
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="local_input",
                    url=seed.source_id,
                    bytes_read=read,
                    refusal=code,
                    local_input=snapshot.evidence
                    if snapshot is not None and code is None
                    else None,
                    reason="owned_local_snapshot" if code is None else "local_input_refused",
                    latency_seconds=max(0.0, self._clock() - started),
                )
            )
            budget.local_input_bytes += read
            budget.record_bytes(read)
            return snapshot, code, cancelled
        finally:
            budget.release_bytes(allowance)

    async def collect(
        self,
        session: CollectionSession,
        scope: Scope,
        seeds: tuple[str, ...],
        *,
        fetch_limit: int | None = None,
        allow_grade: bool = True,
        source_control: "Callable[[int, str], SourceCompletionSnapshot] | None" = None,
        starting_fetches: int | None = None,
    ) -> CollectionStop:
        if source_control is not None:
            policy = self._config.research_recovery
            if (
                policy is None
                or policy.source_completion is None
                or self._config.execution is not None
                or session.source_work is None
                or allow_grade
                or fetch_limit is None
            ):
                raise ValueError(
                    "completed source control requires explicit serial research policy"
                )
            with session.serial_driver():
                return await self._collect_completed_serial(
                    session,
                    scope,
                    seeds,
                    fetch_limit=fetch_limit,
                    source_control=source_control,
                    starting_fetches=starting_fetches,
                )
        if session._driving:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        with session.operation():
            if self._config.execution is not None:
                result = await self._collect_parallel(
                    session, scope, seeds, fetch_limit=fetch_limit, allow_grade=allow_grade
                )
            else:
                result = await self._collect_serial(
                    session, scope, seeds, fetch_limit=fetch_limit, allow_grade=allow_grade
                )
            if result not in {"failed", "budget_exhausted"}:
                await self._resolve_identity(session)
            return result

    async def _collect_completed_serial(
        self,
        session: CollectionSession,
        scope: Scope,
        seeds: tuple[str, ...],
        *,
        fetch_limit: int,
        source_control: "Callable[[int, str], SourceCompletionSnapshot]",
        starting_fetches: int | None,
    ) -> CollectionStop:
        if session._closed or session.budget.config != self._config or fetch_limit <= 0:
            raise ValueError("source completion requires its original positive collection quantum")
        budget, work = session.budget, session.source_work
        if work is None:
            raise ValueError("completed source control lost its native store")
        start = budget.fetches if starting_fetches is None else starting_fetches
        if not 0 <= start <= budget.fetches:
            raise ValueError("original collection cursor exceeds acknowledged spend")
        for seed in seeds:
            if seed not in session._visited:
                session.queue_source(scope, seed, 0, -1.0)
        while session._frontier:
            if budget.fetches - start >= fetch_limit:
                return "round_limit"
            _, url, depth = heapq.heappop(session._frontier)
            if url in session._visited:
                session.discard_source(scope, url, depth, "already_visited")
                continue
            session._visited.add(url)
            completed: list[tuple[SourceWorkToken, Document | None]] = []

            def captured(
                token: "SourceWorkToken",
                document: Document | None,
                target: "list[tuple[SourceWorkToken, Document | None]]" = completed,
            ) -> None:
                target.append((token, document))

            with session.operation():
                result = await self._collect_source(
                    session,
                    scope,
                    url,
                    depth,
                    completion=captured,
                )
            stop: CollectionStop | None = result
            if (
                stop is None
                and budget.fetches - session._window_start >= self._config.saturation_window
            ):
                if session._window_new < self._config.saturation_min_new:
                    stop = "saturated"
                else:
                    session._window_start, session._window_new = budget.fetches, 0
            if completed:
                token, document = completed[0]

                def snapshot(result: CollectionStop | None = stop) -> "SourceCompletionSnapshot":
                    return source_control(start, result or "frontier_empty")

                work.processed(
                    token,
                    document,
                    session.ledger.next_sequence,
                    control=snapshot,
                )
            if stop is not None:
                return stop
        return "frontier_empty"

    async def _collect_serial(
        self,
        session: CollectionSession,
        scope: Scope,
        seeds: tuple[str, ...],
        *,
        fetch_limit: int | None,
        allow_grade: bool,
    ) -> CollectionStop:
        if session._closed or session.budget.config != self._config:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if fetch_limit is not None and fetch_limit <= 0:
            raise ValueError("collection quantum must be positive")
        budget = session.budget
        frontier, visited = session._frontier, session._visited
        for seed in seeds:
            if seed not in visited:
                session.queue_source(scope, seed, 0, -1.0)
        starting_fetches = budget.fetches
        stop: CollectionStop = "frontier_empty"
        while frontier:
            if fetch_limit is not None and budget.fetches - starting_fetches >= fetch_limit:
                stop = "round_limit"
                break
            _, url, depth = heapq.heappop(frontier)
            if url in visited:
                session.discard_source(scope, url, depth, "already_visited")
                continue
            visited.add(url)
            result = await self._collect_source(session, scope, url, depth)
            if result is not None:
                stop = result
                break
            if budget.fetches - session._window_start >= self._config.saturation_window:
                if session._window_new < self._config.saturation_min_new:
                    stop = "saturated"
                    break
                session._window_start, session._window_new = budget.fetches, 0
            if allow_grade and budget.fetches - session._last_grade >= self._config.grade_interval:
                result = await self._grade_prefix(session, session.documents, budget.fetches)
                if result is not None:
                    stop = result
                    break
        return stop

    async def _collect_source(
        self,
        session: CollectionSession,
        scope: Scope,
        url: str,
        depth: int,
        *,
        completion: "Callable[[SourceWorkToken, Document | None], None] | None" = None,
    ) -> CollectionStop | None:
        budget, ledger = session.budget, session.ledger
        work, token = session.source_work, None
        try:
            budget.check_time()
            active_scope = session._reference_scopes.get(url, scope)
            parent_hops = session._reference_hops.get(url, 0)
            if depth > active_scope.max_depth or not active_scope.permits(url):
                raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
            if work is not None:
                token = work.begin(
                    SourceRequest(
                        url=url,
                        scope=active_scope,
                        depth=depth,
                        reference_hops=parent_hops,
                        reference_origin=session.reference_origin(url),
                    ),
                    ledger.next_sequence,
                )
            page = await self._fetcher.fetch(url, active_scope, budget, ledger)
            if work is not None and token is not None:
                work.acquired(token, page)
            if parent_hops:
                session._reference_hops[page.final_url] = parent_hops
                session._reference_origins[page.final_url] = session._reference_origins[url]
            if work is not None and token is not None:
                work.processing(token)
            result = await self._process_page(session, page, url, depth, active_scope, parent_hops)
            if work is not None and token is not None:
                if completion is None:
                    work.processed(token, result, ledger.next_sequence)
                else:
                    completion(token, result)
        except asyncio.CancelledError:
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="refusal",
                    url=url,
                    refusal=RefusalCode.EXTRACTION_FAILED,
                    reason="source_processing_cancelled",
                )
            )
            if work is not None and token is not None:
                work.refused(
                    token, "source_processing_cancelled", ledger.next_sequence, cancelled=True
                )
            elif work is not None:
                session.discard_source(scope, url, depth, "source_processing_cancelled")
            raise
        except (GhimeraRefused, TimeoutError) as exc:
            if isinstance(exc, ExtractionFailure):
                self._record_parse_attempts(ledger, exc.attempts)
            code = exc.code if isinstance(exc, GhimeraRefused) else RefusalCode.BUDGET_EXHAUSTED
            if code in {RefusalCode.GRAPH_CONTRACT, RefusalCode.GRAPH_SINK_FAILED}:
                raise
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="refusal",
                    url=url,
                    refusal=code,
                    reason=code.value,
                )
            )
            if work is not None and token is not None:
                work.refused(token, code.value, ledger.next_sequence)
            elif work is not None:
                session.discard_source(scope, url, depth, code.value)
            if code == RefusalCode.BUDGET_EXHAUSTED:
                return "budget_exhausted"
            if isinstance(exc, SemanticRecoveryStopped) or code in {
                RefusalCode.ADAPTER_CONTRACT,
                RefusalCode.MODEL_UNAVAILABLE,
            }:
                return "failed"
        return None

    async def _collect_parallel(
        self,
        session: CollectionSession,
        scope: Scope,
        seeds: tuple[str, ...],
        *,
        fetch_limit: int | None,
        allow_grade: bool,
    ) -> CollectionStop:
        policy = self._config.execution
        if policy is None or session._closed or session.budget.config != self._config:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if fetch_limit is not None and fetch_limit <= 0:
            raise ValueError("collection quantum must be positive")
        budget, frontier, visited = session.budget, session._frontier, session._visited
        for seed in seeds:
            if seed not in visited:
                session.queue_source(scope, seed, 0, -1.0)
        start = budget.fetches
        sources: dict[asyncio.Task[CollectionStop | None], int] = {}
        source_requests: dict[asyncio.Task[CollectionStop | None], tuple[str, int]] = {}
        grade: asyncio.Task[CollectionStop | None] | None = None
        graded_documents: int | None = None
        grading_stopped = False
        admitted = 0
        halt: CollectionStop | None = None
        try:
            while True:
                # Never infer saturation from pages still being processed.
                window_due = budget.fetches - session._window_start >= (
                    self._config.saturation_window
                )
                draining_window = window_due and session._window_new < (
                    self._config.saturation_min_new
                )
                if window_due and not draining_window:
                    session._window_start, session._window_new = budget.fetches, 0
                if draining_window and not sources:
                    return "saturated"
                if halt is None:
                    if budget.remaining_seconds <= 0 or budget.remaining_bytes <= 0:
                        halt = "budget_exhausted"
                    elif budget.fetches >= self._config.page_budget:
                        halt = "budget_exhausted"
                    elif fetch_limit is not None and budget.fetches - start >= fetch_limit:
                        halt = "round_limit"
                while frontier and len(sources) < policy.active_sources and halt is None:
                    if draining_window or (
                        fetch_limit is not None
                        and budget.fetches - start + len(sources) >= fetch_limit
                    ):
                        break
                    _, url, depth = heapq.heappop(frontier)
                    if url in visited:
                        session.discard_source(scope, url, depth, "already_visited")
                        continue
                    # Reserve identity before the task's first await. Discovered
                    # links cannot dispatch a second copy of an in-flight URL.
                    visited.add(url)
                    task = asyncio.create_task(self._collect_source(session, scope, url, depth))
                    sources[task] = admitted
                    source_requests[task] = (url, depth)
                    admitted += 1
                if (
                    grade is None
                    and allow_grade
                    and not grading_stopped
                    and budget.remaining_seconds > 0
                    and (
                        budget.fetches - session._last_grade >= self._config.grade_interval
                        or (
                            not frontier
                            and not sources
                            and graded_documents is not None
                            and len(session.documents) > graded_documents
                        )
                    )
                ):
                    # The immutable accepted-document prefix is graded while
                    # independent source tasks continue, not after a full drain.
                    grade = asyncio.create_task(
                        self._grade_prefix(session, session.documents, budget.fetches)
                    )
                    graded_documents = len(session.documents)
                tasks = set(sources)
                if grade is not None:
                    tasks.add(grade)
                if not tasks:
                    return halt or "frontier_empty"
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                decision: CollectionStop | None = None
                for task in sorted(done, key=lambda item: sources.get(item, -1)):
                    result = task.result()
                    if task is grade:
                        grade = None
                        if result == "budget_exhausted":
                            grading_stopped = True
                    else:
                        del sources[task]
                        del source_requests[task]
                    if result == "failed":
                        decision = result
                    elif result == "goal_satisfied" and decision is None:
                        decision = result
                    if result == "budget_exhausted":
                        halt = result
                if decision is not None:
                    return decision
        finally:
            # Cancellation/early satisfaction never leaves fetch/model/parser
            # work detached from this run. Owners record actual spent work and
            # finish shielded graph acknowledgements before we return.
            pending = set(sources)
            if grade is not None:
                pending.add(grade)
            for task in pending:
                if not task.done():
                    task.cancel()
            if pending:
                tasks_to_drain = tuple(pending)
                outcomes = await asyncio.gather(*tasks_to_drain, return_exceptions=True)
                for task, outcome in zip(tasks_to_drain, outcomes, strict=True):
                    if isinstance(outcome, asyncio.CancelledError) and task in source_requests:
                        url, depth = source_requests[task]
                        # A task cancelled before its first instruction never
                        # entered _collect_source's cancellation handler.
                        session.discard_source(scope, url, depth, "scheduler_cancelled")
                for outcome in outcomes:
                    if isinstance(outcome, Exception):
                        # A concurrent storage/contract failure cannot disappear
                        # behind another task's goal-satisfied observation.
                        raise outcome

    async def _grade_prefix(
        self, session: CollectionSession, documents: tuple[Document, ...], fetches: int
    ) -> CollectionStop | None:
        budget, ledger = session.budget, session.ledger
        reserved = False

        try:
            async with session._slots.slot("judge"):
                invocation = ModelInvocation(
                    budget,
                    ledger,
                    phase="grade",
                    model=self._judge.model,
                    request=port_input(budget, session.goal, *documents),
                )
                reserved = True
                async with asyncio.timeout(budget.remaining_seconds):
                    grade = await invocation.invoke(
                        lambda: self._judge.grade(session.goal, documents),
                        record_output,
                    )
        except asyncio.CancelledError as exc:
            if reserved:
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="grade",
                        refusal=RefusalCode.MODEL_UNAVAILABLE,
                        model=self._judge.model,
                        model_call=exc.model_call if isinstance(exc, ModelCancelled) else None,
                        reason="served_grade_cancelled",
                    )
                )
            raise asyncio.CancelledError from None
        except (GhimeraRefused, TimeoutError) as exc:
            code = exc.code if isinstance(exc, GhimeraRefused) else RefusalCode.BUDGET_EXHAUSTED
            if reserved:
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="grade",
                        refusal=code,
                        model=self._judge.model,
                        model_call=exc.model_call if isinstance(exc, ModelFailure) else None,
                        reason="served_grade_failed",
                    )
                )
            return "budget_exhausted" if code == RefusalCode.BUDGET_EXHAUSTED else "failed"
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event="grade",
                model=self._judge.model,
                model_call=grade.model_call,
                reason=f"{grade.satisfied}: {grade.reason}",
            )
        )
        session._last_grade = fetches
        if grade.satisfied and grade.confidence >= self._config.grade_threshold:
            return "goal_satisfied"
        return None

    async def _process_page(
        self,
        session: CollectionSession,
        page: Page,
        url: str,
        depth: int,
        active_scope: Scope | None,
        parent_hops: int,
    ) -> Document | None:
        """One extraction/scoring/verdict/identity owner for web and local snapshots."""
        goal, budget, ledger, graph = session.goal, session.budget, session.ledger, session.graph
        documents, visited = session._documents, session._visited
        candidate = None
        extraction_started = self._clock()
        async with session._slots.slot("extraction"), asyncio.timeout(budget.remaining_seconds):
            try:
                extracted = (
                    await self._pdf_transcription.extract(
                        page, native=self._extractor, budget=budget, ledger=ledger
                    )
                    if self._pdf_transcription is not None
                    else await self._extractor.extract(page)
                )
            except ExtractionCancelled as exc:
                self._record_parse_attempts(ledger, exc.attempts)
                # Preserve asyncio.timeout's exact CancelledError contract.
                raise asyncio.CancelledError from None
        if extracted.extraction is not None:
            self._record_parse_attempts(ledger, extracted.extraction.attempts)
        if (
            extracted.extraction is not None
            or extracted.document_parse is not None
            or extracted.source_feed is not None
        ):
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="extraction",
                    url=page.final_url,
                    extraction=extracted.extraction,
                    document_parse=extracted.document_parse,
                    source_feed=extracted.source_feed,
                    reason=extracted.source_feed.parser_revision
                    if extracted.source_feed is not None
                    else self._extractor.revision,
                    latency_seconds=max(0.0, self._clock() - extraction_started),
                )
            )
            if extracted.extraction is not None:
                health = extracted.extraction.locator_health
                if health is not None and health.generic_only:
                    ledger.append(
                        LedgerRow(
                            sequence=ledger.next_sequence,
                            event="policy",
                            url=page.final_url,
                            extraction=extracted.extraction,
                            reason="locator_drift: publisher uses generic extraction",
                        )
                    )
        document_node_id = None
        if graph is not None and self._visuals is None:
            document_node_id = await graph.document(
                page.final_url,
                page.body,
                extracted.text,
                self._extraction_revision(extracted),
                transport=page.transport,
                local_input=page.local_input,
                human_browser=page.human_browser,
                source_refresh=page.source_refresh,
                pdf_reading=extracted.pdf_transcription.graph_reading()
                if extracted.pdf_transcription is not None
                else None,
            )
        # Score native evidence before the judge. Similarity guides the frontier,
        # but never replaces a document verdict or factual source evidence.
        async with session._slots.slot("scoring"):
            ranked = await self._scorer.score(goal, extracted, budget, ledger)
        verdict = None
        for second_look in (False, True):
            verdict = await self._document_verdict(session, extracted, url, second_look)
            if verdict.decision != "hold":
                break
        if verdict is not None and verdict.decision == "accept":
            digest = hashlib.sha256(page.body).hexdigest()
            images: tuple[ImageEvidence, ...] = ()
            if (
                self._visuals is not None
                and active_scope is not None
                and not page.body.startswith(b"%PDF-")
            ):
                async with session._slots.slot("visual"):
                    images = await self._visuals.collect(
                        goal=goal,
                        parent=page,
                        scope=active_scope,
                        fetcher=self._fetcher,
                        budget=budget,
                        ledger=ledger,
                        language_hint=extracted.language,
                    )
            candidate = Document(
                url=page.final_url,
                sha256=digest,
                raw=page.body,
                extracted=extracted,
                verdict=verdict,
                transport=page.transport,
                rendered=page.rendered,
                source_session=page.source_session,
                source_refresh=page.source_refresh,
                challenge_use=page.challenge_use,
                local_input=page.local_input,
                human_browser=page.human_browser,
                images=images,
            )
            if self._visuals is not None and page.body.startswith(b"%PDF-"):
                async with session._slots.slot("visual"):
                    images = await self._visuals.collect_pdf(
                        goal=goal,
                        document=candidate,
                        budget=budget,
                        ledger=ledger,
                        language_hint=extracted.language,
                    )
                candidate = candidate.model_copy(update={"images": images})
            if graph is not None and document_node_id is None:
                document_node_id = await graph.document(
                    page.final_url,
                    page.body,
                    extracted.text,
                    self._extraction_revision(extracted),
                    transport=page.transport,
                    local_input=page.local_input,
                    human_browser=page.human_browser,
                    source_refresh=page.source_refresh,
                    pdf_reading=extracted.pdf_transcription.graph_reading()
                    if extracted.pdf_transcription is not None
                    else None,
                    visual_readings=graph_visual_readings(images),
                )
                if graph.visual_projection is not None:
                    await project_visuals(graph, document_node_id, graph.visual_projection)
            content = session._content
            matched = content.match(candidate) if content is not None else None
            drift = content.drift(candidate) if content is not None else None
            if drift is not None:
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="content_drift",
                        url=page.final_url,
                        reason=drift.reason,
                        content_drift=drift,
                    )
                )
            representative = matched.representative_sha256 if matched is not None else digest
            if representative in documents:
                original = documents[representative]
                occurrences = original.occurrences
                if (
                    matched is not None
                    and (candidate.url, digest) != (original.url, original.sha256)
                    and not any(
                        (item.url, item.sha256) == (candidate.url, digest) for item in occurrences
                    )
                ):
                    occurrence = DuplicateOccurrence.model_validate(
                        dict(
                            candidate.model_dump(exclude={"duplicate_urls", "occurrences"}),
                            dedup=matched.model_dump(by_alias=True),
                        )
                    )
                    occurrences += (occurrence,)
                urls = tuple(
                    dict.fromkeys(
                        original.duplicate_urls
                        + ((page.final_url,) if page.final_url != original.url else ())
                    )
                )
                documents[representative] = Document.model_validate(
                    dict(original.model_dump(), duplicate_urls=urls, occurrences=occurrences)
                )
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="duplicate",
                        url=url,
                        reason=matched.reason if matched is not None else "content_sha256",
                        dedup=matched,
                    )
                )
            else:
                if content is not None:
                    content.add(candidate)
                documents[digest] = candidate
                session._window_new += 1
            if content is not None:
                content.observe(candidate)
            if self._semantics is not None:
                if graph is None or document_node_id is None:
                    raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
                lock = session._semantic_locks.setdefault(document_node_id, asyncio.Lock())
                async with asyncio.timeout(budget.remaining_seconds), lock:
                    if document_node_id not in session._semantic_sources:
                        async with session._slots.slot("semantic"):
                            await self._semantics.extract(
                                goal.text, candidate, document_node_id, graph, budget, ledger
                            )
                        session._semantic_sources.add(document_node_id)
            reference_policy = self._config.references
            if (
                reference_policy is not None
                and active_scope is not None
                and candidate in session.evidence_documents
            ):
                by_target = {item.target_url: item for item in extracted.references}
                reference_links = tuple(link for link in ranked if link.url in by_target)
                for link in reference_links[: reference_policy.max_candidates_per_parent]:
                    await self.queue_reference(
                        session,
                        candidate,
                        by_target[link.url],
                        link,
                        active_scope,
                        parent_hops,
                        origin_url=session.reference_origin(url),
                    )
        if graph is not None and document_node_id is None:
            document_node_id = await graph.document(
                page.final_url,
                page.body,
                extracted.text,
                self._extraction_revision(extracted),
                transport=page.transport,
                local_input=page.local_input,
                human_browser=page.human_browser,
                source_refresh=page.source_refresh,
                pdf_reading=extracted.pdf_transcription.graph_reading()
                if extracted.pdf_transcription is not None
                else None,
            )
        reference_targets = {item.target_url for item in extracted.references}
        for link in ranked[: self._config.max_links_per_page]:
            if link.url in reference_targets:
                continue
            if (
                active_scope is not None
                and link.score >= self._config.min_link_score
                and link.url not in visited
            ):
                if graph is not None and document_node_id is not None:
                    await graph.discovered(link.url, document_node_id)
                if parent_hops:
                    session._reference_scopes.setdefault(link.url, active_scope)
                    session._reference_hops.setdefault(link.url, parent_hops)
                    session._reference_origins.setdefault(
                        link.url, session._reference_origins.get(url, url)
                    )
                session.queue_source(active_scope, link.url, depth + 1, -link.score)
        return candidate

    async def _document_verdict(
        self, session: CollectionSession, extracted: Extracted, url: str, second_look: bool
    ) -> Verdict:
        budget, ledger = session.budget, session.ledger
        async with session._slots.slot("judge"):
            invocation = ModelInvocation(
                budget,
                ledger,
                phase="verdict",
                model=self._judge.model,
                url=url,
                request=port_input(budget, session.goal, extracted, second_look=second_look),
            )
            try:
                async with asyncio.timeout(budget.remaining_seconds):
                    verdict = await invocation.invoke(
                        lambda: self._judge.document(
                            session.goal, extracted, second_look=second_look
                        ),
                        record_output,
                    )
            except asyncio.CancelledError as exc:
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="verdict",
                        url=url,
                        refusal=RefusalCode.MODEL_UNAVAILABLE,
                        model=self._judge.model,
                        model_call=exc.model_call if isinstance(exc, ModelCancelled) else None,
                        reason="served_judge_cancelled",
                    )
                )
                raise asyncio.CancelledError from None
            except (GhimeraRefused, TimeoutError) as exc:
                code = exc.code if isinstance(exc, GhimeraRefused) else RefusalCode.BUDGET_EXHAUSTED
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="verdict",
                        url=url,
                        refusal=code,
                        model=self._judge.model,
                        model_call=exc.model_call if isinstance(exc, ModelFailure) else None,
                        reason="served_judge_failed",
                    )
                )
                raise GhimeraRefused(code) from None
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="verdict",
                    model=self._judge.model,
                    model_call=verdict.model_call,
                    url=url,
                    reason=f"{verdict.decision}: {verdict.reason}",
                )
            )
        return verdict

    def _record_parse_attempts(
        self, ledger: Ledger, attempts: tuple[HtmlExtractionAttempt, ...]
    ) -> None:
        for attempt in attempts:
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="extraction_attempt",
                    url=attempt.source_url,
                    refusal=attempt.refusal,
                    latency_seconds=attempt.latency_seconds,
                    extraction_attempt=attempt,
                    reason=f"{attempt.phase}: {attempt.outcome}",
                )
            )

    async def score_discovery(
        self,
        session: CollectionSession,
        source: Document,
        links: tuple[LinkCandidate, ...],
    ) -> tuple[LinkCandidate, ...]:
        """Use native source context with provider-observed candidates, not invented URLs."""
        if (
            session._closed
            or session.budget.config != self._config
            or (source not in session.evidence_documents)
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        document = source.extracted.model_validate(
            dict(
                source.extracted.model_dump(),
                links=links,
                references=(),
            )
        )
        return await self._scorer.score(session.goal, document, session.budget, session.ledger)

    async def queue_reference(
        self,
        session: CollectionSession,
        source: Document,
        proof: DocumentReference | SearchReference,
        link: LinkCandidate,
        scope: Scope,
        parent_hops: int,
        *,
        origin_url: str | None = None,
    ) -> bool:
        if session._closed or session.budget.config != self._config:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if source not in session.evidence_documents or (link.url, link.anchor) != (
            proof.target_url,
            proof.anchor,
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        selected = session._reference_book.consider(
            source,
            proof,
            link,
            scope,
            parent_hops,
            session._visited,
            session.ledger,
            origin_url,
        )
        if selected is None:
            return False
        session._reference_scopes[link.url] = selected
        session._reference_hops[link.url] = parent_hops + 1
        session._reference_origins[link.url] = link.url
        session.queue_source(selected, link.url, 0, -link.score)
        if session.graph is not None:
            graph = session.graph
            parent = await graph.document(
                source.url,
                source.raw,
                source.extracted.text,
                self._extraction_revision(source.extracted),
                transport=source.transport,
                local_input=source.local_input,
                human_browser=source.human_browser,
                source_refresh=source.source_refresh,
                pdf_reading=source.extracted.pdf_transcription.graph_reading()
                if source.extracted.pdf_transcription is not None
                else None,
                visual_readings=graph_visual_readings(source.images),
            )
            await graph.discovered(link.url, parent)
        return True

    async def admit_retained(self, session: CollectionSession, original: RetainedOriginal) -> None:
        """Reuse the current graph/stage/budget, never replay old source or model work."""
        from ghimera.retained_graph import validate_original

        original = RetainedOriginal.model_validate(original.model_dump())
        validate_original(self._config, original)
        graph = session.graph
        if (
            session._closed
            or session._operating
            or session.budget.config != self._config
            or graph is None
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        key = original.origin.document_sha256
        if key in session._retained_sources:
            if session._retained_sources[key] != original:
                raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
            return
        policy = self._config.research.retained_evidence if self._config.research else None
        if policy is None or (
            len(session._retained_sources) >= policy.max_source_documents
            or sum(
                len(item.document.model_dump_json().encode())
                for item in (*session._retained_sources.values(), original)
            )
            > policy.max_snapshot_bytes
        ):
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        with session.operation():
            work, token = session.source_work, None
            if work is not None:
                token = work.capture_retained(original, session.ledger.next_sequence)
                work.processing(token)
            try:
                await self._project_retained(session, original)
            except (GhimeraRefused, TimeoutError, asyncio.CancelledError) as exc:
                code = (
                    exc.code
                    if isinstance(exc, GhimeraRefused)
                    else RefusalCode.BUDGET_EXHAUSTED
                    if isinstance(exc, TimeoutError)
                    else RefusalCode.SEMANTIC_EXTRACTION_FAILED
                )
                if (
                    work is not None
                    and token is not None
                    and key in session._retained_sources
                    and code not in {RefusalCode.GRAPH_CONTRACT, RefusalCode.GRAPH_SINK_FAILED}
                ):
                    work.refused(
                        token,
                        code.value,
                        session.ledger.next_sequence,
                        cancelled=isinstance(exc, asyncio.CancelledError),
                    )
                # A lost graph admission/projection acknowledgement remains unresolved.
                raise
            if work is not None and token is not None:
                work.completed_retained(token, session.ledger.next_sequence)

    async def _project_retained(
        self, session: CollectionSession, original: RetainedOriginal
    ) -> None:
        """Current graph/model work only; the historical capsule keeps its own calls."""
        graph = session.graph
        if graph is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        document = original.document
        reading = document.extracted.pdf_transcription
        document_id = await graph.document(
            document.url,
            document.raw,
            document.extracted.text,
            original.revision,
            transport=document.transport,
            local_input=document.local_input,
            human_browser=document.human_browser,
            source_refresh=document.source_refresh,
            pdf_reading=reading.graph_reading() if reading else None,
            retained_source=original.origin,
            visual_readings=graph_visual_readings(document.images),
        )
        if graph.visual_projection is not None:
            await project_visuals(graph, document_id, graph.visual_projection)
        session.ledger.append(
            LedgerRow(
                sequence=session.ledger.next_sequence,
                event="retained_source",
                url=document.url,
                reason="retained_original_admitted",
                retained_source=original.origin,
            )
        )
        session._retained_sources[original.origin.document_sha256] = original
        if self._semantics is not None:
            lock = session._semantic_locks.setdefault(document_id, asyncio.Lock())
            try:
                async with asyncio.timeout(session.budget.remaining_seconds), lock:
                    if document_id not in session._semantic_sources:
                        async with session._slots.slot("semantic"):
                            await self._semantics.extract(
                                session.goal.text,
                                document,
                                document_id,
                                graph,
                                session.budget,
                                session.ledger,
                                record_terminal_refusal=True,
                            )
                        session._semantic_sources.add(document_id)
            except (GhimeraRefused, TimeoutError, asyncio.CancelledError) as exc:
                code = (
                    exc.code
                    if isinstance(exc, GhimeraRefused)
                    else RefusalCode.BUDGET_EXHAUSTED
                    if isinstance(exc, TimeoutError)
                    else RefusalCode.SEMANTIC_EXTRACTION_FAILED
                )
                session.ledger.append(
                    LedgerRow(
                        sequence=session.ledger.next_sequence,
                        event="refusal",
                        url=document.url,
                        refusal=code,
                        reason="retained_semantics_refused",
                        retained_failure=original.origin,
                    )
                )
                if isinstance(exc, TimeoutError):
                    raise GhimeraRefused(code) from None
                raise

    def finish(self, session: CollectionSession, stop: StopReason) -> Harvest:
        if session._closed or session._operating or session.budget.config != self._config:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if session.source_work is not None:
            session.source_work.assert_quiescent()
        session._closed = True
        ledger = session.ledger
        ledger.append(LedgerRow(sequence=ledger.next_sequence, event="stop", reason=stop))
        harvest = self.snapshot(session, stop)
        ledger.finish(harvest)
        if session.source_work is not None:
            session.source_work.close()
        return harvest

    def _extraction_revision(self, extracted: Extracted) -> str:
        if extracted.source_feed is not None:
            return extracted.source_feed.parser_revision
        if extracted.pdf_transcription is not None:
            return f"{self._extractor.revision}+{PdfTranscriptionStage.revision}"
        return self._extractor.revision

    def snapshot(self, session: CollectionSession, stop: StopReason = "frontier_empty") -> Harvest:
        """Validated partial evidence; does not append stop or seal the journal."""
        if session._operating or not session.budget.quiescent:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        goal, budget, ledger, graph = session.goal, session.budget, session.ledger, session.graph
        return Harvest(
            schema="chimera.harvest/2" if session._retained_sources else "chimera.harvest/1",
            goal=goal,
            documents=session.documents,
            retained_sources=tuple(session._retained_sources.values()),
            ledger=ledger.snapshot(),
            receipt=Receipt(
                fetches=budget.fetches,
                bytes_read=budget.bytes_read,
                judge_calls=budget.judge_calls,
                encoding_calls=budget.encoding_calls,
                encoding_chars=budget.encoding_chars,
                accepted_documents=len(session.documents),
                elapsed_seconds=budget.elapsed,
                stop_reason=stop,
                effective_config=self._config,
                judge=self._judge.model,
            ),
            graph=graph.snapshot() if graph is not None else None,
        )
