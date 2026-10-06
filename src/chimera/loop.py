"""GoalSpider's priority frontier and self-grade, without its clients or hidden fallback."""

import asyncio
import hashlib
import heapq
import time
from collections.abc import Callable

from chimera.budget import RunBudget
from chimera.config import ChimeraConfig
from chimera.fetch import FetchLadder
from chimera.graph import DirectoryGraphSink, GraphSink, ResearchGraph
from chimera.ledger import Ledger
from chimera.models import Document, Goal, Harvest, LedgerRow, Receipt, Scope, StopReason
from chimera.ports import Extractor, Judge
from chimera.refusals import ChimeraRefused, RefusalCode
from chimera.scoring import Scorer


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
        self._scorer = scorer
        self._judge = judge
        self._graph_sink = graph_sink
        if judge.model.location == "external":
            raise ChimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
        self._clock = clock

    async def run(self, goal: Goal, scope: Scope, *, run_id: str | None = None) -> Harvest:
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
        documents: dict[str, Document] = {}
        frontier = [(-1.0, seed, 0) for seed in goal.seeds]
        heapq.heapify(frontier)
        visited: set[str] = set()
        window_start, window_new, last_grade = 0, 0, 0
        stop: StopReason = "frontier_empty"
        while frontier:
            _, url, depth = heapq.heappop(frontier)
            if url in visited:
                continue
            visited.add(url)
            try:
                budget.check_time()
                if depth > scope.max_depth or not scope.permits(url):
                    raise ChimeraRefused(RefusalCode.OUT_OF_SCOPE)
                page = await self._fetcher.fetch(url, scope, budget, ledger)
                async with asyncio.timeout(budget.remaining_seconds):
                    extracted = await self._extractor.extract(page)
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
                    except (ChimeraRefused, TimeoutError):
                        ledger.append(
                            LedgerRow(
                                sequence=ledger.next_sequence,
                                event="verdict",
                                url=url,
                                refusal=RefusalCode.MODEL_UNAVAILABLE,
                                reason="served_judge_failed",
                            )
                        )
                        raise ChimeraRefused(RefusalCode.MODEL_UNAVAILABLE) from None
                    ledger.append(
                        LedgerRow(
                            sequence=ledger.next_sequence,
                            event="verdict",
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
                        window_new += 1
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
            if budget.fetches - window_start >= self._config.saturation_window:
                if window_new < self._config.saturation_min_new:
                    stop = "saturated"
                    break
                window_start, window_new = budget.fetches, 0
            if budget.fetches - last_grade >= self._config.grade_interval:
                try:
                    budget.reserve_judge()
                except ChimeraRefused:
                    stop = "budget_exhausted"
                    break
                try:
                    async with asyncio.timeout(budget.remaining_seconds):
                        grade = await self._judge.grade(goal, tuple(documents.values()))
                except (ChimeraRefused, TimeoutError):
                    ledger.append(
                        LedgerRow(
                            sequence=ledger.next_sequence,
                            event="grade",
                            refusal=RefusalCode.MODEL_UNAVAILABLE,
                            reason="served_grade_failed",
                        )
                    )
                    stop = "failed"
                    break
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="grade",
                        reason=f"{grade.satisfied}: {grade.reason}",
                    )
                )
                last_grade = budget.fetches
                if grade.satisfied and grade.confidence >= self._config.grade_threshold:
                    stop = "goal_satisfied"
                    break
        ledger.append(LedgerRow(sequence=ledger.next_sequence, event="stop", reason=stop))
        return Harvest(
            schema="chimera.harvest/1",
            goal=goal,
            documents=tuple(documents.values()),
            ledger=ledger.snapshot(),
            receipt=Receipt(
                fetches=budget.fetches,
                bytes_read=budget.bytes_read,
                judge_calls=budget.judge_calls,
                accepted_documents=len(documents),
                elapsed_seconds=budget.elapsed,
                stop_reason=stop,
                effective_config=self._config,
                judge=self._judge.model,
            ),
            graph=graph.snapshot() if graph is not None else None,
        )
