"""Configured SearXNG JSON search through the same direct/Tor source connector."""

from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, ValidationError

from chimera.config import ChimeraConfig
from chimera.http import CurlRoute, page_barrier
from chimera.models import FetchRequest
from chimera.refusals import FetchFailure, RefusalCode
from chimera.research_types import SearchHit, SearchRequest, SearchResponse
from chimera.search import GroundedSearch
from chimera.search_config import SearxConfig as SearxConfig
from chimera.transport import Resolver
from chimera.transport_types import TransportEvidence


class SearchWireHit(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    url: str
    title: str
    content: str = ""


class SearchWire(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    results: tuple[SearchWireHit, ...]


class SearxSearch(GroundedSearch):
    name = "searxng"
    revision = "search-api/1"

    def __init__(
        self,
        config: ChimeraConfig,
        provider: SearxConfig,
        *,
        resolver: Resolver | None = None,
    ) -> None:
        if config.search is not None and config.search != provider:
            raise ValueError("search provider must match the run's effective search recipe")
        # Discovery is not a collected source. Source cookies must neither be
        # required here nor borrowed for its separate service endpoint.
        search_config = ChimeraConfig.model_validate(
            dict(config.model_dump(by_alias=True), source_sessions=())
        )
        self._route, self._provider = CurlRoute(search_config, resolver=resolver), provider

    def transport_selection(self) -> TransportEvidence:
        return self._route.transport_selection(self._provider.endpoint)

    async def request(self, request: SearchRequest) -> SearchResponse:
        parameters = urlencode(
            {
                "q": request.query.text,
                "format": "json",
                "language": self._provider.language,
                "safesearch": self._provider.safe_search,
                "time_range": self._provider.time_range,
            }
        )
        page = await self._route.execute(
            FetchRequest(
                url=self._provider.endpoint + "?" + parameters,
                max_bytes=request.max_bytes,
                timeout_seconds=request.timeout_seconds,
            )
        )
        barrier = page_barrier(page)
        if barrier is not None:
            raise FetchFailure(barrier, len(page.body))
        if page.status != 200 or page.content_type != "application/json":
            raise FetchFailure(RefusalCode.SEARCH_UNAVAILABLE, len(page.body))
        try:
            value = SearchWire.model_validate_json(page.body)
        except ValidationError:
            raise FetchFailure(RefusalCode.SEARCH_UNAVAILABLE, len(page.body)) from None
        return SearchResponse(
            raw=page.body,
            transport=page.transport,
            hits=tuple(
                SearchHit(url=hit.url, title=hit.title, snippet=hit.content)
                for hit in value.results[: request.limit]
            ),
        )
