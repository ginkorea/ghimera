"""Configured real HTTP/search/HTML/embedding/chat adapters, not LLM accuracy."""

import asyncio
import json
import tomllib
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from ghimera import Collector
from ghimera.config import GhimeraConfig
from ghimera.graph import DirectoryGraphSink
from ghimera.journal import read_journal
from ghimera.models import Goal, Harvest, Scope
from ghimera.refusals import GhimeraRefused
from ghimera.research_types import ResearchRequest, ResearchResult
from ghimera.search_config import SearxConfig
from ghimera.searxng import SearxSearch
from tests.test_embedding_scoring import endpoint as encoder_endpoint
from tests.test_embedding_scoring import intent_policy
from tests.test_embedding_scoring import service as encoder_service
from tests.test_html_extraction import ARTICLE
from tests.test_html_extraction import policy as extraction_policy
from tests.test_http_fetch import ResolverFixture
from tests.test_http_fetch import site as source_site
from tests.test_http_fetch import state as source_state
from tests.test_intent_research import policy as research_policy
from tests.test_search_conformance import endpoint as search_endpoint
from tests.test_served_models import endpoint as model_endpoint
from tests.test_served_models import service as model_service

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, **updates):
    source_port, search_port = source_site[0], search_endpoint[0]
    source_url = f"http://fixture.example:{source_port}/plain"
    source_site[4]["/plain"] = ARTICLE.encode()
    search_endpoint[2]["body"] = json.dumps(
        {
            "results": [
                {
                    "url": source_url,
                    "title": "Port infrastructure report",
                    "content": "discovery only",
                },
            ]
        }
    ).encode()
    raw = source_state(source_site)[2].config.model_dump()
    raw["http"]["network"]["fixture_ports"] = [source_port, search_port]
    raw.update(
        research=research_policy(allowed_ports=[source_port], max_model_input_chars=20000),
        search=SearxConfig(
            schema="chimera.searxng/1",
            endpoint=f"http://fixture.example:{search_port}/search",
            language="all",
            safe_search=1,
            time_range="",
        ),
        models={
            "schema": "chimera.model-bindings/1",
            "planner": model_service(model_endpoint[0]),
            "analyst": model_service(model_endpoint[0]),
            "reviewer": model_service(model_endpoint[0], model_id="protocol-reviewer"),
            "judge": model_service(model_endpoint[0]),
        },
        extraction=extraction_policy(tmp_path),
        scoring=intent_policy(encoder_service(encoder_endpoint[0]), max_windows=1),
    )
    raw.update(updates)
    return GhimeraConfig.model_validate(raw), source_url


def test_configured_collector_completes_real_adapter_chain_and_keeps_native_evidence(
    tmp_path,
    source_site,
    search_endpoint,
    model_endpoint,
    encoder_endpoint,
):
    cfg, url = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    collector = Collector(cfg, source_resolver=ResolverFixture())
    assert (
        not source_site[1]
        and not search_endpoint[1]
        and not model_endpoint[1]
        and not encoder_endpoint[1]
    )
    result = asyncio.run(collector.run("find ports"))
    assert result.status == "answered"
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result
    doc = result.harvest.documents[0]
    assert doc.url == url and doc.raw == ARTICLE.encode()
    assert doc.extracted.title == "Port infrastructure report"
    assert doc.extracted.byline == "Research Office" and doc.extracted.date == "2026-10-06"
    assert "Taiwan" in doc.extracted.text and "42" in doc.extracted.text
    assert doc.extracted.extraction.parser_revision.startswith("scrapling@0.4.2+crawl4ai@0.9.4")
    assert result.answer.claims[0].citations[0].matches(doc)
    assert collector.config == result.harvest.receipt.effective_config == cfg
    assert result.harvest.receipt.effective_config.search == cfg.search
    assert source_site[1]["/plain"] == 1 and len(search_endpoint[1]) == 1
    assert result.harvest.receipt.judge_calls == len(model_endpoint[1]) == 5
    assert result.harvest.receipt.encoding_calls == len(encoder_endpoint[1])
    assert next(
        row for row in result.harvest.ledger if row.intent_reference
    ).intent_reference.goal_sha256
    assert all(headers.get("Authorization") is None for _, headers in source_site[3])


