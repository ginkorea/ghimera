"""Durable native acquisition before processing; real process loss, no refetch."""

import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.journal import read_journal
from ghimera.loop import GoalLoop
from ghimera.models import Goal
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.source_work import SourceWorkFailure, SourceWorkStore, read_source_work
from ghimera.source_work_types import SourceRequest
from tests.test_c0 import config, scope
from tests.test_execution import LeafExtractor
from tests.test_execution import policy as execution_policy


def configured(tmp_path, **updates):
    policy = dict(
        schema="ghimera.source-work/1",
        max_operations=20,
        max_page_bytes=100000,
        max_result_bytes=200000,
        max_operation_bytes=310000,
        max_store_bytes=10000000,
        database_timeout_seconds=2,
    )
    policy.update(updates)
    return config(
        page_budget=2,
        source_work=policy,
        journal=dict(
            schema="chimera.run-journal-config/1",
            directory=str(tmp_path / "runs"),
            max_record_bytes=1000000,
            max_journal_bytes=10000000,
            max_summary_bytes=1000000,
            max_records=1000,
        ),
    )


def collector(cfg, *, route=None, extractor=None):
    return GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route or FakeRoute(),)),
        extractor=extractor or FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )


def test_policy_is_explicit_and_default_serialized_identity_is_unchanged(tmp_path):
    assert "source_work" not in config().model_dump()
    cfg = configured(tmp_path)
    assert cfg.source_work.schema_version == "ghimera.source-work/1"
    with pytest.raises(ValidationError, match="durable run journal"):
        config(source_work=cfg.source_work.model_dump())
    for changes in (
        {"max_operations": 0},
        {"max_operation_bytes": 10},
        {"database_timeout_seconds": float("nan")},
        {"extra": True},
    ):
        with pytest.raises(ValidationError):
            configured(tmp_path, **changes)


def test_completed_originals_and_results_survive_fresh_process_without_calls(tmp_path):
    cfg, route = configured(tmp_path), FakeRoute()
    result = asyncio.run(
        collector(cfg, route=route).run(
            Goal(text="ports", seeds=("https://example.org/start",)),
            scope(),
            run_id="complete",
        )
    )
    report = read_source_work(cfg, "complete")
    processed = tuple(item for item in report.operations if item.state == "processed")
    assert len(processed) == len(route.requests) == 2
    # The next frontier candidate is durably refused before a third request.
    assert len(report.operations) == 3
    assert report.operations[-1].reason == "budget_exhausted"
    assert not report.writer_active and not report.unresolved
    assert tuple(item.result for item in processed) == result.documents
    for item in processed:
        assert item.state == "processed"
        assert item.page.body == item.result.raw
        assert item.page.final_url == item.result.url
        assert item.ledger_end <= len(result.ledger)
        assert item.started_at > 0 and item.acquired_at > 0 and item.finished_at > 0
    original = tmp_path / "runs" / "complete" / "source-work" / "operations.sqlite"
    before = original.read_bytes()
    configured_path = tmp_path / "config.json"
    configured_path.write_text(cfg.model_dump_json())
    executed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.source_work import read_source_work
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
print(read_source_work(cfg, 'complete').model_dump_json())
""",
            str(configured_path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert type(report).model_validate_json(executed.stdout) == report
    assert original.read_bytes() == before
    assert original.stat().st_mode & 0o777 == 0o600
    assert original.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("phase", ["fetching", "processing", "after_result"])
def test_real_process_death_retains_precise_unresolved_phase_and_completed_prefix(tmp_path, phase):
    cfg = configured(tmp_path)
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
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
phase = sys.argv[2]
class Route(FakeRoute):
    async def attempt(self, request):
        if phase == 'fetching' or (phase == 'after_result' and self.requests):
            os._exit(23)
        return await super().attempt(request)
class Extractor(FakeExtractor):
    async def extract(self, page):
        if phase == 'processing':
            os._exit(23)
        return await super().extract(page)
loop = GoalLoop(config=cfg, fetcher=FetchLadder((Route(),)), extractor=Extractor(),
                scorer=KeywordScorer(), judge=FakeJudge())
asyncio.run(loop.run(Goal(text='ports', seeds=('https://example.org/start',)),
    Scope(allowed_hosts=('example.org',), max_depth=3, content_types=('text/html',)),
    run_id='crashed'))
""",
            str(path),
            phase,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert executed.returncode == 23, executed.stderr
    journal = read_journal(cfg.journal, "crashed")
    report = read_source_work(cfg, "crashed")
    assert journal.state == "unsealed" and not report.writer_active
    assert len(report.unresolved) == 1
    pending = report.unresolved[0]
    assert pending.ledger_end is None and pending.result is None
    if phase == "processing":
        assert pending.state == "processing" and pending.page.body.endswith(b"/start")
        assert sum(row.event == "fetch" for row in journal.rows) == 1
    else:
        assert pending.state == "fetching" and pending.page is None
        if phase == "after_result":
            completed = report.operations[0]
            assert completed.state == "processed" and completed.result.raw == completed.page.body
            assert sum(row.event == "fetch" for row in journal.rows) == 1
        else:
            # A lost acquisition acknowledgement is unknown spend, not a
            # manufactured zero-byte successful receipt or automatic retry.
            assert journal.rows == ()
    with pytest.raises(ValueError, match="explicit reconciliation"):
        SourceWorkStore.resume(cfg, "crashed", len(journal.rows))
    assert read_source_work(cfg, "crashed") == report


