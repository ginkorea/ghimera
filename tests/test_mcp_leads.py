"""Platform-shaped MCP lead envelopes, bounded failures and Collector composition."""

import asyncio
import json

import pytest

from ghimera.collector import Collector
from ghimera.config import GhimeraConfig
from ghimera.mcp_lead_config import McpLeadConfig
from ghimera.mcp_leads import McpLeadSearch, SessionMcpLeadClient
from ghimera.refusals import FetchFailure, RefusalCode
from ghimera.research_types import ResearchResult, SearchQuery, SearchRequest
from tests.test_collector import (
    assembled,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)
from tests.test_http_fetch import ResolverFixture

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]

ENDPOINT = "https://mcp.example.org/mcp"


def recipe(**updates):
    return McpLeadConfig(schema="ghimera.mcp-leads/1", endpoint=ENDPOINT, **updates)


def request(**updates):
    return SearchRequest(
        query=SearchQuery(text="find ports", question_ids=("q1",)),
        limit=2,
        max_bytes=10000,
        timeout_seconds=5.0,
        **updates,
    )


def envelope(url="https://example.org/report"):
    # server.envelope_schema wraps WebLeads in data/handling_summary;
    # server._both_blocks emits the SAME envelope as text and structured JSON.
    payload = {
        "data": {
            "governed": False,
            "caveat": "Web leads are not document evidence.",
            "query": "find ports",
            "answer": "Model prose contains https://not-a-lead.example/ignore",
            "queries_used": ["port research"],
            "engine": "fixture/search",
            "notes": [],
            "leads": [
                {
                    "lead_id": "lead:opaque",
                    "url": url,
                    "title": "港口調查",
                    "snippet": "Discovery snippet only",
                    "grounding_url": "",
                    "resolved_url": url,
                    "resolution_status": "direct",
                    "source": "fixture",
                    "rank": 1,
                }
            ],
        },
        "handling_summary": {"visible": 0},
        "result_ref": None,
    }
    return {
        "isError": False,
        "structuredContent": payload,
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
    }


class FixtureClient:
    endpoint = ENDPOINT

    def __init__(self, result, *, echo_query=False):
        self.raw = json.dumps(result, ensure_ascii=False).encode()
        self.calls = []
        self.echo_query = echo_query

    async def call_tool(self, name, arguments, *, max_bytes, timeout_seconds):
        self.calls.append((name, arguments, max_bytes, timeout_seconds))
        if self.echo_query:
            result = json.loads(self.raw)
            result["structuredContent"]["data"]["query"] = arguments["query"]
            result["content"][0]["text"] = json.dumps(result["structuredContent"])
            return json.dumps(result).encode()
        return self.raw


@pytest.mark.parametrize("structured", [True, False])
def test_default_platform_envelope_uses_only_returned_leads(structured):
    result = envelope()
    if not structured:
        result.pop("structuredContent")
    client = FixtureClient(result)
    response = asyncio.run(McpLeadSearch(recipe(), client).request(request()))
    assert [hit.url for hit in response.hits] == ["https://example.org/report"]
    assert response.hits[0].title == "港口調查"
    assert response.raw == client.raw
    assert client.calls == [("web_search", {"query": "find ports", "limit": 2}, 10000, 5.0)]


@pytest.mark.parametrize("fault", ["is_error", "missing", "not_searched", "query", "bad_url"])
def test_errors_never_become_successful_empty_discovery(fault):
    result = envelope()
    if fault == "is_error":
        result["isError"] = True
    elif fault == "missing":
        del result["structuredContent"]["data"]["leads"]
    elif fault == "not_searched":
        result["structuredContent"]["data"].update(
            leads=[], queries_used=[], notes=["provider not configured"]
        )
    elif fault == "query":
        result["structuredContent"]["data"]["query"] = "another query"
    else:
        result["structuredContent"]["data"]["leads"][0]["url"] = "file:///etc/passwd"
    client = FixtureClient(result)
    with pytest.raises(FetchFailure) as caught:
        asyncio.run(McpLeadSearch(recipe(), client).request(request()))
    assert caught.value.code == RefusalCode.SEARCH_UNAVAILABLE
    assert caught.value.bytes_read == len(client.raw)


def test_custom_tool_mapping_and_empty_success():
    client = FixtureClient({"structuredContent": {"results": []}})
    provider = recipe(tool_name="onion_search", results_path=("results",), limit_argument=None)
    assert asyncio.run(McpLeadSearch(provider, client).request(request())).hits == ()
    assert client.calls[0][0:2] == ("onion_search", {"query": "find ports"})


def test_oversized_and_mismatched_binding_fail_closed():
    client = FixtureClient(envelope())
    small = request().model_copy(update={"max_bytes": 20})
    with pytest.raises(FetchFailure) as caught:
        asyncio.run(McpLeadSearch(recipe(), client).request(small))
    assert caught.value.code == RefusalCode.ADAPTER_CONTRACT
    assert caught.value.bytes_read == 20
    client.endpoint = "https://other.example/mcp"
    with pytest.raises(ValueError, match="configured discovery endpoint"):
        McpLeadSearch(recipe(), client)


def test_official_session_adapter_borrows_session_and_normalizes_errors():
    calls = []

    class ToolResult:
        def model_dump_json(self):
            return json.dumps(envelope())

    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return ToolResult()

    client = SessionMcpLeadClient(Session(), endpoint=ENDPOINT)
    result = asyncio.run(McpLeadSearch(recipe(), client).request(request()))
    assert result.hits and calls == [("web_search", {"query": "find ports", "limit": 2})]

    class FailedSession:
        async def call_tool(self, name, arguments):
            raise RuntimeError("private transport detail")

    client = SessionMcpLeadClient(FailedSession(), endpoint=ENDPOINT)
    with pytest.raises(FetchFailure) as caught:
        asyncio.run(McpLeadSearch(recipe(), client).request(request()))
    assert "private" not in str(caught.value)


def test_collector_mcp_discovery_fetches_sources_and_keeps_native_evidence(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config, url = assembled(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    values = config.model_dump()
    values["search"] = recipe().model_dump()
    config = GhimeraConfig.model_validate(values)
    with pytest.raises(ValueError, match="explicitly bound MCP client"):
        Collector(config, source_resolver=ResolverFixture())
    assert not model_endpoint[1] and not source_site[1]
    client = FixtureClient(envelope(url), echo_query=True)
    result = asyncio.run(
        Collector(config, mcp_client=client, source_resolver=ResolverFixture()).run("find ports")
    )
    assert result.status == "answered"
    assert result.harvest.documents[0].url == url
    assert result.answer.claims[0].citations[0].matches(result.harvest.documents[0])
    assert source_site[1]["/plain"] == 1 and not search_endpoint[1]
    assert client.calls[0][0] == "web_search"
    assert result.search_provider == "mcp"
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result
    assert any(row.route == "search:mcp@tools-call/1" for row in result.harvest.ledger)
