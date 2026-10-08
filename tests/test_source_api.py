"""Native site mappings replay bytes and feed the existing guarded collection frontier."""

import asyncio
import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera import Collector
from ghimera.graph import DirectoryGraphSink
from ghimera.journal import read_journal
from ghimera.models import Goal, Harvest, Page, Scope
from ghimera.source_feed_parse import parse_feed
from ghimera.source_feed_types import SourceFeedEvidence
from ghimera.source_feeds import SourceFeedExtractor
from tests.test_collector import assembled
from tests.test_http_fetch import ResolverFixture
from tests.test_source_feeds import (
    config,
    encoder_endpoint,
    feed_site,
    model_endpoint,
    policy,
    search_endpoint,
)

__all__ = ["feed_site", "encoder_endpoint", "search_endpoint", "model_endpoint"]


def api_policy(tmp_path, **updates):
    def mapping(relation, path):
        return dict(
            relation=relation,
            entries_path=path,
            url_path=["url"],
            title_path=["title"],
            content_path=["abstract"],
            date_path=["date"],
            id_path=["id"],
            url_template=None,
        )

    api = dict(
        schema="ghimera.site-api/1",
        source_origins=["https://source.example"],
        title="Native citation API",
        language="zh-Hant",
        max_json_nodes=1000,
        max_json_depth=10,
        mappings=[
            mapping("result", ["results"]),
            mapping("references", ["references"]),
            mapping("cited_by", ["cited_by"]),
            mapping("pagination", ["next"]),
        ],
    )
    return policy(
        tmp_path, formats=["json_api"], content_types=["application/json"], site_api=api, **updates
    )


def body():
    return json.dumps(
        dict(
            results=[dict(url="/paper", title="原始研究", abstract="港口研究")],
            references=[dict(url="/reference", title="引用來源")],
            cited_by=[dict(url="/citing", title="引用此文")],
            next=dict(url="/api?page=2", title="Next page"),
        ),
        ensure_ascii=False,
    ).encode()


def test_citation_and_cited_by_declarations_keep_native_json_locators(tmp_path):
    selected = api_policy(tmp_path)
    result = parse_feed(body(), "https://source.example/api", "application/json", selected)
    result.validate_source(body())
    assert [entry.api_relation for entry in result.entries] == [
        "result",
        "references",
        "cited_by",
        "pagination",
    ]
    assert result.entries[1].api_locator == "/references/0/url"
    assert result.entries[-1].api_locator == "/next/url"
    assert result.entries[-1].role == "feed"
    assert result.links[2][0] == "https://source.example/citing"
    assert "港口研究" in result.text
    assert SourceFeedEvidence.model_validate_json(result.model_dump_json()) == result
    changed = result.model_dump()
    changed["entries"][0]["api_locator"] = "/fabricated/url"
    with pytest.raises(ValueError):
        SourceFeedEvidence.model_validate(changed).validate_source(body())


@pytest.mark.parametrize(
    "raw",
    [
        b'{"results":[],"results":[]}',
        b'{"results":[NaN]}',
        b'{"results":[{"url": {"secret":"not a URL"}}]}',
    ],
)
def test_ambiguous_or_malformed_api_data_refuses(tmp_path, raw):
    with pytest.raises(ValueError):
        parse_feed(raw, "https://source.example/api", "application/json", api_policy(tmp_path))


def test_exact_api_origin_and_entry_limits_refuse(tmp_path):
    with pytest.raises(ValueError):
        parse_feed(
            body(), "https://different.example/api", "application/json", api_policy(tmp_path)
        )
    with pytest.raises(ValueError):
        parse_feed(
            body(),
            "https://source.example/api",
            "application/json",
            api_policy(tmp_path, max_entries=1, max_links=1),
        )
    with pytest.raises(ValidationError):
        policy(tmp_path, formats=["json_api"], content_types=["application/json"])


def test_actual_existing_passive_worker_extracts_the_api_for_native_frontier(tmp_path):
    selected = api_policy(tmp_path)
    cfg = config(tmp_path, source_feeds=selected)
    extractor = SourceFeedExtractor(cfg)
    page = Page(
        url="https://source.example/api",
        final_url="https://source.example/api",
        status=200,
        content_type="application/json",
        body=body(),
    )
    extracted = asyncio.run(extractor.extract(page))
    assert extracted.source_feed.format == "json_api"
    assert extracted.links[2].url == "https://source.example/citing"
    assert extracted.links[-1].url == "https://source.example/api?page=2"
    extracted.source_feed.validate_source(page.body)


def test_existing_collector_fetches_api_references_cited_by_and_pagination_with_source_replay(
    tmp_path, feed_site, encoder_endpoint, search_endpoint, model_endpoint
):
    origin = f"http://fixture.example:{feed_site[0]}"
    selected = api_policy(tmp_path).model_dump()
    selected["site_api"]["source_origins"] = [origin]
    selected = type(api_policy(tmp_path)).model_validate(selected)
    raw = body().replace(b"/paper", b"/plain")
    feed_site[4]["/api"] = raw
    feed_site[5]["/api"] = "application/json"
    for path in ("/reference", "/citing", "/api?page=2"):
        feed_site[4][path] = feed_site[4]["/plain"]
    graph = tomllib.loads(Path("examples/research-graph.toml").read_text())
    graph["sink_path"] = str(tmp_path / "graph")
    cfg, _ = assembled(
        tmp_path,
        feed_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        source_feeds=selected,
        min_link_score=0.0,
        grade_interval=100,
        graph=graph,
        journal=dict(
            schema="chimera.run-journal-config/1",
            directory=str(tmp_path / "journal"),
            max_record_bytes=1_000_000,
            max_journal_bytes=10_000_000,
            max_summary_bytes=1_000_000,
            max_records=1000,
        ),
    )
    result = asyncio.run(
        Collector(cfg, source_resolver=ResolverFixture()).collect(
            Goal(text="find ports", seeds=(origin + "/api",)),
            Scope(
                allowed_hosts=("fixture.example",),
                allowed_ports=(feed_site[0],),
                max_depth=1,
                content_types=("application/json", "text/html"),
            ),
            run_id="site-api-frontier",
        )
    )
    assert {doc.url for doc in result.source_documents} == {
        origin + path for path in ("/api", "/plain", "/reference", "/citing", "/api?page=2")
    }
    document = next(doc for doc in result.documents if doc.url == origin + "/api")
    document.extracted.source_feed.validate_source(document.raw)
    assert document.raw == raw
    assert document.extracted.source_feed.entries[2].api_relation == "cited_by"
    assert all(
        feed_site[1][path] == 1
        for path in ("/api", "/plain", "/reference", "/citing", "/api?page=2")
    )
    assert Harvest.model_validate_json(result.model_dump_json()) == result
    assert read_journal(cfg.journal, "site-api-frontier").rows == result.ledger
    assert asyncio.run(DirectoryGraphSink(cfg.graph, "site-api-frontier").replay())