def test_live_inspection_does_not_declare_active_work_dead_or_steal_writer(tmp_path):
    cfg = configured(tmp_path)
    loop = collector(cfg)
    session = asyncio.run(loop.open(Goal(text="ports"), run_id="live"))
    try:
        token = session.source_work.begin(
            SourceRequest(
                url="https://example.org/start",
                scope=scope(),
                depth=0,
                reference_hops=0,
                reference_origin=None,
            ),
            0,
        )
        report = read_source_work(cfg, "live")
        assert report.writer_active and report.operations[0].operation_id == token.operation_id
        assert not report.unresolved
        with pytest.raises(BlockingIOError):
            SourceWorkStore.resume(cfg, "live", 0)
    finally:
        session.close()
    assert len(read_source_work(cfg, "live").unresolved) == 1


def test_persistence_capacity_is_checked_before_another_source_call(tmp_path):
    cfg, route = configured(tmp_path, max_operations=1), FakeRoute()
    with pytest.raises(SourceWorkFailure):
        asyncio.run(
            collector(cfg, route=route).run(
                Goal(text="ports", seeds=("https://example.org/start",)),
                scope(),
                run_id="capacity",
            )
        )
    assert len(route.requests) == 1
    report = read_source_work(cfg, "capacity")
    assert not report.writer_active and report.operations[0].state == "processed"
    assert report.operations[0].result is not None
    assert read_journal(cfg.journal, "capacity").state == "unsealed"


def test_storage_failure_after_acquisition_is_fatal_and_never_acknowledged(tmp_path):
    cfg, route = configured(tmp_path, max_page_bytes=1), FakeRoute()
    with pytest.raises(SourceWorkFailure):
        asyncio.run(
            collector(cfg, route=route).run(
                Goal(text="ports", seeds=("https://example.org/start",)),
                scope(),
                run_id="too-big",
            )
        )
    assert len(route.requests) == 1
    pending = read_source_work(cfg, "too-big").unresolved[0]
    assert pending.state == "fetching" and pending.page is None
    assert sum(row.bytes_read for row in read_journal(cfg.journal, "too-big").rows) > 0


def test_acquired_original_is_durable_before_extractor_runs(tmp_path):
    cfg = configured(tmp_path)
    seen = []

    class InspectExtractor(FakeExtractor):
        async def extract(self, page):
            report = read_source_work(cfg, "ordered")
            operation = report.operations[-1]
            assert report.writer_active and operation.state == "processing"
            assert operation.page == page
            seen.append(page)
            return await super().extract(page)

    asyncio.run(
        collector(cfg, extractor=InspectExtractor()).run(
            Goal(text="ports", seeds=("https://example.org/start",)),
            scope(),
            run_id="ordered",
        )
    )
    assert len(seen) == 2


def test_terminal_refusal_keeps_original_and_reason_without_accepted_result(tmp_path):
    cfg = configured(tmp_path)

    class RefusingExtractor(FakeExtractor):
        async def extract(self, page):
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)

    result = asyncio.run(
        collector(cfg, extractor=RefusingExtractor()).run(
            Goal(text="ports", seeds=("https://example.org/start",)),
            scope(),
            run_id="refused",
        )
    )
    item = read_source_work(cfg, "refused").operations[0]
    assert item.state == "refused" and item.reason == "extraction_failed"
    assert item.page.body and item.result is None and not result.documents


