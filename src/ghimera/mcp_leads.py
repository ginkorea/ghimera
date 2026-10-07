"""Consume an initialized MCP session, not ambient tools, credentials or synthesis."""

import asyncio
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import JsonValue, TypeAdapter, ValidationError

from ghimera.ahmia_wire import AhmiaHitEvidence
from ghimera.mcp_lead_config import McpLeadConfig
from ghimera.refusals import FetchFailure, RefusalCode
from ghimera.research_types import SearchHit, SearchRequest, SearchResponse
from ghimera.search import GroundedSearch


class McpToolResult(Protocol):
    def model_dump_json(self) -> str: ...


class McpSession(Protocol):
    """The official SDK's initialized ClientSession supplies this port."""

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> McpToolResult: ...


class McpLeadClient(Protocol):
    @property
    def endpoint(self) -> str: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> bytes: ...


class SessionMcpLeadClient:
    """Borrow a caller-owned SDK session; never initialize, close or discover it.

    The application's transport owns authentication and receive-size limits.
    Ghimera additionally bounds the serialized tool result and call deadline.
    """

    def __init__(self, session: McpSession, *, endpoint: str) -> None:
        self._session, self._endpoint = session, endpoint

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> bytes:
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await self._session.call_tool(name, arguments)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Client libraries have different RPC/connection exceptions. This
            # external-port boundary exposes no exception text or credentials.
            raise FetchFailure(RefusalCode.SEARCH_UNAVAILABLE, 0) from None
        raw = result.model_dump_json().encode("utf-8")
        if len(raw) > max_bytes:
            raise FetchFailure(RefusalCode.ADAPTER_CONTRACT, max_bytes)
        return raw


_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError("MCP discovery requires a JSON object")
    return value


def _payload(result: dict[str, JsonValue]) -> dict[str, JsonValue]:
    error = result.get("isError", False)
    if not isinstance(error, bool) or error:
        raise ValueError("MCP tool failed")
    structured = result.get("structuredContent")
    if structured is not None:
        return _object(structured)
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1:
        raise ValueError("MCP tool requires structured content or one JSON text block")
    block = _object(content[0])
    if block.get("type") != "text" or not isinstance(block.get("text"), str):
        raise ValueError("MCP discovery text must be JSON")
    return _object(_JSON.validate_json(str(block["text"])))


def _text(row: dict[str, JsonValue], key: str, *, required: bool = False) -> str:
    value = row.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError("MCP lead field must be text")
    return value


class McpLeadSearch(GroundedSearch):
    name = "mcp"
    revision = "tools-call/1"

    def __init__(self, provider: McpLeadConfig, client: McpLeadClient) -> None:
        self._provider = McpLeadConfig.model_validate(provider.model_dump())
        self._client = client
        self._validate_binding()

    def _validate_binding(self) -> None:
        if self._client.endpoint != self._provider.endpoint:
            raise ValueError("MCP client must match the configured discovery endpoint")

    async def request(self, request: SearchRequest) -> SearchResponse:
        self._validate_binding()
        args: dict[str, JsonValue] = {self._provider.query_argument: request.query.text}
        if self._provider.limit_argument is not None:
            args[self._provider.limit_argument] = request.limit
        raw = await self._client.call_tool(
            self._provider.tool_name,
            args,
            max_bytes=request.max_bytes,
            timeout_seconds=request.timeout_seconds,
        )
        if len(raw) > request.max_bytes:
            raise FetchFailure(RefusalCode.ADAPTER_CONTRACT, request.max_bytes)
        try:
            payload = _payload(_object(_JSON.validate_json(raw)))
            if self._provider.results_path == ("data", "leads"):
                data = _object(payload["data"])
                if "query" in data and data["query"] != request.query.text:
                    raise ValueError("MCP lead response must match its query")
                if data.get("notes") and not data.get("queries_used") and not data.get("leads"):
                    raise ValueError("provider did not search; this is not an empty result")
            value: JsonValue = payload
            for key in self._provider.results_path:
                value = _object(value)[key]
            if not isinstance(value, list):
                raise ValueError("MCP result path must resolve to a lead array")
            hits = []
            for item in value:
                row = _object(item)
                url = _text(row, self._provider.url_field, required=True)
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or any(ord(char) < 33 for char in url)
                ):
                    raise ValueError("MCP lead must be an HTTP(S) URL")
                hits.append(
                    SearchHit(
                        url=url,
                        title=_text(row, self._provider.title_field),
                        snippet=_text(row, self._provider.snippet_field),
                        index_evidence=AhmiaHitEvidence.model_validate(row["index_evidence"])
                        if "index_evidence" in row
                        else None,
                    )
                )
        except (ValidationError, ValueError, KeyError):
            raise FetchFailure(RefusalCode.SEARCH_UNAVAILABLE, len(raw)) from None
        return SearchResponse(raw=raw, hits=tuple(hits[: request.limit]))
