"""Explicit SearXNG JSON or ordinary HTML search through the guarded source connector."""

import asyncio
import codecs
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, ValidationError

from chimera.config import ChimeraConfig
from chimera.http import CurlRoute, page_barrier
from chimera.models import FetchRequest, Page
from chimera.passive_worker import PassiveWorker
from chimera.refusals import ChimeraRefused, FetchCancelled, FetchFailure, RefusalCode
from chimera.research_types import SearchHit, SearchRequest, SearchResponse
from chimera.search import GroundedSearch
from chimera.search_config import SearxConfig as SearxConfig
from chimera.search_html_types import SearchHtmlRequest, SearchHtmlResponse
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


class SearxTransport:
    """Shared source-only transport; providers own the response dialect."""

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

    async def fetch(self, request: SearchRequest, *, html: bool) -> Page:
        values = {
            "q": request.query.text,
            "language": self._provider.language,
            "safesearch": str(self._provider.safe_search),
            "time_range": self._provider.time_range,
        }
        values.update({"theme": "simple"} if html else {"format": "json"})
        parameters = urlencode(values)
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
        if page.status != 200:
            raise FetchFailure(RefusalCode.SEARCH_UNAVAILABLE, len(page.body))
        return page


class SearxSearch(GroundedSearch):
    name = "searxng"
    revision = "search-api/1"

    def __init__(
        self, config: ChimeraConfig, provider: SearxConfig, *, resolver: Resolver | None = None
    ) -> None:
        if provider.response_format not in {None, "json"}:
            raise ValueError("JSON search requires the JSON response format")
        self._transport = SearxTransport(config, provider, resolver=resolver)

    def transport_selection(self) -> TransportEvidence:
        return self._transport.transport_selection()

    async def request(self, request: SearchRequest) -> SearchResponse:
        page = await self._transport.fetch(request, html=False)
        if page.content_type != "application/json":
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


class SearxHtmlSearch(GroundedSearch):
    name = "searxng"
    revision = "search-html/1"

    def __init__(
        self, config: ChimeraConfig, provider: SearxConfig, *, resolver: Resolver | None = None
    ) -> None:
        if provider.response_format != "html":
            raise ValueError("HTML search requires the explicit HTML response format")
        if config.extraction is None:
            raise ValueError("HTML search requires a bounded extraction worker recipe")
        self._config = config.extraction
        try:
            if version("scrapling") != "0.4.2":
                raise ValueError("HTML search requires the pinned Scrapling parser")
            codecs.lookup(self._config.default_encoding)
        except (PackageNotFoundError, LookupError):
            raise ValueError(
                "HTML search requires its installed parser and configured encoding"
            ) from None
        self._transport = SearxTransport(config, provider, resolver=resolver)
        self._worker = PassiveWorker(
            interpreter=Path(sys.executable),
            module="chimera.searx_html_worker",
            work_directory=self._config.work_directory,
            max_workers=self._config.max_workers,
            timeout_seconds=self._config.timeout_seconds,
            max_output_bytes=self._config.max_output_bytes,
            max_diagnostic_bytes=self._config.max_diagnostic_bytes,
            cleanup_timeout_seconds=self._config.cleanup_timeout_seconds,
            environment={
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

    def transport_selection(self) -> TransportEvidence:
        return self._transport.transport_selection()

    async def request(self, request: SearchRequest) -> SearchResponse:
        page = await self._transport.fetch(request, html=True)
        if page.content_type != "text/html" or len(page.body) > self._config.max_input_bytes:
            raise FetchFailure(RefusalCode.SEARCH_UNAVAILABLE, len(page.body))
        encoding = self._config.default_encoding
        for name, value in page.headers:
            if name.lower() == "content-type":
                for part in value.split(";")[1:]:
                    if part.strip().lower().startswith("charset="):
                        encoding = part.strip().split("=", 1)[1].strip().strip("\"'")
        try:
            payload = SearchHtmlRequest(page=page, encoding=encoding, limit=request.limit)
            wire = SearchHtmlResponse.model_validate_json(
                await self._worker.run(payload.model_dump_json().encode())
            )
            if wire.refusal is not None:
                raise ChimeraRefused(wire.refusal)
            if wire.hits is None or len(wire.hits) > request.limit:
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        except asyncio.CancelledError:
            raise FetchCancelled(len(page.body)) from None
        except ChimeraRefused as exc:
            raise FetchFailure(exc.code, len(page.body)) from None
        except ValueError:
            raise FetchFailure(RefusalCode.ADAPTER_CONTRACT, len(page.body)) from None
        return SearchResponse(raw=page.body, hits=wire.hits, transport=page.transport)
