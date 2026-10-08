"""Acquisition-only protocol witnesses; no claim of model/source quality."""

import asyncio
import hashlib
import sqlite3
import subprocess
import sys
import threading
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.documents import DOCX_TYPE, DocumentExtractor
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.journal import read_journal
from ghimera.loop import GoalLoop
from ghimera.research import ResearchLoop
from ghimera.research_recovery_config import ResearchRecoveryConfig
from ghimera.research_types import ResearchRequest
from ghimera.source_work import SourceWorkFailure, SourceWorkStore, read_source_work
from tests.test_document_extraction import docx
from tests.test_intent_research import (
    AnalystFixture,
    PlannerFixture,
    ReviewerFixture,
    SearchFixture,
)
from tests.test_local_inputs import seed
from tests.test_local_source_work import configured as native_configured


def configured(tmp_path):
    raw = native_configured(tmp_path).model_dump()
    raw["wall_seconds"] = 300
    raw["judge_budget"] = 20
    raw["journal"]["max_record_bytes"] = 2000000
    raw["journal"]["max_journal_bytes"] = 40000000
    raw["research"]["max_rounds"] = 1
    raw["research"]["max_pages_per_round"] = 1
    raw["research"]["content_types"] = [DOCX_TYPE]
    raw["source_work"]["frontier"] = dict(
        schema="ghimera.source-frontier/1",
        max_entries=20,
        max_entry_bytes=20000,
        max_frontier_bytes=200000,
    )
    raw["model_work"] = dict(
        schema="ghimera.model-work/1",
        max_input_bytes=4000000,
        max_unanswered_calls=2,
        uncertain_policy="hold",
        results=dict(
            schema="ghimera.model-results/1",
            max_result_bytes=1000000,
            max_total_result_bytes=20000000,
        ),
    )
    raw["research_recovery"] = dict(
        schema="ghimera.research-recovery/5",
        max_snapshot_bytes=4000000,
        clock_policy="include_downtime",
        tail_policy="acknowledged_model_return",
        source_acquisition=dict(
            schema="ghimera.source-acquisition-recovery/1",
            execution="serial",
            max_capsule_bytes=4000000,
        ),
    )
    graph = tomllib.loads(Path("examples/research-graph.toml").read_text())
    graph["sink_path"] = str(tmp_path / "graph")
    raw["graph"] = graph
    return GhimeraConfig.model_validate(raw)


class DocumentRoute(FakeRoute):
    async def attempt(self, request):
        from ghimera.models import Page

        self.requests.append(request)
        return Page(
            url=request.url, final_url=request.url, status=200, content_type=DOCX_TYPE, body=docx()
        )


