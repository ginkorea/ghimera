"""Native guarded 200/304 across process owners; no external source or model calls."""

import asyncio
import hashlib
import os
import sqlite3
import subprocess
import sys
import threading
import time

import pytest
from pydantic import SecretStr, ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.fetch import FetchLadder
from ghimera.http import CurlRoute
from ghimera.ledger import Ledger
from ghimera.models import Page
from ghimera.source_refresh import SourceRefreshFailure, SourceRefreshKey, SourceRefreshStore
from ghimera.source_refresh_config import SourceRefreshConfig
from ghimera.source_sessions import SourceCredentials
from tests.test_http_fetch import ResolverFixture, site, state

__all__ = ["site"]


def policy(tmp_path, url, **updates):
    raw = dict(
        schema="ghimera.source-refresh/1",
        directory=tmp_path / "refresh",
        create_if_missing=True,
        urls=[url],
        max_versions=20,
        max_record_bytes=100_000,
        max_store_bytes=1_000_000,
        max_validator_age_seconds=3600.0,
        database_timeout_seconds=2.0,
    )
    raw.update(updates)
    return SourceRefreshConfig.model_validate(raw)


def configured(tmp_path, site, **updates):
    _, scope, budget, _, origin = state(site)
    raw = budget.config.model_dump()
    raw.update(source_refresh=policy(tmp_path, origin + "/conditional"))
    raw.update(updates)
    return GhimeraConfig.model_validate(raw), scope, origin + "/conditional"


def native(cfg, *, clock=lambda: 100.0, credentials=None):
    store = SourceRefreshStore(cfg.source_refresh, clock=clock)
    route = CurlRoute(cfg, resolver=ResolverFixture(), source_credentials=credentials)
    return FetchLadder((route,), source_refresh=store), store


def get(ladder, cfg, scope, url):
    previous = RunBudget(cfg, time.monotonic)
    ledger = Ledger()
    result = asyncio.run(ladder.fetch(url, scope, previous, ledger))
    return result, previous, ledger.snapshot()


def test_new_ladder_reuses_only_after_native_304_and_counts_actual_bytes(tmp_path, site):
    cfg, scope, url = configured(tmp_path, site)
    first, store = native(cfg)
    original, _, _ = get(first, cfg, scope, url)
    second, reopened = native(cfg, clock=lambda: 110.0)
    reused, budget, rows = get(second, cfg, scope, url)
    assert reused.body == original.body and reused.revalidated
    assert reused.source_refresh.source_sha256 == hashlib.sha256(original.body).hexdigest()
    assert reused.source_refresh.captured_at == 100.0
    assert reused.source_refresh.checked_at == 110.0
    assert budget.fetches == 2
    assert [(r.status, r.bytes_read) for r in rows if r.event == "fetch"] == [
        (200, len(b"User-agent: *\nDisallow: /blocked\n")),
        (304, 0),
    ]
    assert budget.bytes_read == sum(r.bytes_read for r in rows)
    assert rows[-1].source_refresh == reused.source_refresh
    assert len(asyncio.run(reopened.versions())) == len(asyncio.run(store.versions())) == 1
    assert Page.model_validate_json(reused.model_dump_json()) == reused


