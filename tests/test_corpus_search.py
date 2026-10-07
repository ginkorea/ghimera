"""Real retained-corpus discovery protocol; fixtures do not establish model quality."""

import asyncio
import json
import threading
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera import Collector, CorpusLeadSearch, CorpusSearchConfig
from ghimera.corpus_search_wire import CorpusSearchWire, corpus_source_url
from ghimera.corpus_storage import CorpusStorage
from ghimera.corpus_types import BoundCorpusDocument
from ghimera.discovery_config import DiscoveryConfig
from ghimera.refusals import GhimeraRefused
from ghimera.research_types import ResearchResult, SearchQuery, SearchRequest
from tests.test_collector import (
    assembled,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)
from tests.test_evidence_corpus import config, corpus, harvest
from tests.test_http_fetch import ResolverFixture

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def binding(store, **updates):
    raw = dict(
        schema="ghimera.corpus-search/1",
        corpus_id=store.identity,
        corpus_config_sha256=store.config.identity,
        query_encoder=store.config.query_encoder,
        max_query_chars=store.config.max_query_chars,
        max_passage_hits=store.config.max_top_k,
        max_results=5,
        max_original_bytes=100000,
        max_response_bytes=200000,
        max_title_chars=100,
        max_snippet_chars=100,
        minimum_cosine=store.config.minimum_cosine,
        languages=(),
    )
    raw.update(updates)
    return CorpusSearchConfig.model_validate(raw)


def request(**updates):
    raw = dict(
        query=SearchQuery(text="ports", question_ids=("q1",)),
        limit=5,
        max_bytes=200000,
        timeout_seconds=10.0,
    )
    raw.update(updates)
    return SearchRequest.model_validate(raw)


def test_native_query_originals_and_leads_survive_fresh_decoder(tmp_path, encoder_endpoint):
    async def operation():
        store = corpus(config(tmp_path, encoder_endpoint[0]), create=True)
        try:
            material = await harvest(("zh", "港口基礎設施研究"), ("en", "unrelated schools"))
            await store.append(material)
            policy = binding(store)
            response = await CorpusLeadSearch(policy, store).request(request())
            wire = CorpusSearchWire.model_validate_json(response.raw)
            wire.validate_policy(policy, "ports")
            assert wire.sources == (material.documents[0],)
            assert wire.query.hits[0].passage.text == response.hits[0].snippet == "港口基礎設施研究"
            assert response.hits[0].url == material.documents[0].url
            assert wire.query.encoding_call.service == store.config.query_encoder
            assert len(encoder_endpoint[1]) == 2
            assert response.transport is None and response.index_retrieved_at is None
            assert not wire.omissions
            assert CorpusSearchWire.model_validate_json(wire.model_dump_json()) == wire
        finally:
            store.close()

    asyncio.run(operation())


def test_same_source_passages_become_one_native_lead(tmp_path, encoder_endpoint):
    async def operation():
        store = corpus(
            config(tmp_path, encoder_endpoint[0], chunk_chars=8, overlap_chars=2), create=True
        )
        try:
            await store.append(
                await harvest(("zh", "港口基礎設施研究港口基礎設施研究港口基礎設施研究"))
            )
            response = await CorpusLeadSearch(binding(store), store).request(request())
            wire = CorpusSearchWire.model_validate_json(response.raw)
            assert len(wire.query.hits) > 1
            assert len(response.hits) == len(wire.sources) == len(wire.selected_passages) == 1
            assert "same_source" in wire.omissions
        finally:
            store.close()

    asyncio.run(operation())


@pytest.mark.parametrize(
    "changes",
    [
        {"corpus_id": "0" * 32},
        {"corpus_config_sha256": "0" * 64},
        {"max_passage_hits": 11},
        {"minimum_cosine": 0.0},
    ],
)
def test_wrong_corpus_or_bounds_refuse_before_encoding(tmp_path, encoder_endpoint, changes):
    store = corpus(config(tmp_path, encoder_endpoint[0]), create=True)
    try:
        with pytest.raises(ValueError, match="exact admitted"):
            CorpusLeadSearch(binding(store, **changes), store)
        assert not encoder_endpoint[1]
    finally:
        store.close()


