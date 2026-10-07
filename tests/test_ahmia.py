"""Ahmia protocol fixtures; not a deployed index or real search-quality acceptance."""

import asyncio
import copy
import json
import threading
import time
import tomllib
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from ghimera.ahmia import AhmiaIndexSearch
from ghimera.ahmia_config import AhmiaConfig
from ghimera.ahmia_wire import decode_ahmia
from ghimera.collector import Collector
from ghimera.config import GhimeraConfig
from ghimera.mcp_leads import McpLeadSearch
from ghimera.private_json import JsonWireFailure, PinnedJsonHttp
from ghimera.refusals import FetchFailure
from ghimera.research_types import ResearchResult, SearchQuery, SearchRequest, SearchResponse
from ghimera.result_archive import ResearchResultArchive
from tests.test_collector import (
    assembled,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)
from tests.test_discovery_router import discovery, provider, state
from tests.test_http_fetch import ResolverFixture
from tests.test_mcp_leads import FixtureClient, recipe
from tests.test_tor_transport import onion, policy, socks_server

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def binding(port, **updates):
    values = dict(
        schema="ghimera.ahmia/1",
        endpoint=f"http://127.0.0.1:{port}/tor-fixture/_search",
        approved_addresses=["127.0.0.1"],
        allow_plaintext=True,
        allow_plaintext_credentials=False,
        authorization="none",
        timeout_seconds=2.0,
        max_request_bytes=10000,
        max_response_bytes=10000,
        max_header_bytes=8192,
        index_name="tor-fixture",
        index_revision="operator-snapshot-1",
        query_fields=[{"name": "title", "boost": 2.0}, {"name": "content", "boost": 1.0}],
        query_operator="and",
        max_results=2,
        snippet_chars=80,
        age_policy="bounded_age",
        max_observation_age_seconds=3600,
    )
    values.update(updates)
    return AhmiaConfig.model_validate(values)


def request(**updates):
    values = dict(
        query=SearchQuery(text='find ports {"query":{"match_all":{}}}', question_ids=("q1",)),
        limit=2,
        max_bytes=10000,
        timeout_seconds=2.0,
    )
    values.update(updates)
    return SearchRequest(**values)


def wire(url=None):
    return {
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {
            "hits": [
                {
                    "_index": "tor-fixture",
                    "_id": "source-1",
                    "_score": 14.25,
                    "_source": {
                        "url": url or f"http://{onion()}/report",
                        "title": "港口調查",
                        "meta": "Index excerpt only",
                        "content": "Longer index content",
                        "updated_on": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                        "is_banned": False,
                    },
                }
            ]
        },
    }


@pytest.fixture
def index():
    seen = []
    controls = {"wire": wire(), "status": 200, "type": "application/json", "pause": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, json.loads(body), dict(self.headers)))
            self.send_response(controls["status"])
            self.send_header("Content-Type", controls["type"])
            self.send_header("Location", "/forbidden-redirect")
            self.end_headers()
            payload = json.dumps(controls["wire"], ensure_ascii=False).encode()
            try:
                self.wfile.write(payload[:12])
                self.wfile.flush()
                time.sleep(controls["pause"])
                self.wfile.write(payload[12:])
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, seen, controls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_concrete_search_uses_exact_index_and_literal_query_with_native_provenance(index):
    selected = binding(index[0])
    search = AhmiaIndexSearch(selected)
    response = asyncio.run(search.request(request(limit=1)))
    path, body, headers = index[1][0]
    assert path == "/tor-fixture/_search" and body["size"] == 1
    assert body["query"]["bool"]["must"][0]["multi_match"]["query"] == request().query.text
    assert body["query"]["bool"]["filter"][0] == {"term": {"is_banned": False}}
    assert body["query"]["bool"]["must"][0]["multi_match"]["fields"] == ["title^2.0", "content^1.0"]
    assert "Authorization" not in headers
    assert search.identity == selected.identity
    evidence = response.hits[0].index_evidence
    assert evidence.index_name == selected.index_name and evidence.hit_id == "source-1"
    assert evidence.index_revision == selected.index_revision
    assert evidence.retrieval_score == 14.25  # deliberately not a probability
    assert evidence.retrieved_at == response.index_retrieved_at
    assert json.loads(response.raw) == index[2]["wire"]