def test_same_configured_collector_gets_fresh_goal_state_and_charges_each_run(
    tmp_path,
    source_site,
    search_endpoint,
    model_endpoint,
    encoder_endpoint,
):
    cfg, url = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    collector = Collector(cfg, source_resolver=ResolverFixture())
    scope = Scope(
        allowed_hosts=("fixture.example",),
        allowed_ports=(source_site[0],),
        max_depth=0,
        content_types=("text/html",),
    )

    async def collect_twice():
        first = await collector.collect(Goal(text="ports", seeds=(url,)), scope)
        second = await collector.collect(Goal(text="ports", seeds=(url,)), scope)
        return first, second

    first, second = asyncio.run(collect_twice())
    assert len(first.documents) == len(second.documents) == 1
    assert first.receipt.encoding_calls == second.receipt.encoding_calls
    assert len(encoder_endpoint[1]) == first.receipt.encoding_calls + second.receipt.encoding_calls
    assert source_site[1]["/plain"] == 2 and not search_endpoint[1]
    assert Harvest.model_validate_json(second.model_dump_json()) == second


def test_configured_html_search_runs_the_full_concrete_collector_chain(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, url = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    values = cfg.model_dump(by_alias=True)
    values["search"].update(schema="chimera.searxng/2", response_format="html")
    cfg = GhimeraConfig.model_validate(values)
    search_endpoint[2].update(
        content_type="text/html",
        body=(
            '<html><div id="results"><div id="urls"><article class="result result-default">'
            f'<h3><a href="{url}">Port infrastructure report</a></h3>'
            '<p class="content">Discovery only.</p></article></div></div></html>'
        ).encode(),
    )
    result = asyncio.run(Collector(cfg, source_resolver=ResolverFixture()).run("find ports"))
    assert result.status == "answered" and result.search_revision == "search-html/1"
    assert result.harvest.receipt.effective_config == cfg
    assert result.search_observations[0].response.raw == search_endpoint[2]["body"]
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result
    doc = result.harvest.documents[0]
    assert doc.url == url and doc.raw == ARTICLE.encode()
    assert result.answer.claims[0].citations[0].matches(doc)
    assert len(search_endpoint[1]) == source_site[1]["/plain"] == 1


def test_configuration_alone_assembles_durable_graph_and_journal(
    tmp_path,
    source_site,
    search_endpoint,
    model_endpoint,
    encoder_endpoint,
):
    graph = tomllib.loads(Path("examples/research-graph.toml").read_text())
    graph["sink_path"] = str(tmp_path / "graph")
    journal = {
        "schema": "chimera.run-journal-config/1",
        "directory": str(tmp_path / "journal"),
        "max_record_bytes": 1000000,
        "max_journal_bytes": 10000000,
        "max_summary_bytes": 1000000,
        "max_records": 1000,
    }
    cfg, _ = assembled(
        tmp_path,
        source_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        graph=graph,
        journal=journal,
    )
    collector = Collector(cfg, source_resolver=ResolverFixture())
    assert not cfg.graph.sink_path.exists() and not cfg.journal.directory.exists()
    result = asyncio.run(collector.run("find ports", run_id="configured-research"))
    assert result.status == "answered" and result.harvest.graph is not None
    roles = {node.role for node in result.harvest.graph.nodes}
    assert {"intent", "question", "query", "source", "document"} <= roles
    batches = asyncio.run(DirectoryGraphSink(cfg.graph, "configured-research").replay())
    assert {node.id for batch in batches for node in batch.nodes} == {
        node.id for node in result.harvest.graph.nodes
    }
    report = read_journal(cfg.journal, "configured-research")
    assert report.state == "complete" and report.rows == result.harvest.ledger
    assert report.summary.receipt == result.harvest.receipt
    assert report.header.config.search == cfg.search
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result


def test_missing_recipes_or_mime_adapter_refuse_before_any_outbound_work(
    tmp_path,
    source_site,
    search_endpoint,
    model_endpoint,
    encoder_endpoint,
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    for section in ("http", "research", "search", "models", "scoring", "extraction"):
        raw = cfg.model_dump()
        raw[section] = None
        with pytest.raises(ValueError, match="requires http"):
            Collector(GhimeraConfig.model_validate(raw), source_resolver=ResolverFixture())
    raw = cfg.model_dump()
    raw["research"]["content_types"] = ("application/pdf",)
    with pytest.raises(ValueError, match="extraction adapters"):
        Collector(GhimeraConfig.model_validate(raw), source_resolver=ResolverFixture())
    collector = Collector(cfg, source_resolver=ResolverFixture())
    with pytest.raises(ValueError, match="extraction adapters"):
        asyncio.run(
            collector.collect(
                Goal(text="ports"),
                Scope(
                    allowed_hosts=("example.org",), max_depth=0, content_types=("application/pdf",)
                ),
            )
        )
    assert (
        not source_site[1]
        and not search_endpoint[1]
        and not model_endpoint[1]
        and not encoder_endpoint[1]
    )


def test_oversized_intent_and_poisoned_request_refuse_before_plan_or_storage(
    tmp_path,
    source_site,
    search_endpoint,
    model_endpoint,
    encoder_endpoint,
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    collector = Collector(cfg, source_resolver=ResolverFixture())
    with pytest.raises(GhimeraRefused, match="budget_exhausted"):
        asyncio.run(collector.run("ports" * 100))
    with pytest.raises(ValidationError):
        asyncio.run(
            collector.run(ResearchRequest(intent="ports").model_copy(update={"intent": ""}))
        )
    assert (
        not source_site[1]
        and not search_endpoint[1]
        and not model_endpoint[1]
        and not encoder_endpoint[1]
    )
    assert not cfg.extraction.work_directory.exists()


def test_search_recipe_cannot_be_rebound_and_old_configuration_shape_is_unchanged(
    tmp_path,
    source_site,
    search_endpoint,
    model_endpoint,
    encoder_endpoint,
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    with pytest.raises(ValueError, match="effective search recipe"):
        SearxSearch(
            cfg, cfg.search.model_copy(update={"language": "another"}), resolver=ResolverFixture()
        )
    assert "search" not in GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump()
    assert GhimeraConfig.model_validate_json(cfg.model_dump_json()) == cfg
    assert not search_endpoint[1]


def test_unbound_model_and_encoder_credentials_refuse_before_source_requests(
    tmp_path,
    source_site,
    search_endpoint,
    model_endpoint,
    encoder_endpoint,
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    with pytest.raises(ValueError, match="configured model-service endpoints"):
        Collector(
            cfg,
            model_credentials={
                "https://wrong.invalid/v1/chat/completions": SecretStr("fixture-only")
            },
        )
    with pytest.raises(ValueError, match="authorization mode"):
        Collector(cfg, encoder_credential=SecretStr("fixture-only"))
    assert (
        not source_site[1]
        and not search_endpoint[1]
        and not model_endpoint[1]
        and not encoder_endpoint[1]
    )


def test_complete_nonactive_toml_template_and_explicit_parse_bounds(tmp_path):
    path = Path("examples/collector.toml")
    cfg = GhimeraConfig.from_toml(path, max_bytes=100000)
    assert cfg.search and cfg.models and cfg.extraction and cfg.scoring.reference_source == "intent"
    assert cfg.research.content_types == ("text/html", "application/xhtml+xml")
    collector = Collector.from_toml(path, max_config_bytes=100000)
    assert collector.config == cfg
    for allowance in (0, -1, True, path.stat().st_size - 1):
        with pytest.raises(ValueError, match="allowance"):
            Collector.from_toml(path, max_config_bytes=allowance)