def test_closed_corpus_refuses_before_encoding(tmp_path, encoder_endpoint):
    store = corpus(config(tmp_path, encoder_endpoint[0]), create=True)
    search = CorpusLeadSearch(binding(store), store)
    store.close()
    with pytest.raises(GhimeraRefused):
        asyncio.run(search.request(request()))
    assert not encoder_endpoint[1]


def test_language_filter_keeps_an_empty_observed_query(tmp_path, encoder_endpoint):
    async def operation():
        store = corpus(config(tmp_path, encoder_endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究")))
            response = await CorpusLeadSearch(binding(store, languages=("ja",)), store).request(
                request()
            )
            wire = CorpusSearchWire.model_validate_json(response.raw)
            assert not response.hits and not wire.sources and not wire.query.hits
            assert wire.query.encoding_call.service == store.config.query_encoder
        finally:
            store.close()

    asyncio.run(operation())


def test_original_size_omission_and_query_budget_are_explicit(tmp_path, encoder_endpoint):
    async def operation():
        store = corpus(config(tmp_path, encoder_endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究")))
            response = await CorpusLeadSearch(binding(store, max_original_bytes=1), store).request(
                request()
            )
            wire = CorpusSearchWire.model_validate_json(response.raw)
            assert wire.query.hits and not wire.sources and not response.hits
            assert wire.omissions == ("original_bytes",)
            with pytest.raises(GhimeraRefused):
                await CorpusLeadSearch(binding(store), store).request(request(max_bytes=1))
            assert len(encoder_endpoint[1]) == 3  # Append and both admitted queries.
        finally:
            store.close()

    asyncio.run(operation())


@pytest.mark.parametrize(
    "url",
    [
        "file:///a",
        "https://user:secret@example.org/a",
        "https://example.org:broken/a",
        "https://[broken/a",
        "https://example.org/a\nb",
        "https://example.org/\x7f",
    ],
)
def test_invalid_original_lead_urls_are_not_model_repaired(url):
    assert not corpus_source_url(url)


def test_corpus_leads_bind_native_sources_and_selected_passages(tmp_path, encoder_endpoint):
    async def operation():
        store = corpus(config(tmp_path, encoder_endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究")))
            response = await CorpusLeadSearch(binding(store), store).request(request())
            original = json.loads(response.raw)
            for change in ("text", "url", "selection", "query", "omission"):
                changed = json.loads(response.raw)
                if change == "text":
                    changed["sources"][0]["extracted"]["text"] = "unsupported text"
                elif change == "url":
                    changed["sources"][0]["url"] = "https://invented.example/a"
                elif change == "selection":
                    changed["selected_passages"] = [999999]
                elif change == "query":
                    changed["query_text"] = "other query"
                else:
                    changed["omissions"] = ["made_up"]
                with pytest.raises(ValidationError):
                    CorpusSearchWire.model_validate(changed)
            wire = CorpusSearchWire.model_validate(original)
            with pytest.raises(ValueError, match="configured"):
                wire.validate_policy(
                    binding(store, minimum_cosine=1.0, max_response_bytes=1), "ports"
                )
        finally:
            store.close()

    asyncio.run(operation())


def test_configured_research_uses_corpus_leads_then_fetches_original_source(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, url = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)

    async def operation():
        first = await Collector(cfg, source_resolver=ResolverFixture()).run("find ports")
        store = corpus(config(tmp_path, encoder_endpoint[0]), create=True)
        try:
            await store.append(first.harvest)
            policy = binding(store)
            next_cfg = type(cfg).model_validate(cfg.model_dump() | {"search": policy})
            collector = Collector(next_cfg, corpus=store, source_resolver=ResolverFixture())
            second = await collector.run("find ports")
            assert second.status == "answered"
            assert second.harvest.documents[0].url == url
            assert source_site[1]["/plain"] == 2  # Leads are not cached-source answer reuse.
            assert len(search_endpoint[1]) == 1
            assert second.search_observations[0].provider == "evidence-corpus"
            assert ResearchResult.model_validate_json(second.model_dump_json()) == second
            changed = second.model_dump()
            changed["search_observations"][0]["response"]["hits"][0]["snippet"] = "invented"
            with pytest.raises(ValidationError):
                ResearchResult.model_validate(changed)
            with pytest.raises(ValueError, match="corpus"):
                Collector(next_cfg, source_resolver=ResolverFixture())
        finally:
            store.close()

    asyncio.run(operation())


def test_strategy_composes_exact_named_corpus_and_preserves_archive(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)

    async def operation():
        first = await Collector(cfg, source_resolver=ResolverFixture()).run("find ports")
        store = corpus(config(tmp_path, encoder_endpoint[0]), create=True)
        try:
            await store.append(first.harvest)
            strategy = DiscoveryConfig(
                schema="ghimera.discovery/1",
                providers=(
                    dict(
                        id="retained",
                        domains=("open_web",),
                        binding=binding(store),
                        query_disclosure="planned_query",
                        use_contract="crawl_and_retain",
                        max_calls=10,
                        byte_budget=1000000,
                        call_seconds_budget=100.0,
                        max_response_bytes=200000,
                        max_results=5,
                        timeout_seconds=10.0,
                        consecutive_failure_limit=2,
                    ),
                ),
                target_domains=("open_web",),
                allow_cross_domain_expansion=False,
                mode="ordered_fallback",
                cold_start_fanout=False,
                provider_concurrency=1,
                stagnation_window=2,
                min_new_documents=1,
                min_new_answers=1,
                max_strategy_changes=1,
            )
            next_cfg = type(cfg).model_validate(
                cfg.model_dump() | {"search": None, "discovery": strategy}
            )
            for stores in (None, {}, {"incorrect": store}):
                with pytest.raises(ValueError, match="corpus"):
                    Collector(next_cfg, discovery_corpora=stores, source_resolver=ResolverFixture())
            collector = Collector(
                next_cfg, discovery_corpora={"retained": store}, source_resolver=ResolverFixture()
            )
            result = await collector.run("find ports")
            assert result.status == "answered"
            assert result.search_observations[0].provider == "binding:retained"
            assert ResearchResult.model_validate_json(result.model_dump_json()) == result
        finally:
            store.close()

    asyncio.run(operation())


def test_owned_original_read_drains_repeated_cancellation(tmp_path, encoder_endpoint, monkeypatch):
    async def operation():
        store = corpus(config(tmp_path, encoder_endpoint[0]), create=True)
        try:
            material = await harvest(("zh", "港口研究"))
            receipt = await store.append(material)
            started, release = threading.Event(), threading.Event()
            original = CorpusStorage.document

            def slow_read(self, document_id):
                started.set()
                if not release.wait(5):
                    raise ValueError("fixture worker deadline")
                return original(self, document_id)

            monkeypatch.setattr(CorpusStorage, "document", slow_read)
            task = asyncio.create_task(
                store.documents((BoundCorpusDocument(material.documents[0]).identity,))
            )
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            assert not task.done()
            with pytest.raises(ValueError, match="active"):
                store.close()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert store.identity == receipt.corpus_id
        finally:
            store.close()

    asyncio.run(operation())


def test_non_active_example_parses_without_creating_a_store():
    policy = CorpusSearchConfig.model_validate(
        tomllib.loads(Path("examples/corpus-search.toml").read_text(encoding="utf-8"))
    )
    assert policy.corpus_id == "0" * 32
    assert policy.identity[0] == "evidence-corpus"
