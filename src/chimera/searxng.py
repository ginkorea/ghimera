"""Configured SearXNG JSON search through the same direct/Tor source connector."""

import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from chimera.config import ChimeraConfig
from chimera.http import CurlRoute, page_barrier
from chimera.models import FetchRequest
from chimera.refusals import FetchFailure, RefusalCode
from chimera.research_types import SearchHit, SearchRequest, SearchResponse
from chimera.search import GroundedSearch
from chimera.transport import Resolver
from chimera.transport_types import TransportEvidence


class SearxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.searxng/1"] = Field(alias="schema")
    endpoint: str
    language: Annotated[str, Field(min_length=1)]
    safe_search: Annotated[int, Field(strict=True, ge=0, le=2)]
    time_range: Literal["", "day", "month", "year"]

    @model_validator(mode="after")
    def exact_endpoint(self) -> "SearxConfig":
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path
            or any(ord(char) < 33 for char in self.endpoint)
        ):
            raise ValueError("search endpoint must be one credential-free HTTP(S) URL")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError("this source connector requires an exact DNS/onion hostname")
        return self


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