@pytest.mark.parametrize("mutation", ["payload", "pin", "recipe", "symlink", "journal", "tail"])
def test_changed_or_unbound_evidence_refuses_without_external_work(tmp_path, mutation):
    cfg = configured(tmp_path)
    result = asyncio.run(
        collector(cfg).run(
            Goal(text="ports", seeds=("https://example.org/start",)),
            scope(),
            run_id="changed",
        )
    )
    root = tmp_path / "runs" / "changed"
    path = root / "source-work" / "operations.sqlite"
    if mutation in {"payload", "pin"}:
        with sqlite3.connect(path) as db:
            db.execute(
                "UPDATE operations SET " + ("payload=?" if mutation == "payload" else "sha256=?"),
                (b"{}" if mutation == "payload" else "0" * 64,),
            )
    elif mutation == "recipe":
        cfg = configured(tmp_path, max_operations=21)
    elif mutation == "symlink":
        saved = root / "moved.sqlite"
        path.rename(saved)
        path.symlink_to(saved)
    elif mutation == "tail":
        with sqlite3.connect(path) as db:
            db.execute(
                "DELETE FROM operations WHERE sequence=(SELECT MAX(sequence) FROM operations)"
            )
    else:
        (root / "ledger.jsonl").write_bytes(b"")
    with pytest.raises((ValueError, GhimeraRefused, OSError)):
        read_source_work(cfg, "changed")
    assert result.documents


def test_parallel_sources_keep_their_own_originals_and_can_finish_out_of_order(tmp_path):
    async def run():
        cfg = configured(tmp_path)
        cfg = type(cfg).model_validate(dict(cfg.model_dump(), execution=execution_policy()))
        first_started, second_finished = asyncio.Event(), asyncio.Event()

        class Extractor(LeafExtractor):
            async def extract(self, page):
                if page.url.endswith("/a"):
                    first_started.set()
                    await second_finished.wait()
                    observed = read_source_work(cfg, "parallel")
                    assert (
                        next(
                            item for item in observed.operations if item.request.url.endswith("/b")
                        ).state
                        == "processed"
                    )
                else:
                    await first_started.wait()
                    second_finished.set()
                return await super().extract(page)

        result = await asyncio.wait_for(
            collector(cfg, extractor=Extractor()).run(
                Goal(text="ports", seeds=("https://example.org/a", "https://example.org/b")),
                scope(),
                run_id="parallel",
            ),
            timeout=5,
        )
        report = read_source_work(cfg, "parallel")
        assert not report.unresolved and not report.writer_active
        assert len(result.documents) == len(report.operations) == 2
        for item in report.operations:
            assert item.state == "processed" and item.page.body == item.result.raw
            assert item.request.url == item.result.url
        assert {item.result.url for item in report.operations} == {
            doc.url for doc in result.documents
        }

    asyncio.run(run())


