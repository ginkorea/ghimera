"""One research run owns its retained discovery bytes; providers remain stateless ports."""

from ghimera.budget import RunBudget
from ghimera.ledger import Ledger
from ghimera.research_types import SearchObservation, SearchQuery, SearchResponse
from ghimera.search import GroundedSearch


class SearchHistory:
    def __init__(self, provider: GroundedSearch, budget: RunBudget, ledger: Ledger) -> None:
        self._provider, self._budget, self._ledger = provider, budget, ledger
        self._observations: list[SearchObservation] = []

    @property
    def observations(self) -> tuple[SearchObservation, ...]:
        return tuple(sorted(self._observations, key=lambda observation: observation.sequence))

    async def discover(self, query: SearchQuery) -> SearchResponse:
        response = await self._provider.discover(query, self._budget, self._ledger)
        # The final provider template appends before return, with no intervening
        # await. Capture immediately here, before graph/model work or gather can
        # fail. Concurrent completions therefore bind their own append position.
        self._observations.append(
            SearchObservation(
                schema="chimera.search-observation/1",
                sequence=self._ledger.next_sequence - 1,
                provider=self._provider.name,
                provider_revision=self._provider.revision,
                query=query,
                response=response,
            )
        )
        return response