@pytest.mark.parametrize(
    "fault",
    [
        "timeout",
        "partial",
        "banned",
        "wrong_index",
        "bad_onion",
        "bad_port",
        "stale",
        "future",
        "numeric_bool",
    ],
)
def test_bad_native_search_never_becomes_successful_empty_discovery(index, fault):
    packet = index[2]["wire"]
    hit = packet["hits"]["hits"][0]
    if fault == "timeout":
        packet["timed_out"] = True
    elif fault == "partial":
        packet["_shards"].update(successful=0, failed=1)
    elif fault == "banned":
        hit["_source"]["is_banned"] = True
    elif fault == "wrong_index":
        hit["_index"] = "another-index"
    elif fault == "bad_onion":
        hit["_source"]["url"] = "http://not-validated.onion/report"
    elif fault == "bad_port":
        hit["_source"]["url"] = f"http://{onion()}:broken/report"
    elif fault == "numeric_bool":
        packet["timed_out"] = 0
    else:
        delta = timedelta(days=1) * (1 if fault == "future" else -1)
        hit["_source"]["updated_on"] = (datetime.now(UTC) + delta).isoformat()
    with pytest.raises(FetchFailure, match="search_unavailable") as caught:
        asyncio.run(AhmiaIndexSearch(binding(index[0])).request(request()))
    assert caught.value.bytes_read > 0


@pytest.mark.parametrize("fault", ["redirect", "not_json", "oversized", "timeout"])
def test_http_refusals_retain_actual_bounded_spend_without_redirects(index, fault):
    selected = binding(index[0])
    call = request()
    if fault == "redirect":
        index[2]["status"] = 302
    elif fault == "not_json":
        index[2]["type"] = "text/html"
    elif fault == "oversized":
        call = request(max_bytes=20)
    else:
        index[2]["pause"] = 0.3
        call = request(timeout_seconds=0.05)
    with pytest.raises(FetchFailure) as caught:
        asyncio.run(AhmiaIndexSearch(selected).request(call))
    assert 0 < caught.value.bytes_read <= call.max_bytes
    assert len(index[1]) == 1


def test_credentials_are_exact_private_binding_inputs_and_dns_is_checked_before_contact(index):
    selected = binding(index[0], authorization="api_key")
    with pytest.raises(ValueError, match="credential"):
        AhmiaIndexSearch(selected)
    with pytest.raises(ValueError, match="HTTPS"):
        AhmiaIndexSearch(selected, credential=SecretStr("fixture-secret"))
    admitted = binding(index[0], authorization="api_key", allow_plaintext_credentials=True)
    asyncio.run(
        AhmiaIndexSearch(admitted, credential=SecretStr("fixture-secret")).request(request())
    )
    assert index[1][0][2]["Authorization"] == "ApiKey fixture-secret"
    assert "fixture-secret" not in admitted.model_dump_json()

    class WrongDNS:
        async def resolve(self, host, port):
            return ("127.0.0.1", "10.2.3.4")

    hostname = admitted.model_dump()
    hostname["endpoint"] = f"http://index.private:{index[0]}/tor-fixture/_search"
    with pytest.raises(FetchFailure):
        asyncio.run(
            AhmiaIndexSearch(
                AhmiaConfig.model_validate(hostname),
                credential=SecretStr("fixture-secret"),
                resolver=WrongDNS(),
            ).request(request())
        )
    assert len(index[1]) == 1


def test_cancelled_index_request_accounts_received_bytes_and_closes_transport(index):
    from ghimera.refusals import FetchCancelled

    index[2]["pause"] = 0.5

    async def scenario():
        task = asyncio.create_task(AhmiaIndexSearch(binding(index[0])).request(request()))
        for _ in range(100):
            if index[1]:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(FetchCancelled) as caught:
            await task
        assert caught.value.bytes_read == 12
        index[2]["pause"] = 0
        assert (await AhmiaIndexSearch(binding(index[0])).request(request())).hits

    asyncio.run(scenario())


def test_empty_index_call_remains_a_retained_budgeted_observation(index):
    index[2]["wire"]["hits"]["hits"] = []
    selected = discovery(
        provider("onions", binding=binding(index[0]), domains=["onion"]),
        target_domains=["onion"],
    )
    history, budget, ledger = state(selected, {"onions": AhmiaIndexSearch(binding(index[0]))})
    observations = asyncio.run(history.discover_many(request().query))
    assert observations[0].response.hits == ()
    assert observations[0].response.index_retrieved_at is not None
    assert budget.search_calls == budget.fetches == 1
    assert budget.bytes_read == len(observations[0].response.raw) == ledger.snapshot()[0].bytes_read


def test_application_mcp_payload_uses_existing_lead_envelope_and_preserves_index_provenance(index):
    payload = asyncio.run(AhmiaIndexSearch(binding(index[0])).tool_payload(request()))
    client = FixtureClient({"structuredContent": payload})
    response = asyncio.run(
        McpLeadSearch(recipe(tool_name="onion_search"), client).request(request())
    )
    assert response.hits[0].url == f"http://{onion()}/report"
    assert response.hits[0].index_evidence.hit_id == "source-1"
    assert client.calls[0][0] == "onion_search"


def test_application_payload_respects_byte_cap_even_when_native_index_response_fits(index):
    search = AhmiaIndexSearch(binding(index[0]))
    payload = asyncio.run(search.tool_payload(request()))
    native_bytes = len(json.dumps(index[2]["wire"], ensure_ascii=False).encode())
    assert len(json.dumps(payload, ensure_ascii=False).encode()) > native_bytes
    with pytest.raises(FetchFailure):
        asyncio.run(search.tool_payload(request(max_bytes=native_bytes)))


