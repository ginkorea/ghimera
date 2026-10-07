"""Read-only Ahmia index leads; no hosted-site scraping or hidden index writes."""

import json
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import JsonValue, SecretStr, ValidationError

from ghimera.ahmia_config import AhmiaConfig
from ghimera.ahmia_wire import decode_ahmia
from ghimera.private_json import JsonResponse, JsonWireCancelled, JsonWireFailure, PinnedJsonHttp
from ghimera.refusals import FetchCancelled, FetchFailure, RefusalCode
from ghimera.research_types import SearchHit, SearchRequest, SearchResponse
from ghimera.search import GroundedSearch
from ghimera.transport import Resolver


class AhmiaHttpPort(Protocol):
    @property
    def config(self) -> AhmiaConfig: ...

    async def post(
        self, body: bytes, *, max_bytes: int | None = None, timeout_seconds: float | None = None
    ) -> JsonResponse: ...


class AhmiaIndexSearch(GroundedSearch):
    name = "ahmia"
    revision = "index-search/1"

    def __init__(
        self,
        config: AhmiaConfig,
        *,
        credential: SecretStr | None = None,
        resolver: Resolver | None = None,
        http: AhmiaHttpPort | None = None,
    ) -> None:
        self._config = AhmiaConfig.model_validate(config.model_dump())
        if http is not None and credential is not None:
            raise ValueError("an injected Ahmia transport owns its credential boundary")
        self._http = http or PinnedJsonHttp(self._config, credential=credential, resolver=resolver)
        if self._http.config != self._config:
            raise ValueError("Ahmia transport must match its recorded index recipe")

    @property
    def config(self) -> AhmiaConfig:
        return self._config

    @property
    def identity(self) -> tuple[str, str]:
        return self.config.identity

    async def request(self, request: SearchRequest) -> SearchResponse:
        request = SearchRequest.model_validate(request.model_dump())
        if self._http.config != self.config:
            raise ValueError("Ahmia transport changed its recorded recipe")
        retrieved_at = datetime.now(UTC)
        filters: list[JsonValue] = [{"term": {"is_banned": False}}]
        if self.config.max_observation_age_seconds is not None:
            filters.append(
                {
                    "range": {
                        "updated_on": {
                            "gte": (
                                retrieved_at
                                - timedelta(seconds=self.config.max_observation_age_seconds)
                            ).isoformat(),
                            "lte": retrieved_at.isoformat(),
                        }
                    }
                }
            )
        limit = min(request.limit, self.config.max_results)
        body: dict[str, JsonValue] = {
            "size": limit,
            "track_total_hits": False,
            "_source": ["url", "title", "meta", "content", "updated_on", "is_banned"],
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": request.query.text,
                                "fields": [
                                    field.name + "^" + str(field.boost)
                                    for field in self.config.query_fields
                                ],
                                "operator": self.config.query_operator,
                            }
                        }
                    ],
                    "filter": filters,
                }
            },
        }
        try:
            response = await self._http.post(
                json.dumps(
                    body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                ).encode(),
                max_bytes=request.max_bytes,
                timeout_seconds=request.timeout_seconds,
            )
        except JsonWireFailure as exc:
            raise FetchFailure(RefusalCode.SEARCH_UNAVAILABLE, len(exc.response.body)) from None
        except JsonWireCancelled as exc:
            raise FetchCancelled(len(exc.response.body)) from None
        retrieved_at = datetime.now(UTC)
        try:
            if (
                response.status != 200
                or response.content_type != "application/json"
                or len(response.body) > min(request.max_bytes, self.config.max_response_bytes)
            ):
                raise ValueError("Ahmia index did not return a bounded JSON search")
            leads = decode_ahmia(response.body, self.config, limit=limit, retrieved_at=retrieved_at)
        except (ValueError, ValidationError):
            raise FetchFailure(
                RefusalCode.SEARCH_UNAVAILABLE, min(len(response.body), request.max_bytes)
            ) from None
        return SearchResponse(
            raw=response.body,
            index_retrieved_at=retrieved_at,
            hits=tuple(
                SearchHit(
                    url=lead.url,
                    title=lead.title,
                    snippet=lead.snippet,
                    index_evidence=lead.index_evidence,
                )
                for lead in leads
            ),
        )

    async def tool_payload(self, request: SearchRequest) -> dict[str, JsonValue]:
        """Application-owned onion_search handler; the host owns RPC admission/auth.

        This does not start an MCP server or a research run. The caller supplies
        explicit per-call limits; the bound index client also enforces its recipe.
        """
        response = await self.request(request)
        leads: list[JsonValue] = []
        for hit in response.hits:
            evidence = hit.index_evidence
            if evidence is None:
                raise ValueError("Ahmia lead requires its index observation")
            leads.append(
                {
                    "url": hit.url,
                    "title": hit.title,
                    "snippet": hit.snippet,
                    "index_evidence": {
                        "schema": evidence.schema_version,
                        "index_name": evidence.index_name,
                        "index_revision": evidence.index_revision,
                        "hit_id": evidence.hit_id,
                        "updated_on": evidence.updated_on.isoformat(),
                        "retrieved_at": evidence.retrieved_at.isoformat(),
                        "retrieval_score": evidence.retrieval_score,
                    },
                }
            )
        payload: dict[str, JsonValue] = {
            "data": {
                "query": request.query.text,
                "queries_used": [request.query.text],
                "leads": leads,
            }
        }
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) > min(
            request.max_bytes, self.config.max_response_bytes
        ):
            raise FetchFailure(RefusalCode.SEARCH_UNAVAILABLE, len(response.raw))
        return payload