def researcher(cfg):
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((DocumentRoute(),)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    return ResearchLoop(
        config=cfg,
        collector=loop,
        search=SearchFixture(),
        planner=PlannerFixture(),
        analyst=AnalystFixture(),
        reviewer=ReviewerFixture(),
    )


ENTRY = """
import asyncio, json, os, sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.research_types import ResearchRequest
from ghimera.source_work import SourceWorkStore
from tests.test_source_acquisition_recovery import researcher, DocumentRoute
cfg=GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
request=ResearchRequest.model_validate_json(Path(sys.argv[2]).read_bytes())
if sys.argv[3]=='die':
    original=SourceWorkStore.acquired
    def acquired(self, token, page, **kwargs):
        original(self, token, page, **kwargs)
        if kwargs.get('control') is not None:
            os._exit(79)
    SourceWorkStore.acquired=acquired
    asyncio.run(researcher(cfg).run(request, run_id='cut'))
else:
    async def resume():
        cut=SourceWorkStore.acquisition(cfg, 'cut')
        original=DocumentRoute.attempt
        async def guarded(self, request):
            if request.url == cut.operation.request.url:
                raise AssertionError('original source was contacted again')
            return await original(self, request)
        DocumentRoute.attempt=guarded
        result=await researcher(cfg).recover('cut', snapshot_sha256=cut.sha256,
                                             boundary='source_acquisition')
        Path(sys.argv[4]).write_text(result.model_dump_json())
    asyncio.run(resume())
"""


def crash(tmp_path, kind):
    cfg = configured(tmp_path)
    path = tmp_path / "owned.docx"
    raw = docx()
    path.write_bytes(raw)
    request = (
        ResearchRequest(intent="find ports", local_documents=(seed(path, raw),))
        if kind == "local"
        else ResearchRequest(intent="find ports", seeds=("https://example.org/one",))
    )
    config_path, request_path = tmp_path / "config.json", tmp_path / "request.json"
    config_path.write_text(cfg.model_dump_json())
    request_path.write_text(request.model_dump_json())
    stopped = subprocess.run(
        [sys.executable, "-c", ENTRY, str(config_path), str(request_path), "die"],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert stopped.returncode == 79, stopped.stderr
    return cfg, request, config_path, request_path, path


@pytest.mark.parametrize("kind", ["web", "local"])
def test_fresh_acquired_bytes_continue_native_parser_graph_without_reacquisition(tmp_path, kind):
    from ghimera.research_types import ResearchResult

    cfg, request, config_path, request_path, path = crash(tmp_path, kind)
    cut = SourceWorkStore.acquisition(cfg, "cut", expected_request=request)
    original = cut.operation.page
    assert cut.operation.state == "acquired" and original is not None
    assert cut.snapshot.progress.harvest.documents == ()
    # The owned input is no longer available. Recovery must use retained bytes.
    if kind == "local":
        path.unlink()
    output = tmp_path / "result.json"
    resumed = subprocess.run(
        [sys.executable, "-c", ENTRY, str(config_path), str(request_path), "resume", str(output)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert resumed.returncode == 0, resumed.stderr
    result = ResearchResult.model_validate_json(output.read_bytes())
    assert result.harvest.documents[0].raw == original.body
    assert result.harvest.documents[0].extracted.document_parse is not None
    assert result.harvest.graph is not None
    if kind == "web":
        assert result.harvest.receipt.fetches == cut.snapshot.progress.harvest.receipt.fetches
        assert result.harvest.receipt.bytes_read == cut.snapshot.progress.harvest.receipt.bytes_read
    else:
        # First-time research after the initial import may acquire other sources.
        assert result.harvest.receipt.fetches >= cut.snapshot.progress.harvest.receipt.fetches
        assert result.harvest.receipt.bytes_read >= cut.snapshot.progress.harvest.receipt.bytes_read
    assert (
        result.harvest.receipt.elapsed_seconds
        >= cut.snapshot.progress.harvest.receipt.elapsed_seconds
    )
    assert result.harvest.ledger[: len(cut.journal.rows)] == cut.journal.rows
    assert sum(row.event == "local_input" for row in result.harvest.ledger) == (kind == "local")
    assert read_source_work(cfg, "cut").operations[0].state == "processed"
    assert read_journal(cfg.journal, "cut").state == "complete"
    with pytest.raises((ValueError, SourceWorkFailure)):
        SourceWorkStore.acquisition(cfg, "cut", cut.sha256)


def test_policy_is_explicit_and_legacy_identity_unchanged(tmp_path):
    cfg = native_configured(tmp_path)
    assert "source_acquisition" not in cfg.model_dump_json()
    configured(tmp_path)
    with pytest.raises(ValidationError):
        raw = configured(tmp_path).model_dump()
        raw["research_recovery"]["schema"] = "ghimera.research-recovery/3"
        GhimeraConfig.model_validate(raw)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_previous_recovery_versions_omit_new_policy_exactly(version):
    raw = dict(
        schema=f"ghimera.research-recovery/{version}",
        max_snapshot_bytes=10000,
        clock_policy="include_downtime",
        tail_policy="acknowledged_model_return",
    )
    if version == 2:
        raw["source_completion"] = dict(
            schema="ghimera.source-completion-recovery/1",
            execution="serial",
            max_capsule_bytes=20000,
        )
    if version == 3:
        raw["model_reconciliation"] = dict(
            schema="ghimera.model-reconciliation-policy/1",
            max_decision_bytes=10000,
            max_decisions_per_run=1,
        )
    policy = ResearchRecoveryConfig.model_validate(raw)
    assert policy.model_dump(mode="json") == raw
    with pytest.raises(ValidationError):
        ResearchRecoveryConfig.model_validate(
            dict(
                raw,
                source_acquisition=dict(
                    schema="ghimera.source-acquisition-recovery/1",
                    execution="serial",
                    max_capsule_bytes=20000,
                ),
            )
        )


def test_reserved_query_policy_is_not_reimplemented_here(tmp_path):
    raw = configured(tmp_path).model_dump()
    raw["research_recovery"]["schema"] = "ghimera.research-recovery/4"
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)


def test_original_request_recipe_runtime_and_live_writer_are_fenced(tmp_path):
    cfg, request, *_ = crash(tmp_path, "web")
    cut = SourceWorkStore.acquisition(cfg, "cut")
    with pytest.raises(ValueError):
        SourceWorkStore.acquisition(cfg, "cut", "0" * 64)
    with pytest.raises(ValueError):
        SourceWorkStore.acquisition(
            cfg, "cut", expected_request=request.model_copy(update={"intent": "different"})
        )
    with pytest.raises(ValueError):
        SourceWorkStore.acquisition(
            cfg,
            "cut",
            expected_runtime=cut.snapshot.runtime.model_copy(
                update={"extractor_revision": "foreign@2"}
            ),
        )
    with pytest.raises(ValueError):
        SourceWorkStore.acquisition(
            cfg,
            "cut",
            expected_models=cut.snapshot.models.model_copy(update={"search_revision": "foreign"}),
        )
    altered = GhimeraConfig.model_validate(dict(cfg.model_dump(), page_budget=cfg.page_budget + 1))
    with pytest.raises(ValueError):
        SourceWorkStore.acquisition(altered, "cut")
    writer = SourceWorkStore.resume(cfg, "cut", len(cut.journal.rows), acquisition=cut)
    try:
        with pytest.raises(BlockingIOError):
            SourceWorkStore.acquisition(cfg, "cut", cut.sha256)
    finally:
        writer.close()


def test_later_journal_prevents_adoption_before_native_processing(tmp_path):
    from ghimera.journal import DirectoryLedgerSink
    from ghimera.models import LedgerRow

    cfg, *_ = crash(tmp_path, "web")
    cut = SourceWorkStore.acquisition(cfg, "cut")
    h = cut.snapshot.progress.harvest
    sink = DirectoryLedgerSink(cfg, "cut", h.goal, h.receipt.judge, resume_rows=cut.journal.rows)
    try:
        sink.append(
            LedgerRow(sequence=len(cut.journal.rows), event="duplicate", reason="later_work")
        )
    finally:
        sink.close()
    with pytest.raises(ValueError):
        SourceWorkStore.acquisition(cfg, "cut", cut.sha256)


def test_control_capacity_failure_cannot_acknowledge_or_refetch(tmp_path):
    cfg = configured(tmp_path)
    raw = cfg.model_dump()
    raw["research_recovery"]["source_acquisition"]["max_capsule_bytes"] = 1
    cfg = GhimeraConfig.model_validate(raw)
    loop = researcher(cfg)
    request = ResearchRequest(intent="find ports", seeds=("https://example.org/one",))
    with pytest.raises(SourceWorkFailure):
        asyncio.run(loop.run(request, run_id="atomic"))
    work = read_source_work(cfg, "atomic")
    assert len(work.operations) == 1
    assert work.operations[0].state == "fetching" and work.operations[0].page is None
    assert (
        sum(row.event == "source_acquisition" for row in read_journal(cfg.journal, "atomic").rows)
        == 1
    )
    with pytest.raises(ValueError, match="no atomic"):
        SourceWorkStore.acquisition(cfg, "atomic")


def test_wrong_retained_page_cannot_use_coherently_rehashed_control(tmp_path):
    cfg, *_ = crash(tmp_path, "web")
    cut = SourceWorkStore.acquisition(cfg, "cut")
    altered_page = cut.operation.page.model_copy(update={"body": b"foreign original"})
    operation = cut.operation.model_copy(update={"page": altered_page})
    payload = operation.model_dump_json().encode()
    operation_pin = hashlib.sha256(payload).hexdigest()
    snapshot = cut.snapshot.model_copy(update={"operation_sha256": operation_pin})
    control = snapshot.model_dump_json().encode()
    database = cfg.journal.directory / "cut" / "source-work" / "operations.sqlite"
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE operations SET payload=?,sha256=? WHERE id=?",
            (payload, operation_pin, cut.operation.operation_id),
        )
        db.execute(
            "UPDATE source_acquisition SET payload=?,sha256=? WHERE id=1",
            (control, hashlib.sha256(control).hexdigest()),
        )
    with pytest.raises(ValueError, match="exact operation/request/Page"):
        SourceWorkStore.acquisition(cfg, "cut")


def test_later_graph_refuses_before_parser_judge_or_external_contact(tmp_path, monkeypatch):
    from ghimera.graph import DirectoryGraphSink, ResearchGraph

    cfg, *_ = crash(tmp_path, "web")
    cut = SourceWorkStore.acquisition(cfg, "cut")

    async def alter():
        graph = ResearchGraph(cfg.graph, "cut", DirectoryGraphSink(cfg.graph, "cut"))
        await graph.start("find ports")
        await graph.discovered("https://example.org/later", graph.intent_id)

    asyncio.run(alter())

    async def denied(*args, **kwargs):
        raise AssertionError("no call may start after graph drift")

    monkeypatch.setattr(DocumentExtractor, "extract", denied)
    monkeypatch.setattr(DocumentRoute, "attempt", denied)
    monkeypatch.setattr(FakeJudge, "document", denied)
    with pytest.raises(ValueError, match="graph changed"):
        asyncio.run(
            researcher(cfg).recover(
                "cut", snapshot_sha256=cut.sha256, boundary="source_acquisition"
            )
        )


def test_downtime_consumes_original_wall_budget_without_new_contact(tmp_path, monkeypatch):
    cfg, *_ = crash(tmp_path, "web")
    cut = SourceWorkStore.acquisition(cfg, "cut")
    monkeypatch.setattr("ghimera.research.time.time", lambda: cut.snapshot.saved_at + 301)

    async def denied(*args, **kwargs):
        raise AssertionError("no call after original wall budget expires")

    monkeypatch.setattr(DocumentExtractor, "extract", denied)
    monkeypatch.setattr(DocumentRoute, "attempt", denied)
    result = asyncio.run(
        researcher(cfg).recover("cut", snapshot_sha256=cut.sha256, boundary="source_acquisition")
    )
    assert result.stop_reason == "budget_exhausted" and not result.harvest.documents
    assert result.harvest.receipt.elapsed_seconds >= 301
    assert result.harvest.receipt.fetches == cut.snapshot.progress.harvest.receipt.fetches
    assert result.harvest.receipt.bytes_read == cut.snapshot.progress.harvest.receipt.bytes_read


def test_drained_known_cancellation_cannot_publish_a_resumable_cut(tmp_path, monkeypatch):
    from ghimera.local_inputs import LocalInputLoader
    from ghimera.models import Goal

    cfg = configured(tmp_path)
    raw = docx()
    path = tmp_path / "cancelled.docx"
    path.write_bytes(raw)
    started, release = threading.Event(), threading.Event()
    original = LocalInputLoader.read

    def delayed(self, item, *, max_bytes):
        started.set()
        assert release.wait(timeout=3)
        return original(self, item, max_bytes=max_bytes)

    monkeypatch.setattr(LocalInputLoader, "read", delayed)
    loop = researcher(cfg)._collector

    def no_cut(*args):
        raise AssertionError("known cancellation cannot authorize acquisition recovery")

    async def run():
        session = await loop.open(Goal(text="find ports"), run_id="cancelled")
        try:
            task = asyncio.create_task(
                loop.import_local(session, (seed(path, raw),), acquisition_control=no_cut)
            )
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            return loop.finish(session, "failed")
        finally:
            release.set()
            session.close()

    harvest = asyncio.run(run())
    (operation,) = read_source_work(cfg, "cancelled").operations
    assert operation.state == "cancelled" and operation.page.body == raw
    assert harvest.receipt.bytes_read == len(raw) and harvest.receipt.fetches == 0
    assert not any(row.source_acquisition is not None for row in harvest.ledger)
    with pytest.raises(ValueError, match="no atomic"):
        SourceWorkStore.acquisition(cfg, "cancelled")
