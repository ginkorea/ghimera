"""Every shipped search provider shares one final, bounded, recorded boundary.

Mutation witnesses: final-template protection, pre-call fetch reservation, and
response-limit validation are exercised in test_contract_mutations.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from chimera.budget import RunBudget
from chimera.config import ChimeraConfig
from chimera.ledger import Ledger
from chimera.refusals import ChimeraRefused, RefusalCode
from chimera.research_types import SearchQuery, SearchResponse
from chimera.searxng import SearxConfig, SearxSearch
from tests.test_http_fetch import ResolverFixture
from tests.test_intent_research import SearchFixture, policy
from tests.test_tor_transport import NoLocalDNS, socks_server
from tests.test_tor_transport import policy as tor_policy


@pytest.fixture
def endpoint():
    seen = []
    wire = {
        "status": 200,
        "content_type": "application/json",
        "body": json.dumps(
            {
                "results": [
                    {"url": "https://example.org/one", "title": "Ports", "content": "snippet"}
                ],
            }
        ).encode(),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.append(self.path)
            self.send_response(wire["status"])
            self.send_header("Content-Type", wire["content_type"])
            self.send_header("Content-Length", str(len(wire["body"])))
            self.end_headers()
            self.wfile.write(wire["body"])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, seen, wire
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def provider(endpoint, *, transport=None, resolver=None):
    from tests.test_c0 import config

    port = endpoint[0]
    raw = config(research=policy()).model_dump(by_alias=True)
    raw["http"]["network"] = {
        "schema": "chimera.network/1",
        "mode": "loopback_fixture",
        "fixture_addresses": ["127.0.0.1"],
        "fixture_ports": [port],
    }
    raw["transport"] = transport
    cfg = ChimeraConfig.model_validate(raw)
    service = SearxConfig(
        schema="chimera.searxng/1",
        endpoint=f"http://fixture.example:{port}/search",
        language="zh-TW",
        safe_search=1,
        time_range="month",
    )
    return SearxSearch(cfg, service, resolver=resolver or ResolverFixture()), cfg


@pytest.mark.parametrize("kind", ["fixture", "searxng"])
def test_search_conformance(kind, endpoint):
    real, cfg = provider(endpoint)
    adapter = SearchFixture() if kind == "fixture" else real
    budget, ledger = RunBudget(cfg, lambda: 0.0), Ledger()
    response = asyncio.run(
        adapter.discover(SearchQuery(text="ports & reports", question_ids=("q1",)), budget, ledger)
    )
    assert response.hits[0].url == "https://example.org/one"
    assert budget.search_calls == budget.fetches == len(ledger.snapshot()) == 1
    assert budget.bytes_read == ledger.snapshot()[0].bytes_read == len(response.raw)
    assert ledger.snapshot()[0].route == f"search:{adapter.name}@{adapter.revision}"
    assert ledger.snapshot()[0].query == "ports & reports"
    if kind == "searxng":
        parameters = parse_qs(urlsplit(endpoint[1][0]).query)
        assert parameters["q"] == ["ports & reports"]
        assert parameters["format"] == ["json"]
        assert parameters["language"] == ["zh-TW"]
        assert parameters["time_range"] == ["month"]


def test_search_cannot_override_template_or_spend_without_reservation():
    from chimera.search import GroundedSearch
    from tests.test_c0 import config

    with pytest.raises(TypeError):
        type(
            "BadSearch",
            (GroundedSearch,),
            {"name": "bad", "revision": "1", "discover": lambda: None},
        )
    with pytest.raises(TypeError):
        type("Missing", (GroundedSearch,), {})
    cfg = config(research=policy(), page_budget=1)
    budget, ledger, adapter = RunBudget(cfg, lambda: 0.0), Ledger(), SearchFixture()
    budget.fetches = 1
    with pytest.raises(ChimeraRefused, match="budget_exhausted"):
        asyncio.run(
            adapter.discover(SearchQuery(text="ports", question_ids=("q1",)), budget, ledger)
        )
    assert not adapter.requests and not ledger.snapshot()


def test_search_response_limit_is_a_contract_not_a_suggestion():
    from tests.test_c0 import config

    class Oversized(SearchFixture):
        async def request(self, request):
            return SearchResponse(raw=b"x" * (request.max_bytes + 1), hits=())

    cfg = config(research=policy(), byte_budget=20)
    budget, ledger = RunBudget(cfg, lambda: 0.0), Ledger()
    with pytest.raises(ChimeraRefused, match="adapter_contract"):
        asyncio.run(
            Oversized().discover(SearchQuery(text="ports", question_ids=("q1",)), budget, ledger)
        )
    assert budget.bytes_read == ledger.snapshot()[0].bytes_read == 20
    assert ledger.snapshot()[0].refusal == RefusalCode.ADAPTER_CONTRACT


@pytest.mark.parametrize("failure", ["disabled_json", "malformed_json", "redirect"])
def test_http_search_refusal_retains_bytes_without_following_or_inventing(endpoint, failure):
    if failure == "disabled_json":
        endpoint[2]["status"] = 403
    elif failure == "redirect":
        endpoint[2]["status"] = 302
    else:
        endpoint[2]["body"] = b'{"wrong":"wire"}'
    adapter, cfg = provider(endpoint)
    budget, ledger = RunBudget(cfg, lambda: 0.0), Ledger()
    with pytest.raises(ChimeraRefused, match="search_unavailable"):
        asyncio.run(
            adapter.discover(SearchQuery(text="ports", question_ids=("q1",)), budget, ledger)
        )
    assert len(endpoint[1]) == 1
    assert budget.bytes_read == len(endpoint[2]["body"]) == ledger.snapshot()[0].bytes_read


def test_search_uses_same_tor_route_without_local_dns(endpoint):
    async def scenario():
        seen = []
        server = await socks_server(endpoint[0], seen)
        async with server:
            tp = tor_policy(server.sockets[0].getsockname()[1]).model_dump(by_alias=True)
            tp["tor"]["allowed_ports"] = [endpoint[0]]
            adapter, cfg = provider(endpoint, transport=tp, resolver=NoLocalDNS())
            budget, ledger = RunBudget(cfg, asyncio.get_running_loop().time), Ledger()
            response = await adapter.discover(
                SearchQuery(text="ports", question_ids=("q1",)), budget, ledger
            )
            assert response.transport.mode == ledger.snapshot()[0].transport.mode == "tor"
            assert [row[0] for row in seen] == [0xF0, 1]

    asyncio.run(scenario())
