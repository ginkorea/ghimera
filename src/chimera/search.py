"""Grounded discovery's final accounting boundary; providers supply request only."""

import asyncio
import hashlib
from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import ClassVar, final

from chimera.budget import RunBudget
from chimera.ledger import Ledger
from chimera.models import LedgerRow
from chimera.refusals import ChimeraRefused, FetchCancelled, FetchFailure, RefusalCode
from chimera.research_types import SearchQuery, SearchRequest, SearchResponse
from chimera.transport_types import TransportEvidence


class GroundedSearch(ABC):
    name: ClassVar[str]
    revision: ClassVar[str]
    TEMPLATE: ClassVar[str] = "discover"
    REFERENCE = MappingProxyType({"declaration": "SearchFixture", "template": "SearchFixture"})

    def transport_selection(self) -> TransportEvidence | None:
        return None

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
        self, query: SearchQuery, budget: RunBudget, ledger: Ledger
    ) -> SearchResponse:
        policy = budget.config.research
        if policy is None or len(query.text) > policy.max_query_chars or not query.text.strip():
            raise ChimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        budget.reserve_search()
        maximum = (
            budget.config.http.max_response_bytes
            if budget.config.http is not None
            else budget.config.byte_budget
        )
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
            limit=policy.results_per_query,
            max_bytes=allowance,
            timeout_seconds=min(budget.config.request_timeout_seconds, budget.remaining_seconds),
        )
        try:
            try:
                async with asyncio.timeout(request.timeout_seconds):
                    response = await self.request(request)
                size = min(len(response.raw), allowance)
                if len(response.raw) > allowance or len(response.hits) > request.limit:
                    code = RefusalCode.ADAPTER_CONTRACT
            except (ChimeraRefused, TimeoutError) as exc:
                code = (
                    exc.code if isinstance(exc, ChimeraRefused) else RefusalCode.SEARCH_UNAVAILABLE
                )
                size = min(exc.bytes_read, allowance) if isinstance(exc, FetchFailure) else 0
            except asyncio.CancelledError as exc:
                size = min(exc.bytes_read, allowance) if isinstance(exc, FetchCancelled) else 0
                code, cancelled = RefusalCode.SEARCH_UNAVAILABLE, True
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="fetch",
                    route=f"search:{self.name}@{self.revision}",
                    query=query.text,
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
                raise ChimeraRefused(code)
            if response is None:
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            return response
        finally:
            budget.release_bytes(allowance)

    @abstractmethod
    async def request(self, request: SearchRequest) -> SearchResponse: ...
