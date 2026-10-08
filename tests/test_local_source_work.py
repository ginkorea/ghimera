"""Owned document capture survives process loss without manufacturing HTTP work."""

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
from ghimera.documents import DocumentExtractor
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.graph import MemoryGraphSink
from ghimera.journal import read_journal
from ghimera.local_inputs import LocalInputAcknowledgementLost, LocalInputLoader
from ghimera.loop import GoalLoop
from ghimera.models import Goal
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.source_work import SourceWorkFailure, SourceWorkStore, read_source_work
from ghimera.source_work_types import LocalSourceRequest, SourceOperation, SourceRequest
from tests.test_document_extraction import docx, native_pdf
from tests.test_local_inputs import configured as local_configured
from tests.test_local_inputs import seed
from tests.test_source_work import configured as work_configured


def configured(tmp_path, **updates):
    data = local_configured(tmp_path).model_dump()
    work = work_configured(tmp_path).model_dump()
    data.update(journal=work["journal"], source_work=work["source_work"])
    data["source_work"].update(updates)
    return GhimeraConfig.model_validate(data)


def collector(cfg, *, extractor=None, route=None):
    return GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route or FakeRoute(),)),
        extractor=extractor or DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )


@pytest.mark.parametrize(
    "mime",
    ("application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
)
def test_native_local_result_and_original_are_retained_without_web_frontier(tmp_path, mime):
    raw = native_pdf() if mime == "application/pdf" else docx()
    path = tmp_path / "owner-document"
    path.write_bytes(raw)
    cfg = configured(
        tmp_path,
        frontier=dict(
            schema="ghimera.source-frontier/1",
            max_entries=20,
            max_entry_bytes=10000,
            max_frontier_bytes=100000,
        ),
    )
    route = FakeRoute()
    loop = collector(cfg, route=route)

    async def run():
        session = await loop.open(Goal(text="ports"), run_id="local")
        try:
            await loop.import_local(session, (seed(path, raw, content_type=mime),))
            return loop.finish(session, "frontier_empty")
        finally:
            session.close()

    harvest = asyncio.run(run())
    report = read_source_work(cfg, "local")
    assert not route.requests and not report.queued and not report.queued_local
    assert len(report.frontier) == 1 and not report.unresolved
    assert isinstance(report.frontier[0].request, LocalSourceRequest)
    assert harvest.receipt.fetches == 0 and harvest.receipt.bytes_read == len(raw)
    (operation,) = report.operations
    assert isinstance(operation.request, LocalSourceRequest)
    assert operation.request.seed.path == path
    assert operation.request.policy_digest == cfg.local_inputs.content_digest()
    assert operation.state == "processed" and operation.page.body == raw
    assert operation.result == harvest.documents[0]
    assert operation.page.local_input == operation.result.local_input
    assert str(path) not in operation.result.model_dump_json()
    assert SourceOperation.model_validate_json(operation.model_dump_json()) == operation
    assert sum(row.event == "local_input" for row in harvest.ledger) == 1
    assert not any(row.event == "fetch" for row in harvest.ledger)
    assert operation.ledger_end <= len(harvest.ledger)


@pytest.mark.parametrize("phase", ("reading", "processing", "after_result"))
def test_real_process_death_preserves_local_phase_original_and_known_spend(tmp_path, phase):
    raw = docx()
    path = tmp_path / "report.docx"
    path.write_bytes(raw)
    path2 = tmp_path / "report-copy.docx"
    path2.write_bytes(raw)
    cfg = configured(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(cfg.model_dump_json())
    executed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, hashlib, os, sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.documents import DocumentExtractor
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.local_input_types import LocalDocumentSeed
from ghimera.local_inputs import LocalInputLoader
from ghimera.loop import GoalLoop
from ghimera.models import Goal
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
phase = sys.argv[2]
paths = tuple(Path(p) for p in sys.argv[3:])
original = LocalInputLoader.read
reads = 0
def read(self, seed, *, max_bytes):
    global reads
    # Verify that the physical read can succeed; then lose its acknowledgement.
    snapshot = original(self, seed, max_bytes=max_bytes)
    if phase == 'reading' or (phase == 'after_result' and reads):
        os._exit(23)
    reads += 1
    return snapshot
LocalInputLoader.read = read
class Extractor(DocumentExtractor):
    async def extract(self, page):
        if phase == 'processing':
            os._exit(23)
        return await super().extract(page)
seeds = tuple(LocalDocumentSeed(path=p, sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
    content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    for p in paths)
loop = GoalLoop(config=cfg, fetcher=FetchLadder((FakeRoute(),)), extractor=Extractor(cfg),
                scorer=KeywordScorer(), judge=FakeJudge())
async def run():
    session = await loop.open(Goal(text='ports'), run_id='crashed-local')
    await loop.import_local(session, seeds)
asyncio.run(run())
""",
            str(config_path),
            phase,
            str(path),
            str(path2),
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert executed.returncode == 23, executed.stderr
    journal = read_journal(cfg.journal, "crashed-local")
    report = read_source_work(cfg, "crashed-local")
    (pending,) = report.unresolved
    assert not report.writer_active and journal.state == "unsealed"
    assert pending.ledger_end is None
    assert isinstance(pending.request, LocalSourceRequest)
    if phase == "processing":
        assert pending.state == "processing" and pending.page.body == raw
        assert sum(row.bytes_read for row in journal.rows) == len(raw)
    else:
        assert pending.state == "fetching" and pending.page is None
        if phase == "after_result":
            assert report.operations[0].state == "processed"
            assert report.operations[0].result.raw == raw
            assert sum(row.bytes_read for row in journal.rows) == len(raw)
        else:
            # The process read bytes but did not acknowledge them. Unknown is not zero.
            assert journal.rows == ()
    assert not any(row.event == "fetch" for row in journal.rows)
    with pytest.raises(ValueError, match="explicit reconciliation"):
        SourceWorkStore.resume(cfg, "crashed-local", len(journal.rows))
    assert read_source_work(cfg, "crashed-local") == report


@pytest.mark.parametrize("cancel_count", (1, 3))
def test_cancelled_read_keeps_drained_original_and_exact_native_byte_spend(
    tmp_path, monkeypatch, cancel_count
):
    raw = docx()
    path = tmp_path / "report.docx"
    path.write_bytes(raw)
    cfg, started, release = configured(tmp_path), threading.Event(), threading.Event()
    original = LocalInputLoader.read

    def delayed(self, item, *, max_bytes):
        started.set()
        assert release.wait(timeout=3)
        return original(self, item, max_bytes=max_bytes)

    monkeypatch.setattr(LocalInputLoader, "read", delayed)
    loop = collector(cfg)

    async def run():
        session = await loop.open(Goal(text="ports"), run_id="cancel-local")
        try:
            task = asyncio.create_task(loop.import_local(session, (seed(path, raw),)))
            assert await asyncio.to_thread(started.wait, 2)
            for _ in range(cancel_count):
                task.cancel()
                # Deliver each cancellation separately while the reader is held.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert session.budget._bytes_reserved == 0
            return loop.finish(session, "failed")
        finally:
            session.close()

    harvest = asyncio.run(run())
    (operation,) = read_source_work(cfg, "cancel-local").operations
    assert operation.state == "cancelled" and operation.result is None
    assert operation.page.body == raw and operation.page.local_input is not None
    assert harvest.receipt.bytes_read == len(raw)
    assert harvest.receipt.fetches == harvest.receipt.judge_calls == 0
    assert not read_source_work(cfg, "cancel-local").unresolved


def test_capacity_refuses_local_read_before_any_physical_io(tmp_path, monkeypatch):
    cfg = configured(tmp_path, max_operations=1)
    raw = docx()
    path, path2 = tmp_path / "one.docx", tmp_path / "two.docx"
    path.write_bytes(raw)
    path2.write_bytes(raw)
    reads = []
    original = LocalInputLoader.read

    def counted(self, item, *, max_bytes):
        reads.append(item.path)
        return original(self, item, max_bytes=max_bytes)

    monkeypatch.setattr(LocalInputLoader, "read", counted)
    loop = collector(cfg)

    async def run():
        session = await loop.open(Goal(text="ports"), run_id="capacity-local")
        try:
            await loop.import_local(session, (seed(path, raw), seed(path2, raw)))
        finally:
            session.close()

    with pytest.raises(SourceWorkFailure):
        asyncio.run(run())
    assert reads == [path]
    (operation,) = read_source_work(cfg, "capacity-local").operations
    assert operation.state == "processed" and operation.result.raw == raw


def test_lost_reader_task_acknowledgement_is_not_a_false_zero_byte_cancellation(
    tmp_path, monkeypatch
):
    raw = docx()
    path = tmp_path / "report.docx"
    path.write_bytes(raw)
    cfg = configured(tmp_path)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_read, original_create = LocalInputLoader.read, asyncio.create_task
    readers = []

    def delayed(self, item, *, max_bytes):
        started.set()
        try:
            assert release.wait(timeout=3)
            return original_read(self, item, max_bytes=max_bytes)
        finally:
            finished.set()

    def record_task(coroutine, **kwargs):
        task = original_create(coroutine, **kwargs)
        readers.append(task)
        return task

    monkeypatch.setattr(LocalInputLoader, "read", delayed)
    monkeypatch.setattr(asyncio, "create_task", record_task)
    loop = collector(cfg)

    async def run():
        session = await loop.open(Goal(text="ports"), run_id="lost-reader")
        try:
            task = original_create(loop.import_local(session, (seed(path, raw),)))
            assert await asyncio.to_thread(started.wait, 2)
            assert len(readers) == 1
            readers[0].cancel()
            with pytest.raises(LocalInputAcknowledgementLost):
                await task
            # The physical thread can still read; release it explicitly in this
            # controlled witness. Its lost result must not fabricate native spend.
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)
            with pytest.raises(SourceWorkFailure, match="unacknowledged"):
                loop.finish(session, "failed")
        finally:
            release.set()
            session.close()

    asyncio.run(run())
    (operation,) = read_source_work(cfg, "lost-reader").unresolved
    assert operation.state == "fetching" and operation.page is None
    assert operation.reason is None and operation.ledger_end is None
    assert read_journal(cfg.journal, "lost-reader").rows == ()


def test_pin_failure_retains_actual_read_bytes_but_not_a_false_original(tmp_path):
    raw = docx()
    path = tmp_path / "changed.docx"
    path.write_bytes(raw)
    cfg = configured(tmp_path)
    loop = collector(cfg)

    async def run():
        session = await loop.open(Goal(text="ports"), run_id="changed-local")
        try:
            with pytest.raises(GhimeraRefused) as refusal:
                await loop.import_local(session, (seed(path, raw, sha256="0" * 64),))
            assert refusal.value.code == RefusalCode.LOCAL_INPUT_FAILED
            return loop.finish(session, "failed")
        finally:
            session.close()

    harvest = asyncio.run(run())
    (operation,) = read_source_work(cfg, "changed-local").operations
    assert operation.state == "refused" and operation.page is None
    assert operation.reason == RefusalCode.LOCAL_INPUT_FAILED.value
    assert harvest.receipt.bytes_read == len(raw)


def test_local_request_policy_and_original_cannot_be_rebound(tmp_path):
    raw = docx()
    path = tmp_path / "document.docx"
    path.write_bytes(raw)
    cfg = configured(tmp_path)
    loop = collector(cfg)

    async def run():
        session = await loop.open(Goal(text="ports"), run_id="binding-local")
        try:
            await loop.import_local(session, (seed(path, raw),))
            return loop.finish(session, "frontier_empty")
        finally:
            session.close()

    asyncio.run(run())
    (operation,) = read_source_work(cfg, "binding-local").operations
    for field, replacement in (("policy_digest", "0" * 64),):
        broken = operation.model_dump()
        broken["request"][field] = replacement
        with pytest.raises(ValidationError, match="pinned input"):
            SourceOperation.model_validate(broken)
    broken = operation.model_dump()
    broken["request"]["seed"]["content_type"] = "application/pdf"
    with pytest.raises(ValidationError, match="pinned input"):
        SourceOperation.model_validate(broken)
    with pytest.raises(ValidationError):
        SourceRequest.model_validate(operation.request.model_dump())
    # Mutation of a never-acquired intent must also fail policy checks at readback.
    changed = dict(operation.request.model_dump(), policy_digest="0" * 64)
    identity = LocalSourceRequest.model_validate(changed).identity
    intent = dict(
        operation.model_dump(),
        request=changed,
        operation_id=identity,
        state="fetching",
        page=None,
        result=None,
        acquired_at=None,
        finished_at=None,
        ledger_end=None,
    )
    payload = SourceOperation.model_validate(intent).model_dump_json().encode()
    database = tmp_path / "runs" / "binding-local" / "source-work" / "operations.sqlite"
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE operations SET id=?,payload=?,sha256=?",
            (identity, payload, hashlib.sha256(payload).hexdigest()),
        )
    with pytest.raises(ValueError, match="input policy"):
        read_source_work(cfg, "binding-local")


def test_graph_acknowledgement_loss_keeps_the_captured_local_original_unresolved(tmp_path):
    raw = docx()
    path = tmp_path / "report.docx"
    path.write_bytes(raw)
    graph = tomllib.loads(Path("examples/research-graph.toml").read_text())
    graph["sink_path"] = str(tmp_path / "graph")
    data = configured(tmp_path).model_dump()
    data["graph"] = graph
    cfg = GhimeraConfig.model_validate(data)

    class UncertainSink(MemoryGraphSink):
        async def append(self, batch):
            if batch.sequence == 1:
                (operation,) = read_source_work(cfg, "uncertain-local").operations
                assert operation.state == "processing" and operation.page.body == raw
                await super().append(batch)
                # The sink has the batch, but its acknowledgement is lost.
                raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
            return await super().append(batch)

    sink = UncertainSink()
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        graph_sink=sink,
    )

    async def run():
        session = await loop.open(Goal(text="ports"), run_id="uncertain-local")
        try:
            with pytest.raises(GhimeraRefused) as refused:
                await loop.import_local(session, (seed(path, raw),))
            assert refused.value.code == RefusalCode.GRAPH_SINK_FAILED
            with pytest.raises(SourceWorkFailure, match="unacknowledged"):
                session.checkpoint_state()
        finally:
            session.close()

    asyncio.run(run())
    (operation,) = read_source_work(cfg, "uncertain-local").unresolved
    assert operation.state == "processing" and operation.page.body == raw
    assert operation.ledger_end is None and operation.reason is None
    assert len(asyncio.run(sink.replay())) == 2
    assert read_journal(cfg.journal, "uncertain-local").state == "unsealed"


def test_completed_local_work_can_reopen_without_touching_the_file(tmp_path, monkeypatch):
    raw = docx()
    path = tmp_path / "report.docx"
    path.write_bytes(raw)
    cfg = configured(tmp_path)
    loop = collector(cfg)

    async def suspend():
        session = await loop.open(Goal(text="ports"), run_id="reopen-local")
        try:
            await loop.import_local(session, (seed(path, raw),))
            return session.checkpoint_state(), loop.snapshot(session)
        finally:
            session.close()

    state, harvest = asyncio.run(suspend())
    report = read_source_work(cfg, "reopen-local")
    path.unlink()

    def never_read(self, item, *, max_bytes):
        raise AssertionError("a completed local source must not be silently read again")

    monkeypatch.setattr(LocalInputLoader, "read", never_read)

    async def resume():
        session = await loop.restore(
            "reopen-local", harvest, state, search_calls=0, downtime_seconds=0
        )
        try:
            assert session.documents == harvest.documents
            return loop.finish(session, "frontier_empty")
        finally:
            session.close()

    resumed = asyncio.run(resume())
    assert resumed.documents == harvest.documents
    assert resumed.receipt.bytes_read == len(raw)
    assert read_source_work(cfg, "reopen-local") == report


def test_inspector_does_not_expose_private_local_paths_or_contact_sources(tmp_path):
    raw = docx()
    path = tmp_path / "private-person-report.docx"
    path.write_bytes(raw)
    cfg = configured(tmp_path)
    loop = collector(cfg)

    async def run():
        session = await loop.open(Goal(text="ports"), run_id="cli-local")
        try:
            await loop.import_local(session, (seed(path, raw),))
            return loop.finish(session, "frontier_empty")
        finally:
            session.close()

    asyncio.run(run())
    path.unlink()
    config_path = tmp_path / "config.json"
    config_path.write_text(cfg.model_dump_json())
    execution = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.source_work import main
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
GhimeraConfig.from_toml = classmethod(lambda cls, path: cfg)
sys.argv = ['ghimera.source_work', '--config', sys.argv[1], '--run-id', 'cli-local']
raise SystemExit(main())
""",
            str(config_path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert execution.stdout == "run_id=cli-local operations=1 writer_active=False unresolved=0\n"
    assert str(path) not in execution.stdout + execution.stderr
    assert "private-person" not in execution.stdout + execution.stderr
