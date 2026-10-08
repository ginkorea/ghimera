"""Stateless configured provider set; routing and quotas belong to SearchHistory."""

from collections.abc import Mapping
from urllib.parse import urlsplit

from ghimera.budget import RunBudget
from ghimera.corpus_search import CorpusLeadSearch, corpus_leads
from ghimera.corpus_search_wire import CorpusSearchWire
from ghimera.discovery_config import DiscoveryConfig, DiscoveryProvider, Domain
from ghimera.ledger import Ledger
from ghimera.query_work import QueryWork
from ghimera.query_work_types import QueryCorpusBinding, QueryReservation
from ghimera.research_reranking_types import RerankDecision
from ghimera.research_types import SearchQuery, SearchRequest, SearchResponse
from ghimera.search import GroundedSearch
from ghimera.transport_types import TransportEvidence


class BoundSearch(GroundedSearch):
    name = "configured"
    revision = "discovery-binding/1"

    def __init__(
        self, policy: DiscoveryProvider, adapter: GroundedSearch, domains: tuple[Domain, ...]
    ) -> None:
        self.policy = DiscoveryProvider.model_validate(policy.model_dump())
        self._adapter = adapter
        self._domains = set(domains) & set(self.policy.domains)

    @property
    def identity(self) -> tuple[str, str]:
        return self.policy.identity

    def transport_selection(self) -> TransportEvidence | None:
        return self._adapter.transport_selection()

    def query_binding(self, query: SearchQuery) -> QueryCorpusBinding | None:
        return self._adapter.query_binding(query)

    def admit_query(self, reservation: QueryReservation, response: SearchResponse | None) -> None:
        if response is not None and isinstance(self._adapter, CorpusLeadSearch):
            request = SearchRequest.model_validate_json(reservation.request_json)
            wire = CorpusSearchWire.model_validate_json(response.raw)
            native = response.model_copy(update={"hits": corpus_leads(wire, self._adapter.policy)})
            if self._filter(request, native) != response:
                raise ValueError("query ACK changed its original configured-domain projection")
            response = native
        self._adapter.admit_query(reservation, response)

    async def request(self, request: SearchRequest) -> SearchResponse:
        # Only the inherited final template owns reservations and observations.
        response = await self._adapter.request(request)
        return self._filter(request, response)

    async def request_for_run(
        self,
        request: SearchRequest,
        budget: RunBudget,
        ledger: Ledger,
        rerank_decision: RerankDecision | None,
        query_work: QueryWork | None = None,
    ) -> SearchResponse:
        response = (
            await self._adapter.request_for_run(request, budget, ledger, rerank_decision)
            if query_work is None
            else await self._adapter.request_for_run(
                request, budget, ledger, rerank_decision, query_work
            )
        )
        return self._filter(request, response)

    def _filter(self, request: SearchRequest, response: SearchResponse) -> SearchResponse:
        if len(response.hits) > request.limit:
            return response  # The final accounting template must detect the violation.
        hits = []
        for hit in response.hits:
            try:
                host = urlsplit(hit.url).hostname or ""
                domain = "onion" if host.endswith(".onion") else "open_web"
                if domain in self._domains:
                    hits.append(hit)
            except ValueError:
                continue
        return response.model_copy(update={"hits": tuple(hits)})


class DiscoveryProviders:
    def __init__(self, policy: DiscoveryConfig, adapters: Mapping[str, GroundedSearch]) -> None:
        self.policy = DiscoveryConfig.model_validate(policy.model_dump())
        if set(adapters) != {provider.id for provider in self.policy.providers}:
            raise ValueError("every discovery provider requires exactly one bound adapter")
        self.providers = tuple(
            BoundSearch(p, adapters[p.id], self.policy.target_domains)
            for p in self.policy.providers
        )

    @property
    def identity(self) -> tuple[str, str]:
        return self.policy.identity