@pytest.mark.parametrize("multiple", [False, True])
def test_native_collector_fetches_onion_via_tor_and_archives_source_evidence(
    tmp_path, index, source_site, search_endpoint, model_endpoint, encoder_endpoint, multiple
):
    seen = []
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    native_url = f"http://{onion()}:{source_site[0]}/plain"
    index[2]["wire"] = wire(native_url)

    async def scenario():
        socks = await socks_server(source_site[0], seen)
        async with socks:
            values = cfg.model_dump()
            tp = policy(socks.sockets[0].getsockname()[1]).model_dump()
            tp["tor"]["allowed_ports"] = [source_site[0]]
            values.update(search=binding(index[0]), transport=tp)
            if multiple:
                values.update(
                    search=None,
                    discovery=discovery(
                        provider("onions", binding=binding(index[0]), domains=["onion"]),
                        target_domains=["onion"],
                    ),
                )
            concrete = GhimeraConfig.model_validate(values)
            collector = Collector(concrete, source_resolver=ResolverFixture())
            return await collector.run("find ports", run_id="ahmia-native")

    result = asyncio.run(scenario())
    assert result.status == "answered"
    assert result.harvest.documents[0].url == native_url
    assert result.answer.claims[0].citations[0].matches(result.harvest.documents[0])
    selected = result.harvest.receipt.effective_config
    assert (result.search_provider, result.search_revision) == (
        selected.discovery.identity if multiple else binding(index[0]).identity
    )
    assert result.search_observations[0].response.index_retrieved_at is not None
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result
    assert seen and all(row[1] == onion() for row in seen)
    assert not search_endpoint[1]
    assert all(headers.get("Authorization") is None for _, headers in source_site[3])
    archive_path = tmp_path / "ahmia-archive"
    archive = ResearchResultArchive.create(archive_path, run_id="ahmia-native")
    try:
        archive.write(result, max_bytes=1000000)
    finally:
        archive.close()
    assert ResearchResultArchive.read(archive_path, max_bytes=1000000) == result
    for fault in ("metadata", "raw", "identity", "legacy"):
        tampered = copy.deepcopy(result.model_dump())
        if fault == "metadata":
            tampered["search_observations"][0]["response"]["hits"][0]["index_evidence"][
                "hit_id"
            ] = "forged"
            # Re-seal the normalized response: native replay must reject the
            # changed hit rather than merely notice the old response digest.
            observation = tampered["search_observations"][0]
            tampered["harvest"]["ledger"][observation["sequence"]]["search_response_sha256"] = (
                SearchResponse.model_validate(observation["response"]).content_digest()
            )
        elif fault == "raw":
            tampered["search_observations"][0]["response"]["raw"] = b"{}"
        elif fault == "identity":
            tampered["search_revision"] = "another-index-recipe"
        else:
            tampered["schema"] = "chimera.research-result/1"
            tampered["search_observations"] = ()
        with pytest.raises(
            ValidationError, match="native index response" if fault == "metadata" else None
        ):
            ResearchResult.model_validate(tampered)


def test_invalid_endpoint_recipe_refuses_and_example_is_inert():
    for updates in (
        {"endpoint": "https://index.private/tor-fixture/_delete_by_query"},
        {"index_name": "*"},
        {"age_policy": "all_observed"},
        {"approved_addresses": ["8.8.8.8"]},
    ):
        with pytest.raises(ValidationError):
            binding(12345, **updates)
    values = tomllib.loads(Path("examples/ahmia.toml").read_text())["search"]
    selected = AhmiaConfig.model_validate(values)
    assert selected.endpoint.startswith("https://index.private.invalid/")


def test_private_post_rejects_nonfinite_timeout_and_excess_request_before_io(index):
    client = PinnedJsonHttp(binding(index[0], max_request_bytes=1))
    for timeout in (float("nan"), float("inf"), 0):
        with pytest.raises(ValueError):
            asyncio.run(client.post(b"{}", timeout_seconds=timeout))
    with pytest.raises(JsonWireFailure) as caught:
        asyncio.run(client.post(b"{}"))
    assert caught.value.response.body == b"" and not index[1]


def test_decoder_retains_empty_native_result_but_rejects_missing_success_shape(index):
    packet = wire()
    packet["hits"]["hits"] = []
    assert (
        decode_ahmia(
            json.dumps(packet).encode(), binding(index[0]), limit=2, retrieved_at=datetime.now(UTC)
        )
        == ()
    )
    packet.pop("_shards")
    with pytest.raises(ValidationError):
        decode_ahmia(
            json.dumps(packet).encode(), binding(index[0]), limit=2, retrieved_at=datetime.now(UTC)
        )
