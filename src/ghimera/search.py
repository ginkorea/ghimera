"""Grounded discovery's final accounting boundary; providers supply request only."""

import asyncio
import hashlib
from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import ClassVar, final

from ghimera.budget import RunBudget
from ghimera.discovery_config import SearchCallLimits
from ghimera.ledger import Ledger
from ghimera.models import LedgerRow
from ghimera.refusals import FetchCancelled, FetchFailure, GhimeraRefused, RefusalCode
from ghimera.research_reranking_types import RerankDecision
from ghimera.research_types import SearchQuery, SearchRequest, SearchResponse
from ghimera.transport_types import TransportEvidence


class GroundedSearch(ABC):
    name: ClassVar[str]
    revision: ClassVar[str]
    TEMPLATE: ClassVar[str] = "discover"
    REFERENCE = MappingProxyType({"declaration": "SearchFixture", "template": "SearchFixture"})

    def transport_selection(self) -> TransportEvidence | None:
        return None

    @property
    def identity(self) -> tuple[str, str]:
        return self.name, self.revision

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if "discover" in cls.__dict__:
            raise TypeError("GroundedSearch.discover is final; implement request")
        if any(
            not isinstance(getattr(cls, key, None), str) or not getattr(cls, key).strip()
            for key in ("name", "revision")
        ):
            raise TypeError("GroundedSearch declares nonblank provider name and revision")

    @final
    async def discover(
        self,
        query: SearchQuery,
        budget: RunBudget,
        ledger: Ledger,
        *,
        limits: SearchCallLimits | None = None,
        rerank_decision: RerankDecision | None = None,
    ) -> SearchResponse:
        policy = budget.config.research
        if policy is None or len(query.text) > policy.max_query_chars or not query.text.strip():
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        if limits is not None:
            limits = SearchCallLimits.model_validate(limits.model_dump())
        budget.reserve_search()
        maximum = (
            budget.config.http.max_response_bytes
            if budget.config.http is not None
            else budget.config.byte_budget
        )
        if limits is not None:
            maximum = min(maximum, limits.max_bytes)
        allowance = budget.reserve_bytes(maximum)
        try:
            budget.reserve_fetch()
        except BaseException:
            budget.release_bytes(allowance)
            raise
        started = budget.clock()
        response = None
        code = None
        size = 0
        cancelled = False
        request = SearchRequest(
            query=query,
            limit=min(policy.results_per_query, limits.limit)
            if limits
            else policy.results_per_query,
            max_bytes=allowance,
            timeout_seconds=min(
                budget.config.request_timeout_seconds,
                budget.remaining_seconds,
                limits.timeout_seconds if limits else budget.remaining_seconds,
            ),
        )
        try:
            try:
                async with asyncio.timeout(request.timeout_seconds):
                    response = await self.request_for_run(request, budget, ledger, rerank_decision)
                size = min(len(response.raw), allowance)
                if len(response.raw) > allowance or len(response.hits) > request.limit:
                    code = RefusalCode.ADAPTER_CONTRACT
            except (GhimeraRefused, TimeoutError) as exc:
                code = (
                    exc.code if isinstance(exc, GhimeraRefused) else RefusalCode.SEARCH_UNAVAILABLE
                )
                size = min(exc.bytes_read, allowance) if isinstance(exc, FetchFailure) else 0
            except asyncio.CancelledError as exc:
                size = min(exc.bytes_read, allowance) if isinstance(exc, FetchCancelled) else 0
                code, cancelled = RefusalCode.SEARCH_UNAVAILABLE, True
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="fetch",
                    route=f"search:{self.identity[0]}@{self.identity[1]}",
                    query=query.text,
                    search_response_sha256=response.content_digest()
                    if response is not None and code is None
                    else None,
                    refusal=code,
                    bytes_read=size,
                    latency_seconds=max(0.0, budget.clock() - started),
                    transport=response.transport
                    if response is not None
                    else self.transport_selection(),
                    reason=(
                        code.value
                        if code
                        else "grounded_search:" + hashlib.sha256(response.raw).hexdigest()
                        if response is not None
                        else "search_contract"
                    ),
                )
            )
            budget.record_bytes(size)
            if cancelled:
                raise asyncio.CancelledError
            if code is not None:
                raise GhimeraRefused(code)
            if response is None:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            return response
        finally:
            budget.release_bytes(allowance)

    async def request_for_run(
        self,
        request: SearchRequest,
        budget: RunBudget,
        ledger: Ledger,
        rerank_decision: RerankDecision | None,
    ) -> SearchResponse:
        """Default providers retain their port; the final template owns all accounting."""
        return await self.request(request)

    @abstractmethod
    async def request(self, request: SearchRequest) -> SearchResponse: ...
