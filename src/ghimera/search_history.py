"""Run-owned routing, quotas and retained responses; no uncharged composite search."""

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from ghimera.budget import RunBudget
from ghimera.discovery import BoundSearch, DiscoveryProviders
from ghimera.discovery_config import SearchCallLimits
from ghimera.ledger import Ledger
from ghimera.models import LedgerRow
from ghimera.query_work import QueryWork
from ghimera.query_work_types import QueryCursor
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research_reranking_types import RerankDecision
from ghimera.research_types import ResearchRound, SearchObservation, SearchQuery, SearchResponse
from ghimera.search import GroundedSearch


@dataclass
class _Usage:
    calls: int = 0
    bytes_read: int = 0
    seconds: float = 0.0
    reserved_bytes: int = 0
    reserved_seconds: float = 0.0
    failures: int = 0


class SearchHistory:
    def __init__(
        self,
        provider: GroundedSearch | DiscoveryProviders,
        budget: RunBudget,
        ledger: Ledger,
        *,
        restored: tuple[SearchObservation, ...] = (),
    ) -> None:
        self._provider, self._budget, self._ledger = provider, budget, ledger
        if isinstance(provider, DiscoveryProviders) and provider.policy != budget.config.discovery:
            raise ValueError("discovery history requires the effective run recipe")
        self._observations: list[SearchObservation] = list(restored)
        self._rounds: tuple[ResearchRound, ...] = ()
        self._usage: dict[tuple[str, str], _Usage] = {}
        self._semaphore = asyncio.Semaphore(
            provider.policy.provider_concurrency if isinstance(provider, DiscoveryProviders) else 1
        )
        for row in ledger.snapshot():
            if row.event != "fetch" or row.route is None or not row.route.startswith("search:"):
                continue
            if isinstance(provider, DiscoveryProviders):
                for bound in provider.providers:
                    if row.route == f"search:{bound.identity[0]}@{bound.identity[1]}":
                        self._charge(self._usage.setdefault(bound.identity, _Usage()), row)
        acknowledged = {
            row.query_ack.reservation_sequence
            for row in ledger.snapshot()
            if row.query_ack is not None
        }
        if isinstance(provider, DiscoveryProviders):
            for row in ledger.snapshot():
                reservation = row.query_reservation
                if (
                    reservation is not None
                    and reservation.channel == "discovery"
                    and row.sequence not in acknowledged
                ):
                    identity = (reservation.provider, reservation.provider_revision)
                    self._usage.setdefault(identity, _Usage()).calls += 1

    @staticmethod
    def _charge(usage: _Usage, row: LedgerRow) -> None:
        usage.bytes_read += row.bytes_read
        usage.seconds += row.latency_seconds
        usage.calls += 1
        usage.failures = usage.failures + 1 if row.refusal is not None else 0

    def set_rounds(self, rounds: tuple[ResearchRound, ...]) -> None:
        self._rounds = rounds

    def _ordered(self) -> tuple[BoundSearch, ...]:
        provider = self._provider
        if not isinstance(provider, DiscoveryProviders):
            return ()
        changes, stagnant = 0, 0
        for round_ in self._rounds:
            progress = round_.discovery_progress
            if progress is None:
                raise ValueError("discovery continuation requires retained round progress")
            if (
                progress.new_documents >= provider.policy.min_new_documents
                or progress.new_answers >= provider.policy.min_new_answers
            ):
                stagnant = 0
            else:
                stagnant += 1
                if stagnant >= provider.policy.stagnation_window:
                    changes = min(changes + 1, provider.policy.max_strategy_changes)
                    stagnant = 0
        offset = changes % len(provider.providers)
        return provider.providers[offset:] + provider.providers[:offset]

    @property
    def observations(self) -> tuple[SearchObservation, ...]:
        return tuple(sorted(self._observations, key=lambda observation: observation.sequence))

    async def discover(self, query: SearchQuery) -> SearchResponse:
        if isinstance(self._provider, DiscoveryProviders):
            raise ValueError("multi-provider discovery returns individual observations")
        return (await self._call(self._provider, query)).response

    async def _call(
        self,
        provider: GroundedSearch,
        query: SearchQuery,
        *,
        limits: SearchCallLimits | None = None,
        before_call: Callable[[GroundedSearch], None] | None = None,
        query_work: QueryWork | None = None,
    ) -> SearchObservation:
        if before_call is not None and (query_work is None or query_work.original is None):
            before_call(provider)
        decision = (
            RerankDecision(
                schema="ghimera.rerank-decision/1",
                action="fresh",
                operation_key=hashlib.sha256(
                    (
                        f"discovery:{len(self._rounds) + 1}:{provider.identity}:"
                        f"{query.content_digest()}"
                    ).encode()
                ).hexdigest(),
            )
            if self._budget.config.research is not None
            and self._budget.config.research.reranking is not None
            else None
        )
        response = await provider.discover(
            query,
            self._budget,
            self._ledger,
            limits=limits,
            rerank_decision=decision,
            query_work=query_work,
        )
        # The final provider template appends before return, with no intervening
        # await. Capture immediately here, before graph/model work or gather can
        # fail. Concurrent completions therefore bind their own append position.
        observation = SearchObservation(
            schema="chimera.search-observation/1",
            sequence=query_work.ack_sequence
            if query_work is not None and query_work.ack_sequence is not None
            else self._ledger.next_sequence - 1,
            provider=provider.identity[0],
            provider_revision=provider.identity[1],
            query=query,
            response=response,
        )
        if observation not in self._observations:
            self._observations.append(observation)
        return observation

    async def _attempt(
        self,
        provider: BoundSearch,
        query: SearchQuery,
        before_call: Callable[[GroundedSearch], None] | None,
        query_work: QueryWork | None = None,
    ) -> SearchObservation | GhimeraRefused:
        async with self._semaphore:
            usage = self._usage.setdefault(provider.identity, _Usage())
            policy = provider.policy
            available_bytes = policy.byte_budget - usage.bytes_read - usage.reserved_bytes
            available_seconds = policy.call_seconds_budget - usage.seconds - usage.reserved_seconds
            held = (
                "circuit_open"
                if usage.failures >= policy.consecutive_failure_limit
                else "call_budget"
                if usage.calls >= policy.max_calls
                else "byte_budget"
                if available_bytes <= 0
                else "call_seconds_budget"
                if available_seconds <= 0
                else None
            )
            if held is not None:
                self._ledger.append(
                    LedgerRow(
                        sequence=self._ledger.next_sequence,
                        event="policy",
                        query=query.text,
                        reason="discovery_held:" + policy.id + ":" + held,
                    )
                )
                return GhimeraRefused(RefusalCode.SEARCH_UNAVAILABLE)
            limits = SearchCallLimits(
                max_bytes=min(policy.max_response_bytes, available_bytes),
                limit=policy.max_results,
                timeout_seconds=min(policy.timeout_seconds, available_seconds),
            )
            usage.calls += 1  # Atomic reservation before the provider's first await.
            usage.reserved_bytes += limits.max_bytes
            usage.reserved_seconds += limits.timeout_seconds
            start = self._ledger.next_sequence
            try:
                return await self._call(
                    provider, query, limits=limits, before_call=before_call, query_work=query_work
                )
            except GhimeraRefused as exc:
                return exc
            finally:
                usage.reserved_bytes -= limits.max_bytes
                usage.reserved_seconds -= limits.timeout_seconds
                usage.calls -= 1
                rows = self._ledger.snapshot()
                if len(rows) > start:
                    row = rows[-1]
                    if row.route == f"search:{provider.identity[0]}@{provider.identity[1]}":
                        self._charge(usage, row)

    async def discover_many(
        self,
        query: SearchQuery,
        *,
        before_call: Callable[[GroundedSearch], None] | None = None,
        query_factory: Callable[[int, GroundedSearch], QueryWork] | None = None,
        restored_cursor: QueryCursor | None = None,
    ) -> tuple[SearchObservation, ...]:
        provider = self._provider
        if not isinstance(provider, DiscoveryProviders):
            return (
                await self._call(
                    provider,
                    query,
                    before_call=before_call,
                    query_work=query_factory(0, provider) if query_factory is not None else None,
                ),
            )
        ordered = self._ordered()
        fanout = provider.policy.mode == "fanout" or (
            provider.policy.cold_start_fanout and not self._rounds
        )
        observations: list[SearchObservation] = []
        failures: list[GhimeraRefused] = []
        result: SearchObservation | GhimeraRefused
        if fanout:
            # Wait for every owned attempt; never lose neighboring evidence on failure.
            results = await asyncio.gather(*(self._attempt(p, query, before_call) for p in ordered))
        else:
            results = []
            for index, bound in enumerate(ordered):
                if restored_cursor is not None and index < restored_cursor.provider_index:
                    previous = self.completed(restored_cursor, provider_index=index)
                    results.extend(previous)
                    if previous and previous[-1].response.hits:
                        break
                    continue
                work = query_factory(index, bound) if query_factory is not None else None
                if work is not None and work.resuming:
                    start = self._ledger.next_sequence
                    try:
                        result = await self._call(bound, query, query_work=work)
                    except GhimeraRefused as exc:
                        result = exc
                    finally:
                        rows = self._ledger.snapshot()
                        if work.resuming and len(rows) > start and rows[-1].query_ack is not None:
                            usage = self._usage.setdefault(bound.identity, _Usage())
                            usage.calls -= 1
                            self._charge(usage, rows[-1])
                else:
                    result = await self._attempt(bound, query, before_call, work)
                results.append(result)
                if isinstance(result, SearchObservation) and result.response.hits:
                    break
                if (
                    isinstance(result, GhimeraRefused)
                    and result.code == RefusalCode.BUDGET_EXHAUSTED
                ):
                    break
        for result in results:
            if isinstance(result, GhimeraRefused):
                failures.append(result)
            else:
                observations.append(result)
        budget_failure = next(
            (exc for exc in failures if exc.code == RefusalCode.BUDGET_EXHAUSTED), None
        )
        if budget_failure is not None:
            raise budget_failure
        if not observations:
            raise failures[-1] if failures else GhimeraRefused(RefusalCode.SEARCH_UNAVAILABLE)
        return tuple(observations)

    def completed(
        self, cursor: QueryCursor, *, provider_index: int | None = None
    ) -> tuple[SearchObservation, ...]:
        """Select by original durable coordinates, never query text or guessed UUID."""
        from ghimera.research_types import SearchRequest

        rows = self._ledger.snapshot()
        result = []
        for row in rows:
            ack = row.query_ack
            if ack is None or ack.result_json is None:
                continue
            original = rows[ack.reservation_sequence].query_reservation
            if original is None or original.channel != "discovery":
                continue
            coordinates = original.cursor
            if (coordinates.stage, coordinates.round_number, coordinates.query_index) != (
                cursor.stage,
                cursor.round_number,
                cursor.query_index,
            ) or (provider_index is not None and coordinates.provider_index != provider_index):
                continue
            request = SearchRequest.model_validate_json(original.request_json)
            result.append(
                SearchObservation(
                    schema="chimera.search-observation/1",
                    sequence=row.sequence,
                    provider=original.provider,
                    provider_revision=original.provider_revision,
                    query=request.query,
                    response=SearchResponse.model_validate_json(ack.result_json),
                )
            )
        return tuple(result)