def test_fresh_native_subprocess_reads_persisted_version(tmp_path, site):
    cfg, scope, url = configured(tmp_path, site)
    first, _ = native(cfg)
    original, _, _ = get(first, cfg, scope, url)
    code = """
import asyncio,json,sys,time
from ghimera.config import GhimeraConfig
from ghimera.budget import RunBudget
from ghimera.models import Scope
from ghimera.fetch import FetchLadder
from ghimera.http import CurlRoute
from ghimera.ledger import Ledger
from ghimera.source_refresh import SourceRefreshStore
from tests.test_http_fetch import ResolverFixture
cfg=GhimeraConfig.model_validate_json(sys.stdin.readline())
scope=Scope.model_validate_json(sys.stdin.readline())
url=sys.stdin.readline().strip()
store=SourceRefreshStore(cfg.source_refresh,clock=lambda:110.0)
ladder=FetchLadder((CurlRoute(cfg,resolver=ResolverFixture()),),source_refresh=store)
page=asyncio.run(ladder.fetch(url,scope,RunBudget(cfg,time.monotonic),Ledger()))
print(page.model_dump_json())
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=cfg.model_dump_json() + "\n" + scope.model_dump_json() + "\n" + url + "\n",
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )
    page = Page.model_validate_json(result.stdout)
    assert page.body == original.body and page.source_refresh is not None
    assert {k.lower(): v for k, v in site[3][-1][1].items()}["if-none-match"] == '"original"'


def test_changed_200_appends_without_replacing_old_original(tmp_path, site):
    cfg, scope, url = configured(tmp_path, site)
    ladder, store = native(cfg)
    original, _, _ = get(ladder, cfg, scope, url)
    site[4]["/plain"] = b"<article>new port evidence</article>"
    site[4]["/conditional_etag"] = b'"updated"'
    ladder, _ = native(cfg, clock=lambda: 111.0)
    updated, _, _ = get(ladder, cfg, scope, url)
    versions = asyncio.run(store.versions())
    assert updated.source_refresh is None and not updated.revalidated
    assert [v.page.body for v in versions] == [original.body, updated.body]
    assert versions[1].previous_sha256 == versions[0].identity


def test_expired_validator_causes_full_fetch(tmp_path, site):
    cfg, scope, url = configured(tmp_path, site)
    ladder, _ = native(cfg)
    get(ladder, cfg, scope, url)
    ladder, _ = native(cfg, clock=lambda: 4000.0)
    current, _, _ = get(ladder, cfg, scope, url)
    assert not current.revalidated
    assert "if-none-match" not in {k.lower() for k in site[3][-1][1]}


def test_changed_credentials_do_not_receive_prior_validator_or_body(tmp_path, site):
    cfg, scope, url = configured(tmp_path, site)
    raw = cfg.model_dump()
    raw["source_sessions"] = [
        dict(
            schema="chimera.source-session/1",
            session_id="authorized-fixture",
            origin=url.rsplit("/", 1)[0],
            path_prefixes=["/conditional"],
            header_names=["authorization"],
            allow_http=True,
        )
    ]
    cfg = GhimeraConfig.model_validate(raw)

    def credentials(value):
        return {
            "authorized-fixture": SourceCredentials(headers=(("authorization", SecretStr(value)),))
        }

    ladder, store = native(cfg, credentials=credentials("Bearer controlled-account-a"))
    get(ladder, cfg, scope, url)
    ladder, _ = native(cfg, credentials=credentials("Bearer controlled-account-b"))
    current, _, _ = get(ladder, cfg, scope, url)
    assert not current.revalidated
    assert "if-none-match" not in {k.lower() for k in site[3][-1][1]}
    versions = asyncio.run(store.versions())
    assert versions[0].key != versions[1].key
    contents = (cfg.source_refresh.directory / "versions.sqlite").read_bytes()
    assert b"controlled-account-a" not in contents and b"controlled-account-b" not in contents


@pytest.mark.parametrize(
    "header,value",
    [
        ("/conditional_cache_control", b"no-store"),
        ("/conditional_vary", b"Accept-Language, *"),
    ],
)
def test_no_store_records_invalidation_not_body_and_does_not_resurrect(
    tmp_path, site, header, value
):
    cfg, scope, url = configured(tmp_path, site)
    first, store = native(cfg)
    get(first, cfg, scope, url)
    site[4][header] = value
    site[4]["/conditional_etag"] = b'"no-store-original"'
    site[4]["/plain"] = b"not for persistent cache"
    second, _ = native(cfg)
    fresh, _, _ = get(second, cfg, scope, url)
    assert fresh.source_refresh is None and fresh.body == b"not for persistent cache"
    del site[4][header]
    third, _ = native(cfg)
    fresh, _, _ = get(third, cfg, scope, url)
    assert not fresh.revalidated
    assert "if-none-match" not in {k.lower() for k in site[3][-1][1]}
    versions = asyncio.run(store.versions())
    assert len(versions) == 3 and versions[1].page is None


def test_robots_refusal_still_prevents_even_cached_target_contact(tmp_path, site):
    cfg, scope, url = configured(tmp_path, site)
    first, _ = native(cfg)
    get(first, cfg, scope, url)
    site[4]["/robots.txt"] = b"User-agent: *\nDisallow: /conditional\n"
    second, _ = native(cfg)
    from ghimera.refusals import GhimeraRefused

    with pytest.raises(GhimeraRefused, match="robots_disallowed"):
        get(second, cfg, scope, url)
    assert site[1]["/conditional"] == 1


def test_store_binding_is_required_before_any_request(tmp_path, site):
    cfg, scope, url = configured(tmp_path, site)
    ladder = FetchLadder((CurlRoute(cfg, resolver=ResolverFixture()),))
    with pytest.raises(SourceRefreshFailure, match="matching"):
        get(ladder, cfg, scope, url)
    assert not site[1]


def test_capacity_failure_preserves_originals(tmp_path, site):
    _, scope, url = configured(tmp_path, site)
    cfg, _, _ = configured(tmp_path, site, source_refresh=policy(tmp_path, url, max_versions=1))
    first, store = native(cfg)
    original, _, _ = get(first, cfg, scope, url)
    site[4]["/conditional_etag"] = b'"new"'
    second, _ = native(cfg)
    with pytest.raises(SourceRefreshFailure, match="capacity exhausted"):
        get(second, cfg, scope, url)
    assert [v.page.body for v in asyncio.run(store.versions())] == [original.body]


@pytest.mark.parametrize("mutation", ["payload", "tail", "head"])
def test_changed_store_refuses_instead_of_volatile_fallback(tmp_path, site, mutation):
    cfg, scope, url = configured(tmp_path, site)
    first, _ = native(cfg)
    get(first, cfg, scope, url)
    with sqlite3.connect(cfg.source_refresh.directory / "versions.sqlite") as db:
        if mutation == "payload":
            db.execute("UPDATE versions SET payload=?", (b"bad",))
            db.execute("UPDATE metadata SET bytes=3")
        elif mutation == "tail":
            db.execute("DELETE FROM versions")
        else:
            db.execute("UPDATE metadata SET head=?", ("0" * 64,))
    with pytest.raises(SourceRefreshFailure):
        second, _ = native(cfg)
        get(second, cfg, scope, url)
    assert site[1]["/conditional"] == 1


def test_missing_or_public_storage_and_clock_rollback_refuse(tmp_path, site):
    cfg, scope, url = configured(tmp_path, site)
    first, store = native(cfg)
    get(first, cfg, scope, url)
    backward, _ = native(cfg, clock=lambda: 99.0)
    with pytest.raises(SourceRefreshFailure):
        get(backward, cfg, scope, url)
    os.chmod(cfg.source_refresh.directory, 0o755)
    with pytest.raises(SourceRefreshFailure):
        SourceRefreshStore(cfg.source_refresh)
    os.chmod(cfg.source_refresh.directory, 0o700)
    (cfg.source_refresh.directory / "versions.sqlite").unlink()
    with pytest.raises(SourceRefreshFailure):
        SourceRefreshStore(cfg.source_refresh)
    with pytest.raises(SourceRefreshFailure):
        asyncio.run(store.versions())


@pytest.mark.parametrize(
    "updates",
    [
        {"urls": ["https://user:secret@example.org/file"]},
        {"urls": ["https://example.org/#part"]},
        {"directory": "/"},
        {"max_store_bytes": 1},
        {"max_validator_age_seconds": float("inf")},
    ],
)
def test_invalid_policy_is_rejected(tmp_path, updates):
    with pytest.raises(ValidationError):
        policy(tmp_path, "https://example.org/file", **updates)


def test_graph_uses_distinct_refresh_provenance_without_changing_old_identity(tmp_path, site):
    from ghimera.graph import MemoryGraphSink, ResearchGraph
    from tests.test_research_graph import policy as graph_policy

    cfg, scope, url = configured(tmp_path, site)
    first, _ = native(cfg)
    old, _, _ = get(first, cfg, scope, url)
    second, _ = native(cfg, clock=lambda: 110.0)
    new, _, _ = get(second, cfg, scope, url)
    graph = ResearchGraph(graph_policy(tmp_path / "graph"), "refresh-test", MemoryGraphSink())
    before = graph.document_node(url, old.body, "port evidence", "extractor@1")
    after = graph.document_node(
        url, new.body, "port evidence", "extractor@1", source_refresh=new.source_refresh
    )
    assert before.content_sha256 == after.content_sha256 and before.id != after.id
    assert "source_refresh" not in before.model_dump()
    assert after.source_refresh == new.source_refresh
    with pytest.raises(ValidationError):
        type(after).model_validate(
            after.model_copy(update={"source_url": url + "/other"}).model_dump()
        )


def test_parallel_store_writers_keep_one_complete_immutable_chain(tmp_path, site):
    cfg, scope, url = configured(tmp_path, site)
    ladder, store = native(cfg)
    original, _, _ = get(ladder, cfg, scope, url)
    key = asyncio.run(store.versions())[0].key

    async def write():
        reopened = SourceRefreshStore(cfg.source_refresh, clock=lambda: 100.0)
        await asyncio.gather(store.capture(key, original), reopened.capture(key, original))
        return await store.versions()

    versions = asyncio.run(write())
    assert [v.sequence for v in versions] == [0, 1, 2]
    assert versions[1].previous_sha256 == versions[0].identity
    assert versions[2].previous_sha256 == versions[1].identity


def test_cancelled_write_drains_its_owned_transaction_before_return(tmp_path, site, monkeypatch):
    cfg, scope, url = configured(tmp_path, site)
    ladder, store = native(cfg)
    original, _, _ = get(ladder, cfg, scope, url)
    key = asyncio.run(store.versions())[0].key
    entered, release = threading.Event(), threading.Event()
    previous = store._operation

    def delayed(action):
        entered.set()
        assert release.wait(5)
        return previous(action)

    monkeypatch.setattr(store, "_operation", delayed)

    async def cancel():
        task = asyncio.create_task(store.capture(key, original))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    monkeypatch.setattr(store, "_operation", previous)
    assert len(asyncio.run(store.versions())) == 2


def test_access_refusal_invalidates_without_retaining_refused_content(tmp_path, site):
    cfg, scope, _ = configured(tmp_path, site)
    url = f"http://fixture.example:{site[0]}/forbidden"
    cfg, scope, _ = configured(tmp_path, site, source_refresh=policy(tmp_path, url))
    route = CurlRoute(cfg, resolver=ResolverFixture())
    store = SourceRefreshStore(cfg.source_refresh, clock=lambda: 100.0)
    key = SourceRefreshKey(
        schema="ghimera.source-refresh-key/1",
        url=url,
        route=route.name,
        representation_sha256=route.conditional_binding(url),
    )
    asyncio.run(
        store.capture(
            key,
            Page(
                url=url,
                final_url=url,
                status=200,
                content_type="text/html",
                body=b"previous entitled response",
            ),
        )
    )
    from ghimera.refusals import GhimeraRefused

    with pytest.raises(GhimeraRefused):
        get(FetchLadder((route,), source_refresh=store), cfg, scope, url)
    assert asyncio.run(store.latest(key)) is None
    versions = asyncio.run(store.versions())
    assert len(versions) == 2 and versions[-1].page is None


def test_safe_toml_example_and_legacy_wire_shape(tmp_path, site):
    import tomllib
    from pathlib import Path

    cfg = state(site)[2].config
    assert "source_refresh" not in cfg.model_dump()
    assert (
        "source_refresh"
        not in Page(
            url="https://example.org/",
            final_url="https://example.org/",
            status=200,
            content_type="text/html",
            body=b"x",
        ).model_dump()
    )
    example = tomllib.loads(Path("examples/source-refresh.toml").read_text())["source_refresh"]
    example["directory"] = tmp_path / "refresh"
    raw = cfg.model_dump()
    raw["source_refresh"] = example
    enabled = GhimeraConfig.model_validate(raw)
    assert enabled.source_refresh.create_if_missing is False
    with pytest.raises(SourceRefreshFailure):
        SourceRefreshStore(enabled.source_refresh)


def test_guarded_redirect_keeps_initial_request_and_final_refresh_identity(tmp_path, site):
    cfg, scope, target = configured(tmp_path, site)
    initial = target.rsplit("/", 1)[0] + "/conditional-redirect"
    first, _ = native(cfg)
    get(first, cfg, scope, initial)
    second, _ = native(cfg, clock=lambda: 110.0)
    page, _, rows = get(second, cfg, scope, initial)
    assert page.url == initial and page.final_url == target
    assert page.source_refresh.source_url == target and page.revalidated
    assert Page.model_validate_json(page.model_dump_json()) == page
    assert [r.status for r in rows if r.event == "fetch"] == [200, 302, 304]


def test_store_itself_refuses_no_store_original_even_outside_fetch_ladder(tmp_path, site):
    cfg, scope, url = configured(tmp_path, site)
    ladder, store = native(cfg)
    original, _, _ = get(ladder, cfg, scope, url)
    key = asyncio.run(store.versions())[0].key
    forbidden = original.model_copy(update={"headers": (("cache-control", "no-store"),)})
    with pytest.raises(SourceRefreshFailure):
        asyncio.run(store.capture(key, forbidden))
    assert len(asyncio.run(store.versions())) == 1
