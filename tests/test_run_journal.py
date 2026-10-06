"""Real local storage, interruption, bounded writes and goal/research binding."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chimera.config import ChimeraConfig
from chimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from chimera.fetch import FetchLadder
from chimera.journal import DirectoryLedgerSink, read_journal
from chimera.loop import GoalLoop
from chimera.models import Goal, LedgerRow, Scope
from chimera.refusals import ChimeraRefused
from tests.test_c0 import config
from tests.test_intent_research import run as research_run


def configured(tmp_path, **changes):
    journal = dict(
        schema="chimera.run-journal-config/1",
        directory=str(tmp_path / "runs"),
        max_record_bytes=1000000,
        max_journal_bytes=10000000,
        max_summary_bytes=1000000,
        max_records=1000,
    )
    journal.update(changes)
    return config(page_budget=1, journal=journal)


def collector(cfg, route=None):
    return GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route or FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )


def test_real_collection_seals_jsonl_and_summary_and_reading_does_not_mutate(
    tmp_path, monkeypatch, capsys
):
    cfg = configured(tmp_path)
    result = asyncio.run(
        collector(cfg).run(
            Goal(text="ports", seeds=("https://example.org/one",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
            run_id="run-1",
        )
    )
    report = read_journal(cfg.journal, "run-1")
    assert report.state == "complete" and report.rows == result.ledger
    assert report.summary.receipt == result.receipt
    root = tmp_path / "runs" / "run-1"
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    assert read_journal(cfg.journal, "run-1") == report
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in root.iterdir())
    assert len((root / "ledger.jsonl").read_text().splitlines()) == len(result.ledger)
    from chimera.journal import main

    configuration = tmp_path / "collector.toml"
    configuration.write_text(
        Path("examples/chimera.toml").read_text()
        + "\n[journal]\n"
        + "\n".join(
            f"{key} = {json.dumps(value)}"
            for key, value in cfg.journal.model_dump(mode="json", by_alias=True).items()
        )
    )
    monkeypatch.setattr(
        "sys.argv", ["journal", "--config", str(configuration), "--run-id", "run-1"]
    )
    assert main() == 0
    output = capsys.readouterr()
    assert "run_id=run-1 state=complete" in output.out and output.err == ""
    assert result.documents[0].extracted.text not in output.out
    executed = subprocess.run(
        [
            sys.executable,
            "-m",
            "chimera.journal",
            "--config",
            str(configuration),
            "--run-id",
            "run-1",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert executed.stdout == output.out and executed.stderr == ""


def test_interrupted_run_retains_fsynced_prefix_and_is_not_complete(tmp_path):
    cfg = configured(tmp_path)
    sink = DirectoryLedgerSink(cfg, "interrupted", Goal(text="ports"), FakeJudge().model)
    row = LedgerRow(sequence=0, event="policy", reason="first observation")
    sink.append(row)
    sink.close()
    report = read_journal(cfg.journal, "interrupted")
    assert report.state == "unsealed" and report.rows == (row,) and report.summary is None
    assert not report.incomplete_tail
    with pytest.raises(ChimeraRefused, match="ledger_sink_failed"):
        DirectoryLedgerSink(cfg, "interrupted", Goal(text="ports"), FakeJudge().model)


def test_truncated_tail_is_reported_but_never_sealed_or_silently_dropped(tmp_path):
    cfg = configured(tmp_path)
    sink = DirectoryLedgerSink(cfg, "partial", Goal(text="ports"), FakeJudge().model)
    row = LedgerRow(sequence=0, event="policy", reason="retained")
    sink.append(row)
    sink.close()
    path = tmp_path / "runs" / "partial" / "ledger.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"schema":')
    report = read_journal(cfg.journal, "partial")
    assert report.incomplete_tail and report.rows == (row,) and report.state == "unsealed"
    assert path.read_bytes().endswith(b'{"schema":')


def test_changed_reordered_or_missing_events_refuse_complete_read(tmp_path):
    cfg = configured(tmp_path)
    asyncio.run(
        collector(cfg).run(
            Goal(text="ports", seeds=("https://example.org/one",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
            run_id="changed",
        )
    )
    path = tmp_path / "runs" / "changed" / "ledger.jsonl"
    original = path.read_bytes()
    lines = original.splitlines(keepends=True)
    for damaged in (
        b"".join(reversed(lines)),
        b"".join(lines[:-1]),
        original.replace(b'"reason":', b'"unknown":', 1),
        original + b"{",
    ):
        path.write_bytes(damaged)
        with pytest.raises(ChimeraRefused, match="ledger_sink_failed"):
            read_journal(cfg.journal, "changed")
    path.write_bytes(original)
    assert read_journal(cfg.journal, "changed").state == "complete"


def test_unsafe_paths_limits_and_missing_run_identity_refuse_before_fetch(tmp_path):
    cfg = configured(tmp_path)
    route = FakeRoute()
    with pytest.raises(ChimeraRefused, match="ledger_sink_failed"):
        asyncio.run(
            collector(cfg, route).run(
                Goal(text="ports"),
                Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
            )
        )
    assert route.requests == []
    for run_id in ("../escape", "a/b", ""):
        with pytest.raises(ChimeraRefused, match="ledger_sink_failed"):
            DirectoryLedgerSink(cfg, run_id, Goal(text="ports"), FakeJudge().model)
    root = tmp_path / "shared"
    root.mkdir(mode=0o755)
    shared = configured(tmp_path, directory=str(root))
    with pytest.raises(ChimeraRefused, match="ledger_sink_failed"):
        DirectoryLedgerSink(shared, "shared", Goal(text="ports"), FakeJudge().model)
    linked = tmp_path / "linked"
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(ChimeraRefused, match="ledger_sink_failed"):
        DirectoryLedgerSink(
            configured(tmp_path, directory=str(linked)),
            "link",
            Goal(text="ports"),
            FakeJudge().model,
        )
    tiny = configured(tmp_path, max_record_bytes=10)
    with pytest.raises(ChimeraRefused, match="ledger_sink_failed"):
        DirectoryLedgerSink(tiny, "too-small", Goal(text="ports"), FakeJudge().model)


def test_sink_failure_does_not_acknowledge_a_volatile_ledger_row(tmp_path):
    from chimera.ledger import Ledger

    cfg = configured(tmp_path, max_records=1)
    sink = DirectoryLedgerSink(cfg, "bounded", Goal(text="ports"), FakeJudge().model)
    ledger = Ledger(sink=sink)
    ledger.append(LedgerRow(sequence=0, event="policy", reason="first"))
    with pytest.raises(ChimeraRefused, match="ledger_sink_failed"):
        ledger.append(LedgerRow(sequence=1, event="policy", reason="second"))
    assert ledger.next_sequence == 1
    ledger.close()
    assert len(read_journal(cfg.journal, "bounded").rows) == 1
    path = tmp_path / "runs" / "bounded" / "ledger.jsonl"
    os.chmod(path, 0o644)
    with pytest.raises(ChimeraRefused, match="ledger_sink_failed"):
        read_journal(cfg.journal, "bounded")


def test_research_model_and_search_phases_share_the_same_durable_run(tmp_path):
    from tests.test_intent_research import policy

    values = configured(tmp_path).model_dump(by_alias=True)
    values["research"] = policy()
    values["page_budget"] = 30
    result, _, _ = research_run(cfg=ChimeraConfig.model_validate(values), run_id="research")
    report = read_journal(result.harvest.receipt.effective_config.journal, "research")
    assert report.state == "complete" and report.rows == result.harvest.ledger
    assert {"plan", "discovery", "assessment", "answer", "review"} <= {
        row.event for row in report.rows
    }


def test_failed_fsync_is_not_acknowledged_or_retried_by_the_sink(tmp_path, monkeypatch):
    from chimera.ledger import Ledger

    cfg = configured(tmp_path)
    sink = DirectoryLedgerSink(cfg, "io-failure", Goal(text="ports"), FakeJudge().model)
    ledger = Ledger(sink=sink)

    def failed_sync(fd):
        raise OSError("injected disk sync failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", failed_sync)
        with pytest.raises(ChimeraRefused, match="ledger_sink_failed"):
            ledger.append(LedgerRow(sequence=0, event="policy", reason="unacknowledged"))
    assert ledger.snapshot() == ()
    with pytest.raises(ChimeraRefused, match="ledger_sink_failed"):
        ledger.append(LedgerRow(sequence=0, event="policy", reason="not silently retried"))
    report = read_journal(cfg.journal, "io-failure")
    assert report.state == "unsealed" and len(report.rows) == 1
