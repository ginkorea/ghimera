"""Native process-loss witnesses for queued work, separate from uncertain external calls."""

import asyncio
import hashlib
import sqlite3
import subprocess
import sys

import pytest
from pydantic import ValidationError

from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.journal import read_journal
from ghimera.loop import GoalLoop
from ghimera.models import Goal
from ghimera.source_work import SourceWorkFailure, SourceWorkStore, read_source_work
from ghimera.source_work_types import SourceCoordinates, SourceFrontierEntry
from tests.test_c0 import scope
from tests.test_execution import LeafExtractor, policy
from tests.test_source_work import collector, configured


def frontier_config(tmp_path, **updates):
    cfg = configured(tmp_path)
    settings = dict(
        schema="ghimera.source-frontier/1",
        max_entries=30,
        max_entry_bytes=4000,
        max_frontier_bytes=100000,
    )
    settings.update(updates)
    raw = cfg.model_dump()
    raw["source_work"]["frontier"] = settings
    return type(cfg).model_validate(raw)


def test_queue_configuration_is_explicit_without_changing_existing_recipe(tmp_path):
    assert "frontier" not in configured(tmp_path).source_work.model_dump()
    assert frontier_config(tmp_path).source_work.frontier.max_entries == 30
    for updates in (
        dict(max_entries=0),
        dict(max_entry_bytes=True),
        dict(max_frontier_bytes=1),
        dict(max_frontier_bytes=10000001),
        dict(unknown=True),
    ):
        with pytest.raises(ValidationError):
            frontier_config(tmp_path, **updates)
    cfg = frontier_config(tmp_path)
    raw = cfg.model_dump()
    raw["source_work"]["max_store_bytes"] = cfg.source_work.max_operation_bytes
    with pytest.raises(ValidationError, match="capacity"):
        type(cfg).model_validate(raw)


@pytest.mark.parametrize("crash", ["first_fetch", "after_enqueue"])
def test_process_loss_preserves_all_seed_intents_before_first_request(tmp_path, crash):
    cfg = frontier_config(tmp_path)
    path = tmp_path / "config.json"
    path.write_text(cfg.model_dump_json())
    executed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, os, sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Goal, Scope
from ghimera.source_work import SourceWorkStore
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
crash = sys.argv[2]
original = SourceWorkStore.enqueue
def enqueue(self, request, priority, ledger_start):
    original(self, request, priority, ledger_start)
    if crash == 'after_enqueue':
        os._exit(23)
SourceWorkStore.enqueue = enqueue
class Route(FakeRoute):
    async def attempt(self, request):
        os._exit(23)
loop = GoalLoop(config=cfg, fetcher=FetchLadder((Route(),)), extractor=FakeExtractor(),
                scorer=KeywordScorer(), judge=FakeJudge())
asyncio.run(loop.run(Goal(text='ports', seeds=(
    'https://example.org/a', 'https://example.org/b')),
    Scope(allowed_hosts=('example.org',), max_depth=3, content_types=('text/html',)),
    run_id='lost'))
