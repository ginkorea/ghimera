"""GoalSpider's priority frontier and self-grade, without its clients or hidden fallback."""

import asyncio
import hashlib
import heapq
import time
from collections.abc import Callable
from typing import Literal

from chimera.budget import RunBudget
from chimera.config import ChimeraConfig
from chimera.fetch import FetchLadder
from chimera.graph import DirectoryGraphSink, GraphSink, ResearchGraph
from chimera.ledger import Ledger
from chimera.models import (
    Document,
    Goal,
    Harvest,
    LedgerRow,
    ModelIdentity,
    Receipt,
    Scope,
    StopReason,
)
from chimera.ports import Extractor, Judge
from chimera.refusals import ChimeraRefused, ModelFailure, RefusalCode
from chimera.scoring import Scorer

CollectionStop = StopReason | Literal["round_limit"]


class CollectionSession:
    """One run's state, reused by research rounds without resetting its budget."""

    def __init__(
        self, goal: Goal, budget: RunBudget, ledger: Ledger, graph: ResearchGraph | None
    ) -> None:
        self.goal, self.budget, self.ledger, self.graph = goal, budget, ledger, graph
        self._documents: dict[str, Document] = {}
        self._frontier: list[tuple[float, str, int]] = []
        self._visited: set[str] = set()
        self._window_start, self._window_new, self._last_grade = 0, 0, 0
        self._closed = False

    @property
    def documents(self) -> tuple[Document, ...]:
        return tuple(self._documents.values())