def test_cancelled_work_drains_and_releases_owner_without_losing_the_original(tmp_path):
    async def run():
        cfg, started = configured(tmp_path), asyncio.Event()

        class Extractor(LeafExtractor):
            async def extract(self, page):
                started.set()
                await asyncio.Future()

        task = asyncio.create_task(
            collector(cfg, extractor=Extractor()).run(
                Goal(text="ports", seeds=("https://example.org/start",)),
                scope(),
                run_id="cancelled",
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        report = read_source_work(cfg, "cancelled")
        item = report.operations[0]
        assert not report.writer_active and not report.unresolved
        assert item.state == "cancelled" and item.page.body and item.result is None
        assert item.reason == "source_processing_cancelled"
        assert read_journal(cfg.journal, "cancelled").state == "unsealed"

    asyncio.run(run())


def test_round_checkpoint_reopens_quiescent_source_owner_without_refetch(tmp_path):
    from tests.test_research_continuation import assemble, suspend
    from tests.test_research_continuation import configured as checkpoint_config

    cfg = checkpoint_config(tmp_path)
    cfg = type(cfg).model_validate(
        dict(cfg.model_dump(), source_work=configured(tmp_path).source_work)
    )
    first, route, _, _ = assemble(cfg)
    receipt = suspend(first)
    before = read_source_work(cfg, "research")
    assert not before.writer_active and len(before.operations) == len(route.requests) == 1
    resumed, new_route, new_search, _ = assemble(cfg)
    result = asyncio.run(resumed.resume("research", checkpoint_sha256=receipt.sha256))
    after = read_source_work(cfg, "research")
    assert not new_route.requests and not new_search.requests
    assert result.status == "answered" and after == before
    with pytest.raises(ValueError, match="unsealed"):
        SourceWorkStore.resume(cfg, "research", len(result.harvest.ledger))


def test_unacknowledged_source_cannot_be_sealed_and_tokens_cannot_cross_runs(tmp_path):
    cfg = configured(tmp_path)
    loop = collector(cfg)
    first = asyncio.run(loop.open(Goal(text="ports"), run_id="first"))
    second = asyncio.run(loop.open(Goal(text="ports"), run_id="second"))
    request = SourceRequest(
        url="https://example.org/start",
        scope=scope(),
        depth=0,
        reference_hops=0,
        reference_origin=None,
    )
    try:
        token = first.source_work.begin(request, 0)
        second.source_work.begin(request, 0)
        with pytest.raises(SourceWorkFailure, match="another run"):
            second.source_work.processing(token)
        with pytest.raises(SourceWorkFailure, match="unacknowledged"):
            first.checkpoint_state()
        with pytest.raises(SourceWorkFailure, match="unacknowledged"):
            loop.finish(first, "failed")
        assert read_journal(cfg.journal, "first").rows == ()
    finally:
        first.close()
        second.close()


def test_inspection_command_has_safe_bounded_output_and_no_module_warning(tmp_path):
    cfg = configured(tmp_path)
    asyncio.run(
        collector(cfg).run(
            Goal(text="ports", seeds=("https://example.org/start",)), scope(), run_id="cli"
        )
    )
    path = tmp_path / "collector.toml"
    path.write_text(
        Path("examples/chimera.toml").read_text()
        + "\n[journal]\n"
        + "\n".join(
            f"{key} = {json.dumps(value)}"
            for key, value in cfg.journal.model_dump(mode="json").items()
        )
        + "\n"
        + Path("examples/source-work.toml").read_text()
    )
    # Use the actual candidate's policy, not different sample capacities.
    text = path.read_text()
    text = (
        text[: text.index("[source_work]")]
        + "[source_work]\n"
        + "\n".join(
            f"{key} = {json.dumps(value)}"
            for key, value in cfg.source_work.model_dump(mode="json").items()
        )
    )
    # The effective collection recipe is also exact (the fixture page budget differs).
    path.write_text(text.replace("page_budget = 400", "page_budget = 2"))
    executed = subprocess.run(
        [sys.executable, "-m", "ghimera.source_work", "--config", str(path), "--run-id", "cli"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert executed.returncode == 0, executed.stderr
    assert executed.stdout == "run_id=cli operations=3 writer_active=False unresolved=0\n"
    assert (
        executed.stderr == "" and "body" not in executed.stdout and "https" not in executed.stdout
    )


def test_closed_session_cannot_launch_more_sources(tmp_path):
    cfg, route = configured(tmp_path), FakeRoute()
    loop = collector(cfg, route=route)
    session = asyncio.run(loop.open(Goal(text="ports"), run_id="closed"))
    session.close()
    with pytest.raises(GhimeraRefused):
        asyncio.run(loop.collect(session, scope(), ("https://example.org/start",)))
    assert not route.requests


def test_command_storage_failure_is_bounded_and_never_echoes_exception_data(
    tmp_path, monkeypatch, capsys
):
    from ghimera import command
    from tests.test_collector_command import options, toml_lines

    opts = options(tmp_path, configured(tmp_path))
    path = tmp_path / "job.toml"
    path.write_text("\n".join(toml_lines(opts.model_dump(mode="json"))))

    async def failed(options):
        raise SourceWorkFailure("PRIVATE-INPUT-VALUE")

    monkeypatch.setattr(command, "execute", failed)
    assert command.main(["--job", str(path), "--max-job-bytes", "100000"]) == 2
    output = capsys.readouterr()
    assert not output.out and output.err == "command_source_work_failed\n"