""",
            str(path),
            crash,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert executed.returncode == 23, executed.stderr
    report = read_source_work(cfg, "lost")
    assert not report.writer_active
    if crash == "first_fetch":
        assert len(report.frontier) == 2
        assert len(report.operations) == len(report.unresolved) == 1
        assert [item.request.url for item in report.queued] == ["https://example.org/b"]
    else:
        assert len(report.frontier) == 1 and not report.operations
        assert [item.request.url for item in report.queued] == ["https://example.org/a"]
    assert all(item.priority == -1 and item.ledger_start == 0 for item in report.frontier)


def test_frontier_capacity_refuses_before_any_seed_fetch(tmp_path):
    cfg, route = frontier_config(tmp_path, max_entries=1), FakeRoute()
    with pytest.raises(SourceWorkFailure, match="intent"):
        asyncio.run(
            collector(cfg, route=route).run(
                Goal(text="ports", seeds=("https://example.org/a", "https://example.org/b")),
                scope(),
                run_id="capacity",
            )
        )
    report = read_source_work(cfg, "capacity")
    assert not route.requests and not report.operations
    assert len(report.queued) == 1 and not report.unresolved


def test_discovered_links_keep_scope_depth_priority_and_completed_work_is_not_queued(tmp_path):
    cfg = frontier_config(tmp_path)
    result = asyncio.run(
        collector(cfg).run(
            Goal(text="ports", seeds=("https://example.org/start",)),
            scope(),
            run_id="links",
        )
    )
    report = read_source_work(cfg, "links")
    # The extractor also discovers an out-of-scope link. It must retain that
    # intent and its refusal without manufacturing an acquisition operation.
    assert len(report.frontier) == 4 and len(report.operations) == 3
    assert not report.queued and not report.unresolved
    discarded = tuple(item for item in report.frontier if item.discard_reason is not None)
    assert len(discarded) == 1 and discarded[0].request.url == "https://evil.example/x"
    assert discarded[0].discard_reason == "out_of_scope"
    assert report.frontier[0].request.depth == 0
    for entry in report.frontier[1:]:
        assert entry.request.depth > 0 and entry.request.scope == scope()
        assert entry.priority < 0
        assert entry.ledger_start <= len(result.ledger)


def test_out_of_scope_seed_is_durably_discarded_not_sent_to_route(tmp_path):
    cfg, route = frontier_config(tmp_path), FakeRoute()
    result = asyncio.run(
        collector(cfg, route=route).run(
            Goal(text="ports", seeds=("https://elsewhere.org/private",)),
            scope(),
            run_id="scope",
        )
    )
    report = read_source_work(cfg, "scope")
    assert not route.requests and not report.operations and not report.queued
    assert report.frontier[0].discard_reason == "out_of_scope"
    boundary = report.frontier[0].ledger_end
    assert boundary == 1 and result.ledger[boundary - 1].event == "refusal"
    assert result.ledger[boundary - 1].url == report.frontier[0].request.url
    assert result.ledger[-1].event == "stop"  # A later run acknowledgement, not this source's.


def test_parallel_collection_uses_the_same_durable_queue_without_serializing_models(tmp_path):
    cfg = frontier_config(tmp_path)
    raw = cfg.model_dump()
    raw.update(execution=policy().model_dump(), grade_interval=100, saturation_window=100)
    cfg = type(cfg).model_validate(raw)

    async def run():
        reached = asyncio.Event()

        class Extractor(LeafExtractor):
            async def extract(self, page):
                if page.url.endswith("/a"):
                    await asyncio.wait_for(reached.wait(), timeout=2)
                return await super().extract(page)

        class Judge(FakeJudge):
            async def document(self, goal, document, *, second_look):
                if document.title.endswith("/b"):
                    reached.set()
                return await super().document(goal, document, second_look=second_look)

        loop = GoalLoop(
            config=cfg,
            fetcher=FetchLadder((FakeRoute(),)),
            extractor=Extractor(),
            scorer=KeywordScorer(),
            judge=Judge(),
        )
        result = await loop.run(
            Goal(text="ports", seeds=("https://example.org/a", "https://example.org/b")),
            scope(),
            run_id="parallel",
        )
        assert result.receipt.fetches == len(result.documents) == 2

    asyncio.run(run())
    report = read_source_work(cfg, "parallel")
    assert len(report.frontier) == len(report.operations) == 2
    assert not report.queued and not report.unresolved


@pytest.mark.parametrize("mutation", ["tail", "priority", "journal"])
def test_queue_readback_refuses_deleted_or_altered_acknowledgements(tmp_path, mutation):
    cfg = frontier_config(tmp_path)
    asyncio.run(
        collector(cfg).run(
            Goal(text="ports", seeds=("https://example.org/start",)),
            scope(),
            run_id="altered",
        )
    )
    path = tmp_path / "runs" / "altered" / "source-work" / "operations.sqlite"
    with sqlite3.connect(path) as db:
        if mutation == "tail":
            db.execute("DELETE FROM frontier WHERE sequence=(SELECT MAX(sequence) FROM frontier)")
        else:
            row = db.execute("SELECT id,payload FROM frontier ORDER BY sequence LIMIT 1").fetchone()
            entry = SourceFrontierEntry.model_validate_json(row[1])
            update = dict(entry.model_dump())
            if mutation == "priority":
                update["priority"] = 0.25
                # Malformed policy with a recomputed payload pin must still refuse.
                import json

                payload = json.dumps(update).encode()
            else:
                update["ledger_start"] = 10000
                payload = SourceFrontierEntry.model_validate(update).model_dump_json().encode()
            db.execute(
                "UPDATE frontier SET payload=?,sha256=? WHERE id=?",
                (payload, hashlib.sha256(payload).hexdigest(), row[0]),
            )
    with pytest.raises(ValueError):
        read_source_work(cfg, "altered")


def test_repeat_enqueue_keeps_first_observation_and_highest_priority(tmp_path):
    async def run():
        cfg = frontier_config(tmp_path)
        session = await collector(cfg).open(Goal(text="ports"), run_id="repeat")
        try:
            session.queue_source(scope(), "https://example.org/a", 1, -0.3)
            first = session.source_work.report().frontier[0]
            session.queue_source(scope(), "https://example.org/a", 1, -0.8)
            session.queue_source(scope(), "https://example.org/a", 1, -0.2)
            report = session.source_work.report()
            assert len(report.frontier) == 1
            assert report.frontier[0].priority == -0.8
            assert report.frontier[0].queued_at == first.queued_at
            assert report.frontier[0].request == SourceCoordinates(
                url="https://example.org/a",
                scope=scope(),
                depth=1,
                reference_hops=0,
                reference_origin=None,
            )
        finally:
            session.close()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["missing", "invented", "priority", "ancestry"])
def test_checkpoint_cannot_lose_or_rewrite_queued_work(tmp_path, change):
    async def run():
        cfg = frontier_config(tmp_path)
        session = await collector(cfg).open(Goal(text="ports"), run_id="checkpoint")
        try:
            session.queue_source(scope(), "https://example.org/a", 1, -0.8)
            state = session.checkpoint_state()
            updated = dict(state.model_dump())
            if change == "missing":
                updated["frontier"] = ()
            elif change == "invented":
                updated["frontier"] += ((-1, "https://example.org/unobserved", 0),)
            elif change == "priority":
                updated["frontier"] = ((-0.3, "https://example.org/a", 1),)
            else:
                updated["reference_hops"] = {"https://example.org/a": 1}
            with pytest.raises(SourceWorkFailure, match="checkpoint"):
                session.source_work.verify_frontier(type(state).model_validate(updated))
        finally:
            session.close()

    asyncio.run(run())


def test_changed_queue_after_checkpoint_refuses_reopening_without_external_calls(tmp_path):
    async def run():
        cfg = frontier_config(tmp_path)
        loop = collector(cfg, extractor=LeafExtractor())
        session = await loop.open(Goal(text="ports"), run_id="continuation")
        session.queue_source(scope(), "https://example.org/a", 0, -1)
        state, harvest = session.checkpoint_state(), loop.snapshot(session)
        session.close()
        store = SourceWorkStore.resume(cfg, "continuation", len(harvest.ledger))
        try:
            store.enqueue(
                SourceCoordinates(
                    url="https://example.org/b",
                    scope=scope(),
                    depth=0,
                    reference_hops=0,
                    reference_origin=None,
                ),
                -1,
                len(harvest.ledger),
            )
        finally:
            store.close()
        with pytest.raises(SourceWorkFailure, match="checkpoint"):
            await loop.restore("continuation", harvest, state, search_calls=0, downtime_seconds=0)
        report = read_source_work(cfg, "continuation")
        assert len(report.queued) == 2 and not report.writer_active
        assert read_journal(cfg.journal, "continuation").rows == ()

    asyncio.run(run())


def test_quiescent_frontier_resumes_under_its_exact_checkpoint_without_refetch(tmp_path):
    async def run():
        cfg, route = frontier_config(tmp_path), FakeRoute()
        loop = collector(cfg, route=route, extractor=LeafExtractor())
        session = await loop.open(Goal(text="ports"), run_id="resume")
        session.queue_source(scope(), "https://example.org/a", 0, -1)
        state, harvest = session.checkpoint_state(), loop.snapshot(session)
        session.close()
        restored = await loop.restore("resume", harvest, state, search_calls=0, downtime_seconds=0)
        try:
            assert not route.requests
            assert restored.checkpoint_state() == state
            await loop.collect(restored, scope(), (), allow_grade=False)
            assert len(route.requests) == 1
            assert not restored.source_work.report().queued
        finally:
            restored.close()

    asyncio.run(run())


def test_queued_reference_ancestry_is_retained_and_cannot_be_silently_changed(tmp_path):
    async def run():
        cfg = frontier_config(tmp_path)
        session = await collector(cfg).open(Goal(text="ports"), run_id="reference")
        target = "https://reference.org/report"
        selected = scope(allowed_hosts=("reference.org",), max_depth=1)
        try:
            session._reference_scopes[target] = selected
            session._reference_hops[target] = 1
            session._reference_origins[target] = target
            session.queue_source(scope(), target, 0, -0.7)
            item = session.source_work.report().queued[0]
            assert item.request.scope == selected
            assert item.request.reference_hops == 1
            assert item.request.reference_origin == target
            session._reference_hops[target] = 2
            with pytest.raises(SourceWorkFailure, match="intent"):
                session.queue_source(scope(), target, 0, -0.8)
            assert session.source_work.report().queued[0] == item
        finally:
            session.close()

    asyncio.run(run())


def test_scheduler_cancellation_acknowledges_not_started_intents_without_unknown_spend(tmp_path):
    async def run():
        cfg = frontier_config(tmp_path)
        cfg = type(cfg).model_validate(dict(cfg.model_dump(), execution=policy()))
        started, route = asyncio.Event(), FakeRoute()

        class NotAcquired(GoalLoop):
            async def _collect_source(self, session, scope, url, depth):
                started.set()
                await asyncio.Future()

        loop = NotAcquired(
            config=cfg,
            fetcher=FetchLadder((route,)),
            extractor=LeafExtractor(),
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        )
        session = await loop.open(Goal(text="ports"), run_id="cancel-before-start")
        try:
            task = asyncio.create_task(
                loop.collect(
                    session,
                    scope(),
                    (
                        "https://example.org/a",
                        "https://example.org/b",
                    ),
                )
            )
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
            assert not route.requests and not session.ledger.snapshot()
            report = session.source_work.report()
            assert not report.operations and not report.queued
            assert len(report.frontier) == 2
            assert all(item.discard_reason == "scheduler_cancelled" for item in report.frontier)
            session.checkpoint_state()
        finally:
            session.close()

    asyncio.run(run())


def test_observed_reference_runs_through_the_native_admission_and_frontier_boundaries(tmp_path):
    from tests.test_reference_expansion import ReferenceExtractor, references

    cfg = frontier_config(tmp_path)
    cfg = type(cfg).model_validate(
        dict(
            cfg.model_dump(),
            references=references(outside_scope="observed_public"),
        )
    )
    route = FakeRoute()
    result = asyncio.run(
        collector(cfg, route=route, extractor=ReferenceExtractor()).run(
            Goal(text="ports", seeds=("https://example.org/root",)),
            scope(max_depth=0),
            run_id="admitted-reference",
        )
    )
    report = read_source_work(cfg, "admitted-reference")
    assert [item.url for item in route.requests] == [
        "https://example.org/root",
        "https://references.example/ports",
    ]
    assert len(report.frontier) == len(report.operations) == 2 and not report.queued
    child = report.frontier[1]
    assert child.request.depth == 0 and child.request.reference_hops == 1
    assert child.request.reference_origin == "https://references.example/ports"
    assert child.request.scope.permits(child.request.url)
    assert not scope().permits(child.request.url)
    assert result.ledger[child.ledger_start - 1].reference.outcome == "queued"


def test_full_research_round_reopens_queued_work_without_recollecting_completed_source(tmp_path):
    from tests.test_research_continuation import assemble, suspend
    from tests.test_research_continuation import configured as research_config

    cfg = research_config(tmp_path)
    cfg = type(cfg).model_validate(
        dict(
            cfg.model_dump(),
            source_work=frontier_config(tmp_path).source_work,
        )
    )
    first, original_route, _, _ = assemble(cfg, missing=True)
    receipt = suspend(first)
    before = read_source_work(cfg, "research")
    assert before.queued and not before.writer_active and not before.unresolved
    before_fetches = sum(row.event == "fetch" for row in read_journal(cfg.journal, "research").rows)
    resumed, new_route, new_search, _ = assemble(cfg, missing=True)
    result = asyncio.run(resumed.resume("research", checkpoint_sha256=receipt.sha256))
    assert not new_search.requests
    assert all(item.url != original_route.requests[0].url for item in new_route.requests)
    # The native fetch budget also includes the already-observed search request;
    # it must survive continuation, not be silently subtracted as non-page work.
    assert result.harvest.receipt.fetches == before_fetches + len(new_route.requests)
    after = read_source_work(cfg, "research")
    assert not after.writer_active and not after.unresolved
    assert result.status == "partial" and result.stop_reason == "rounds_exhausted"
    fetched_urls = {item.url for item in (*original_route.requests, *new_route.requests)}
    assert {item.request.url for item in after.operations} == fetched_urls
    assert len(after.queued) == 1
    assert after.queued[0].request.url == "https://example.org/one/next/next/next"
    assert after.queued[0].request.url not in fetched_urls
