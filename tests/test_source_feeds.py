"""Passive native manifests and the existing scoped collector, not model accuracy."""

import asyncio
import hashlib
import sys
import threading
import tomllib
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera import Collector, PersistentCollection, PersistentCollector
from ghimera.config import GhimeraConfig
from ghimera.graph import DirectoryGraphSink
from ghimera.journal import read_journal
from ghimera.models import Document, Goal, Harvest, Page, Scope, Verdict
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.source_feed_config import SourceFeedConfig
from ghimera.source_feed_parse import parse_feed
from ghimera.source_feeds import SourceFeedExtractor
from tests.test_collector import assembled
from tests.test_embedding_scoring import endpoint as encoder_endpoint
from tests.test_evidence_corpus import config as corpus_policy
from tests.test_evidence_corpus import corpus
from tests.test_html_extraction import ARTICLE
from tests.test_http_fetch import ResolverFixture
from tests.test_http_fetch import state as fetch_state
from tests.test_search_conformance import endpoint as search_endpoint
from tests.test_served_models import endpoint as model_endpoint

__all__ = ["encoder_endpoint", "search_endpoint", "model_endpoint"]
BASE = "https://source.example/root/feed"
RSS = b"""<rss version="2.0"><channel><title>Ports feed</title><language>zh-CN</language>
<item><title>Port infrastructure report</title><link>/plain</link>
<description>Native port construction declaration</description>
<guid isPermaLink="false">item-1</guid>
<pubDate>2026-10-08</pubDate><enclosure url="/report.pdf" type="application/pdf" length="1"/>
</item></channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom" xml:base="/data/" xml:lang="zh-Hans">
<title type="text">港口 &lt;原文&gt;</title><entry xml:base="reports/"><id>native-id</id>
<title>港口报告</title><summary type="html">&lt;b&gt;原始公告&lt;/b&gt;</summary>
<link href="port"/><link rel="self" href="manifest"/>
<link rel="enclosure" href="attachment.pdf"/></entry></feed>""".encode()
SITEMAP = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://source.example/plain</loc><lastmod>2026-10-08</lastmod></url></urlset>"""
INDEX = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>/child.xml</loc></sitemap></sitemapindex>"""
JSON_FEED = """{"version":"https://jsonfeed.org/version/1.1","title":"港口消息",
"language":"zh-Hans","items":[{"id":"post-1","url":"/plain","title":"港口报告",
"content_text":"原始港口公告","date_published":"2026-10-08",
"attachments":[{"url":"/report.pdf","mime_type":"application/pdf"}]}]}""".encode()


def policy(tmp_path, **updates):
    raw = tomllib.loads(Path("examples/source-feeds.toml").read_text())
    raw.update(worker_python=sys.executable, work_directory=str(tmp_path / "feed-worker"))
    raw.update(updates)
    return SourceFeedConfig.model_validate(raw)


def config(tmp_path, **updates):
    raw = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump()
    raw.update(source_feeds=policy(tmp_path))
    raw.update(updates)
    return GhimeraConfig.model_validate(raw)


@pytest.mark.parametrize(
    "mime,raw,fmt,target",
    (
        ("application/rss+xml", RSS, "rss", "https://source.example/plain"),
        ("application/atom+xml", ATOM, "atom", "https://source.example/data/reports/port"),
        ("application/xml", SITEMAP, "sitemap", "https://source.example/plain"),
        ("application/xml", INDEX, "sitemap", "https://source.example/child.xml"),
        ("application/feed+json", JSON_FEED, "json_feed", "https://source.example/plain"),
    ),
)
def test_native_manifest_dialects_keep_exact_source_and_declarations(
    tmp_path, mime, raw, fmt, target
):
    result = parse_feed(raw, BASE, mime, policy(tmp_path))
    result.validate_source(raw)
    assert result.format == fmt and result.links[0][0] == target
    assert result.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert len(result.entries) == 1
    assert result.omitted_links == 0
    assert result.entries[0].role == ("feed" if raw == INDEX else "document")
    if fmt == "atom":
        assert result.title == "港口 <原文>" and result.entries[0].content == "原始公告"
        assert result.declared_language == "zh-Hans"
    elif fmt == "rss":
        assert result.entries[0].declared_id == "item-1"
        assert result.entries[0].declared_date == "2026-10-08"


@pytest.mark.parametrize(
    "raw,mime",
    (
        (RSS, "application/rss+xml"),
        (ATOM, "application/atom+xml"),
        (JSON_FEED, "application/feed+json"),
    ),
)
def test_enclosures_are_explicit_and_never_contacted_by_the_parser(tmp_path, raw, mime):
    ordinary = parse_feed(raw, BASE, mime, policy(tmp_path))
    enabled = parse_feed(raw, BASE, mime, policy(tmp_path, include_attachments=True))
    assert len(ordinary.links) == 1 and len(enabled.links) == 2
    enabled.validate_source(raw)


def test_permalink_ids_url_safety_and_link_omissions_are_observable(tmp_path):
    raw = b"""<rss version="2.0"><channel><title>Ports</title>
    <item><title>A</title><guid>https://source.example/a</guid></item>
    <item><title>B</title><guid isPermaLink="false">https://source.example/b</guid></item>
    <item><title>C</title><link>javascript:alert(1)</link></item>
    <item><title>D</title><link>https://name:secret@source.example/private</link></item>
    <item><title>A duplicate</title><link>https://source.example/a</link></item>
    <item><title>Other</title><link>/other</link></item></channel></rss>"""
    result = parse_feed(raw, BASE, "application/rss+xml", policy(tmp_path, max_links=1))
    assert result.links == (("https://source.example/a", "A"),)
    assert result.omitted_links == 1 and len(result.entries) == 6
    assert result.entries[1].observed_url == ""
    assert result.entries[2].url is result.entries[3].url is None
    result.validate_source(raw)


@pytest.mark.parametrize(
    "raw",
    (
        b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss version="2.0"/>',
        b'<!DOCTYPE rss [<!ENTITY x "payload">]><rss version="2.0">'
        b"<channel><title>&x;</title></channel></rss>",
        b'<rss version="2.0"><channel><title>bad',
        b"<html><body>Not a feed</body></html>",
        b'<?xml version="1.0" encoding="iso-8859-1"?><rss version="2.0"/>',
        b"\xff\xfe<\x00r\x00s\x00s\x00",
    ),
)
def test_malformed_xml_and_entity_sources_refuse_before_reading_external_material(tmp_path, raw):
    with pytest.raises(ValueError):
        parse_feed(raw, BASE, "application/xml", policy(tmp_path))


@pytest.mark.parametrize(
    "updates",
    (
        {"max_input_bytes": 10},
        {"max_xml_nodes": 2},
        {"max_xml_depth": 2},
        {"max_entries": 1, "max_links": 1, "include_attachments": True},
        {"max_text_chars": 10, "max_field_chars": 10},
        {"max_output_bytes": 100},
        {"formats": ["atom"]},
    ),
)
def test_parser_bounds_refuse_rather_than_silently_publish_a_partial_manifest(tmp_path, updates):
    with pytest.raises(ValueError):
        parse_feed(RSS, BASE, "application/xml", policy(tmp_path, **updates))


def test_json_feed_is_not_an_untyped_site_api(tmp_path):
    for raw in (
        b'{"title":"API","items":[]}',
        b'{"version":"https://jsonfeed.org/version/1.1",'
        b'"title":"feed","items":[{"id":1,"url":"/plain"}]}',
    ):
        with pytest.raises(ValueError):
            parse_feed(raw, BASE, "application/json", policy(tmp_path))


def test_worker_and_serialized_document_reject_invented_native_values(tmp_path):
    cfg = config(tmp_path)
    page = Page(url=BASE, final_url=BASE, status=200, content_type="application/rss+xml", body=RSS)
    extracted = asyncio.run(SourceFeedExtractor(cfg).extract(page))
    doc = Document(
        url=BASE,
        sha256=hashlib.sha256(RSS).hexdigest(),
        raw=RSS,
        extracted=extracted,
        verdict=Verdict(
            decision="accept",
            kind="source_manifest",
            publisher="source",
            language="und",
            reason="native declaration",
        ),
    )
    assert (
        doc.extracted.language == "und" and doc.extracted.source_feed.declared_language == "zh-CN"
    )
    assert Document.model_validate_json(doc.model_dump_json()) == doc
    doc.validate_policy(cfg)
    broken = doc.model_dump()
    broken["extracted"]["source_feed"]["entries"][0]["declared_date"] = "2030-01-01"
    broken["extracted"]["text"] = broken["extracted"]["text"].replace("2026-10-08", "2030-01-01")
    with pytest.raises(ValidationError, match="replay"):
        Document.model_validate(broken)
    broken = doc.model_dump()
    broken["extracted"]["title"] = "invented title"
    with pytest.raises(ValidationError):
        Document.model_validate(broken)
    with pytest.raises(ValueError):
        doc.validate_policy(
            config(tmp_path, source_feeds=policy(tmp_path, include_attachments=True))
        )


def test_feed_worker_deadline_reaps_child_and_releases_capacity(tmp_path, monkeypatch):
    children = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        child = await create(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    cfg = config(tmp_path, source_feeds=policy(tmp_path, timeout_seconds=0.001))
    client = SourceFeedExtractor(cfg)
    page = Page(url=BASE, final_url=BASE, status=200, content_type="application/rss+xml", body=RSS)

    async def exercise():
        for _ in range(2):
            with pytest.raises(GhimeraRefused) as exc:
                await client.extract(page)
            assert exc.value.code == RefusalCode.BUDGET_EXHAUSTED
        assert len(children) == 2 and all(child.returncode is not None for child in children)

    asyncio.run(exercise())


def test_feed_worker_cancellation_reaps_child_and_next_parse_can_run(tmp_path, monkeypatch):
    children = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        child = await create(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    client = SourceFeedExtractor(config(tmp_path, source_feeds=policy(tmp_path, max_workers=1)))
    page = Page(url=BASE, final_url=BASE, status=200, content_type="application/rss+xml", body=RSS)

    async def exercise():
        task = asyncio.create_task(client.extract(page))
        async with asyncio.timeout(5):
            while not children:
                await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None
        result = await client.extract(page)
        assert result.source_feed.format == "rss" and children[1].returncode == 0

    asyncio.run(exercise())


@pytest.fixture
def feed_site():
    counts, starts, seen = Counter(), [], []
    bodies, types = {"/plain": ARTICLE.encode(), "/feed": RSS}, {"/feed": "application/rss+xml"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            counts[self.path] += 1
            seen.append((self.path, dict(self.headers)))
            status = 200
            if self.path == "/robots.txt":
                body, mime = b"User-agent: *\nDisallow: /blocked\n", "text/plain"
            else:
                body, mime = bodies.get(self.path, b""), types.get(self.path, "text/html")
                if self.path == "/conditional" and self.headers.get("If-None-Match") == '"feed-1"':
                    status, body = 304, b""
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            if self.path == "/conditional":
                self.send_header("ETag", '"feed-1"')
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server.server_port, counts, starts, seen, bodies, types
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def test_conditional_feed_refresh_reuses_exact_original_without_counting_it_as_new_bytes(
    tmp_path, feed_site
):
    feed_site[4]["/conditional"] = RSS
    feed_site[5]["/conditional"] = "application/rss+xml"
    ladder, scope, budget, ledger, origin = fetch_state(feed_site)
    scope = scope.model_copy(update={"content_types": ("application/rss+xml",)})
    client = SourceFeedExtractor(config(tmp_path))

    async def exercise():
        first = await ladder.fetch(origin + "/conditional", scope, budget, ledger)
        first_extraction = await client.extract(first)
        spent = budget.bytes_read
        second = await ladder.fetch(origin + "/conditional", scope, budget, ledger)
        assert second.revalidated and second.body == first.body == RSS
        assert budget.bytes_read == spent
        assert await client.extract(second) == first_extraction

    asyncio.run(exercise())
    assert feed_site[1]["/robots.txt"] == 1 and feed_site[1]["/conditional"] == 2
    assert ledger.snapshot()[-1].status == 304 and ledger.snapshot()[-1].bytes_read == 0
    latest = next(headers for path, headers in reversed(feed_site[3]) if path == "/conditional")
    assert {key.lower(): value for key, value in latest.items()}["if-none-match"] == '"feed-1"'


def test_public_collector_uses_existing_fetch_frontier_graph_journal_and_native_documents(
    tmp_path, feed_site, search_endpoint, model_endpoint, encoder_endpoint
):
    graph = tomllib.loads(Path("examples/research-graph.toml").read_text())
    graph["sink_path"] = str(tmp_path / "graph")
    cfg, url = assembled(
        tmp_path,
        feed_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        source_feeds=policy(tmp_path),
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
    seed = url.replace("/plain", "/feed")
    scope = Scope(
        allowed_hosts=("fixture.example",),
        allowed_ports=(feed_site[0],),
        max_depth=2,
        content_types=("application/rss+xml", "text/html"),
    )
    result = asyncio.run(
        Collector(cfg, source_resolver=ResolverFixture()).collect(
            Goal(text="find ports", seeds=(seed,)), scope, run_id="source-feed-collection"
        )
    )
    assert {doc.url for doc in result.documents} == {seed, url}
    doc = next(doc for doc in result.documents if doc.url == seed)
    assert doc.raw == RSS and doc.extracted.source_feed.format == "rss"
    assert feed_site[1]["/feed"] == feed_site[1]["/plain"] == 1
    assert not search_endpoint[1]
    assert Harvest.model_validate_json(result.model_dump_json()) == result
    assert read_journal(cfg.journal, "source-feed-collection").rows == result.ledger
    assert asyncio.run(DirectoryGraphSink(cfg.graph, "source-feed-collection").replay())
    assert (
        next(n for n in result.graph.nodes if n.role == "document" and n.label == seed).revision
        == "source-feed-parser/1"
    )
    broken = result.model_dump()
    broken["ledger"] = [row for row in broken["ledger"] if row.get("source_feed") is None]
    for index, row in enumerate(broken["ledger"]):
        row["sequence"] = index
    with pytest.raises(ValidationError, match="parse observation"):
        Harvest.model_validate(broken)

    async def persist_and_reopen():
        selected = corpus_policy(tmp_path, encoder_endpoint[0], chunk_chars=300, overlap_chars=10)
        store = corpus(selected, create=True)
        service = PersistentCollector(Collector(cfg, source_resolver=ResolverFixture()), store)
        try:
            completed = await service.persist(result)
            assert completed.corpus.added_documents == 2
            assert (
                PersistentCollection.model_validate_json(completed.model_dump_json()) == completed
            )
            assert (await service.persist(result)).corpus.added_documents == 0
        finally:
            store.close()
        reopened = corpus(selected, create=False)
        try:
            # The two-dimensional protocol encoder ties native English readings.
            # Retrieve the admitted candidate set, not an invented ranking score.
            found = await reopened.search(
                doc.extracted.text[: selected.chunk_chars], top_k=selected.max_top_k
            )
            native = [hit for hit in found.hits if hit.passage.source_url == seed]
            assert native
            for hit in native:
                original = reopened.document(hit.passage.document_id)
                assert (
                    original.raw == RSS
                    and original.extracted.source_feed == doc.extracted.source_feed
                )
                hit.passage.validate_source(original)
        finally:
            reopened.close()
        assert feed_site[1]["/feed"] == feed_site[1]["/plain"] == 1

    asyncio.run(persist_and_reopen())


def test_manifest_links_do_not_expand_scope_or_override_robots(
    tmp_path, feed_site, search_endpoint, model_endpoint, encoder_endpoint
):
    raw = RSS.replace(b"/plain", b"/blocked").replace(
        b"</channel>",
        b"<item><title>port</title><link>http://other.example/private</link></item></channel>",
    )
    feed_site[4]["/feed"] = raw
    cfg, url = assembled(
        tmp_path,
        feed_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        source_feeds=policy(tmp_path),
        min_link_score=0.0,
        grade_interval=100,
    )
    seed = url.replace("/plain", "/feed")
    result = asyncio.run(
        Collector(cfg, source_resolver=ResolverFixture()).collect(
            Goal(text="find ports", seeds=(seed,)),
            Scope(
                allowed_hosts=("fixture.example",),
                allowed_ports=(feed_site[0],),
                max_depth=2,
                content_types=("application/rss+xml", "text/html"),
            ),
        )
    )
    assert feed_site[1]["/feed"] == 1 and feed_site[1]["/blocked"] == 0
    assert feed_site[1]["/private"] == 0
    assert any(row.refusal == RefusalCode.ROBOTS_DISALLOWED for row in result.ledger)
    assert len(result.documents) == 1


def test_sitemap_index_advances_via_frontier_not_serial_recursive_fetch(
    tmp_path, feed_site, search_endpoint, model_endpoint, encoder_endpoint
):
    origin = f"http://fixture.example:{feed_site[0]}"
    feed_site[4]["/index.xml"] = INDEX
    feed_site[4]["/child.xml"] = SITEMAP.replace(b"https://source.example", origin.encode())
    feed_site[5].update({"/index.xml": "application/xml", "/child.xml": "application/xml"})
    cfg, _ = assembled(
        tmp_path,
        feed_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        source_feeds=policy(tmp_path),
        min_link_score=0.0,
        grade_interval=100,
    )
    result = asyncio.run(
        Collector(cfg, source_resolver=ResolverFixture()).collect(
            Goal(text="find ports", seeds=(origin + "/index.xml",)),
            Scope(
                allowed_hosts=("fixture.example",),
                allowed_ports=(feed_site[0],),
                max_depth=3,
                content_types=("application/xml", "text/html"),
            ),
        )
    )
    assert {doc.url for doc in result.documents} == {
        origin + path for path in ("/index.xml", "/child.xml", "/plain")
    }
    assert all(feed_site[1][path] == 1 for path in ("/index.xml", "/child.xml", "/plain"))
    assert Harvest.model_validate_json(result.model_dump_json()) == result