class GoalLoop:
    def __init__(
        self,
        *,
        config: ChimeraConfig,
        fetcher: FetchLadder,
        extractor: Extractor,
        scorer: Scorer,
        judge: Judge,
        graph_sink: GraphSink | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._fetcher = fetcher
        self._extractor = extractor
        extractor.validate_config(config)
        self._scorer = scorer
        self._judge = judge
        self._graph_sink = graph_sink
        if judge.model.location == "external":
            raise ChimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
        self._clock = clock

    @property
    def config(self) -> ChimeraConfig:
        return self._config

    @property
    def judge_model(self) -> ModelIdentity:
        return self._judge.model

    async def open(self, goal: Goal, *, run_id: str | None = None) -> CollectionSession:
        budget = RunBudget(self._config, self._clock)
        ledger = Ledger()
        graph = None
        if self._config.graph is not None and self._config.graph.enabled:
            if run_id is None:
                raise ChimeraRefused(RefusalCode.GRAPH_CONTRACT)
            sink = (
                self._graph_sink
                if self._graph_sink is not None
                else DirectoryGraphSink(self._config.graph, run_id)
            )
            graph = ResearchGraph(self._config.graph, run_id, sink)
            await graph.start(goal.text)
            for seed in goal.seeds:
                await graph.discovered(seed, graph.intent_id)
        return CollectionSession(goal, budget, ledger, graph)

    async def run(self, goal: Goal, scope: Scope, *, run_id: str | None = None) -> Harvest:
        session = await self.open(goal, run_id=run_id)
        stop = await self.collect(session, scope, goal.seeds)
        if stop == "round_limit":
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        return self.finish(session, stop)

    async def collect(
        self,
        session: CollectionSession,
        scope: Scope,
        seeds: tuple[str, ...],
        *,
        fetch_limit: int | None = None,
        allow_grade: bool = True,
    ) -> CollectionStop:
        if session._closed or session.budget.config != self._config:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if fetch_limit is not None and fetch_limit <= 0:
            raise ValueError("collection quantum must be positive")
        goal, budget, ledger, graph = session.goal, session.budget, session.ledger, session.graph
        documents, frontier, visited = session._documents, session._frontier, session._visited
        for seed in seeds:
            if seed not in visited:
                heapq.heappush(frontier, (-1.0, seed, 0))
        starting_fetches = budget.fetches
        stop: CollectionStop = "frontier_empty"
        while frontier:
            if fetch_limit is not None and budget.fetches - starting_fetches >= fetch_limit:
                stop = "round_limit"
                break
            _, url, depth = heapq.heappop(frontier)
            if url in visited:
                continue
            visited.add(url)
            try:
                budget.check_time()
                if depth > scope.max_depth or not scope.permits(url):
                    raise ChimeraRefused(RefusalCode.OUT_OF_SCOPE)
                page = await self._fetcher.fetch(url, scope, budget, ledger)
                extraction_started = self._clock()
                async with asyncio.timeout(budget.remaining_seconds):
                    extracted = await self._extractor.extract(page)
                if extracted.extraction is not None or extracted.document_parse is not None:
                    ledger.append(
                        LedgerRow(
                            sequence=ledger.next_sequence,
                            event="extraction",
                            url=page.final_url,
                            extraction=extracted.extraction,
                            document_parse=extracted.document_parse,
                            reason=self._extractor.revision,
                            latency_seconds=max(0.0, self._clock() - extraction_started),
                        )
                    )
                document_node_id = None
                if graph is not None:
                    document_node_id = await graph.document(
                        page.final_url,
                        page.body,
                        extracted.text,
                        self._extractor.revision,
                        transport=page.transport,
                    )
                verdict = None
                for second_look in (False, True):
                    budget.reserve_judge()
                    try:
                        async with asyncio.timeout(budget.remaining_seconds):
                            verdict = await self._judge.document(
                                goal, extracted, second_look=second_look
                            )
                    except (ChimeraRefused, TimeoutError) as exc:
                        code = (
                            exc.code
                            if isinstance(exc, ChimeraRefused)
                            else RefusalCode.BUDGET_EXHAUSTED
                        )
                        ledger.append(
                            LedgerRow(
                                sequence=ledger.next_sequence,
                                event="verdict",
                                url=url,
                                refusal=code,
                                model=self._judge.model,
                                model_call=exc.model_call
                                if isinstance(exc, ModelFailure)
                                else None,
                                reason="served_judge_failed",
                            )
                        )
                        raise ChimeraRefused(code) from None
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
                    if verdict.decision != "hold":
                        break
                if verdict is not None and verdict.decision == "accept":
                    digest = hashlib.sha256(page.body).hexdigest()
                    if digest in documents:
                        original = documents[digest]
                        documents[digest] = original.model_copy(
                            update={
                                "duplicate_urls": original.duplicate_urls + (page.final_url,),
                            }
                        )
                        ledger.append(
                            LedgerRow(
                                sequence=ledger.next_sequence,
                                event="duplicate",
                                url=url,
                                reason="content_sha256",
                            )
                        )
                    else:
                        documents[digest] = Document(
                            url=page.final_url,
                            sha256=digest,
                            raw=page.body,
                            extracted=extracted,
                            verdict=verdict,
                            transport=page.transport,
                        )
                        session._window_new += 1
                ranked = await self._scorer.score(goal, extracted, budget)
                for link in ranked[: self._config.max_links_per_page]:
                    if link.score >= self._config.min_link_score and link.url not in visited:
                        if graph is not None and document_node_id is not None:
                            await graph.discovered(link.url, document_node_id)
                        heapq.heappush(frontier, (-link.score, link.url, depth + 1))
            except (ChimeraRefused, TimeoutError) as exc:
                code = exc.code if isinstance(exc, ChimeraRefused) else RefusalCode.BUDGET_EXHAUSTED
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
                if code == RefusalCode.BUDGET_EXHAUSTED:
                    stop = "budget_exhausted"
                    break
                if code in {RefusalCode.ADAPTER_CONTRACT, RefusalCode.MODEL_UNAVAILABLE}:
                    stop = "failed"
                    break
            if budget.fetches - session._window_start >= self._config.saturation_window:
                if session._window_new < self._config.saturation_min_new:
                    stop = "saturated"
                    break
                session._window_start, session._window_new = budget.fetches, 0
            if allow_grade and budget.fetches - session._last_grade >= self._config.grade_interval:
                try:
                    budget.reserve_judge()
                except ChimeraRefused:
                    stop = "budget_exhausted"
                    break
                try:
                    async with asyncio.timeout(budget.remaining_seconds):
                        grade = await self._judge.grade(goal, tuple(documents.values()))
                except (ChimeraRefused, TimeoutError) as exc:
                    code = (
                        exc.code
                        if isinstance(exc, ChimeraRefused)
                        else RefusalCode.BUDGET_EXHAUSTED
                    )
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
                    stop = "budget_exhausted" if code == RefusalCode.BUDGET_EXHAUSTED else "failed"
                    break
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="grade",
                        model=self._judge.model,
                        model_call=grade.model_call,
                        reason=f"{grade.satisfied}: {grade.reason}",
                    )
                )
                session._last_grade = budget.fetches
                if grade.satisfied and grade.confidence >= self._config.grade_threshold:
                    stop = "goal_satisfied"
                    break
        return stop

    def finish(self, session: CollectionSession, stop: StopReason) -> Harvest:
        if session._closed or session.budget.config != self._config:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        session._closed = True
        goal, budget, ledger, graph = session.goal, session.budget, session.ledger, session.graph
        ledger.append(LedgerRow(sequence=ledger.next_sequence, event="stop", reason=stop))
        return Harvest(
            schema="chimera.harvest/1",
            goal=goal,
            documents=session.documents,
            ledger=ledger.snapshot(),
            receipt=Receipt(
                fetches=budget.fetches,
                bytes_read=budget.bytes_read,
                judge_calls=budget.judge_calls,
                accepted_documents=len(session.documents),
                elapsed_seconds=budget.elapsed,
                stop_reason=stop,
                effective_config=self._config,
                judge=self._judge.model,
            ),
            graph=graph.snapshot() if graph is not None else None,
        )
