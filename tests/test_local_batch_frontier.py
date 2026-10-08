"""Durable caller batches: real file reads/process loss, no invented web frontier."""

import asyncio
import sqlite3
import subprocess
import sys

import pytest

from ghimera.documents import DocumentExtractor
from ghimera.journal import read_journal
from ghimera.local_inputs import LocalInputLoader
from ghimera.models import Goal
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.source_work import SourceWorkFailure, read_source_work
from ghimera.source_work_types import LocalSourceRequest, SourceCoordinates
from tests.test_c0 import scope
from tests.test_document_extraction import docx
from tests.test_local_inputs import seed
from tests.test_local_source_work import collector
from tests.test_local_source_work import configured as local_configured


def configured(tmp_path, **updates):
    return local_configured(
        tmp_path,
        frontier=dict(
            schema="ghimera.source-frontier/1",
            max_entries=20,
            max_entry_bytes=10000,
            max_frontier_bytes=100000,
        )
        | updates,
    )


def batch(tmp_path):
    first, second = docx(), docx("Ports: the second document describes another terminal.")
    a, b = tmp_path / "first.docx", tmp_path / "second.docx"
    a.write_bytes(first)
    b.write_bytes(second)
    return (seed(a, first), seed(b, second)), (first, second)


@pytest.mark.parametrize("phase", ("queued", "reading"))
def test_process_loss_keeps_all_unstarted_files_in_caller_order(tmp_path, phase):
    cfg = configured(tmp_path)
    seeds, _ = batch(tmp_path)
    path = tmp_path / "recipe.json"
    path.write_text(cfg.model_dump_json())
    seed_path = tmp_path / "seeds.json"
    seed_path.write_text("[" + ",".join(item.model_dump_json() for item in seeds) + "]")
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, json, os, sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.local_input_types import LocalDocumentSeed
from ghimera.local_inputs import LocalInputLoader
from ghimera.models import Goal
from ghimera.source_frontier import SourceFrontier
from tests.test_local_source_work import collector
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
seeds = tuple(LocalDocumentSeed.model_validate(item)
              for item in json.loads(Path(sys.argv[2]).read_bytes()))
phase = sys.argv[3]
enqueue = SourceFrontier.enqueue_local_batch
def queued(self, requests, ledger_start, *, available_bytes):
    enqueue(self, requests, ledger_start, available_bytes=available_bytes)
    if phase == 'queued':
        os._exit(23)
SourceFrontier.enqueue_local_batch = queued
read = LocalInputLoader.read
def reading(self, item, *, max_bytes):
    read(self, item, max_bytes=max_bytes)
    os._exit(23)
LocalInputLoader.read = reading
loop = collector(cfg)
async def run():
    session = await loop.open(Goal(text='ports'), run_id='lost-batch')
    await loop.import_local(session, seeds)
asyncio.run(run())
""",
            str(path),
            str(seed_path),
            phase,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 23, process.stderr
    report = read_source_work(cfg, "lost-batch")
    assert len(report.frontier) == 2 and not report.queued and not report.writer_active
    assert all(isinstance(item.request, LocalSourceRequest) for item in report.frontier)
    expected = seeds if phase == "queued" else seeds[1:]
    assert tuple(request.seed for request in report.queued_local) == expected
    assert read_journal(cfg.journal, "lost-batch").rows == ()
    if phase == "queued":
        assert not report.operations and not report.unresolved
    else:
        (uncertain,) = report.unresolved
        assert uncertain.request.seed == seeds[0]
        assert uncertain.state == "fetching" and uncertain.ledger_end is None
        assert uncertain.page is None  # Physical read completed but its acknowledgement was lost.
    assert read_source_work(cfg, "lost-batch") == report


@pytest.mark.parametrize("failure", ("capacity", "database", "changed_pin"))
def test_batch_refusal_is_atomic_and_precedes_any_file_io(tmp_path, monkeypatch, failure):
    cfg = configured(tmp_path, max_entries=1) if failure == "capacity" else configured(tmp_path)
    seeds, _ = batch(tmp_path)
    if failure == "changed_pin":
        seeds = (seeds[0], seeds[0].model_copy(update={"sha256": "0" * 64}))
    reads = []

    def read(*args, **kwargs):
        reads.append(args)
        raise AssertionError("The rejected batch must not open any input")

    monkeypatch.setattr(LocalInputLoader, "read", read)

    async def run():
        loop = collector(cfg)
        session = await loop.open(Goal(text="ports"), run_id="rejected-batch")
        try:
            if failure == "database":
                # Abort the second INSERT inside the actual SQLite transaction.
                db = session.source_work._private.db
                db.execute(
                    "CREATE TRIGGER fail_second BEFORE INSERT ON frontier "
                    "WHEN NEW.sequence=1 BEGIN SELECT RAISE(ABORT,'controlled fixture'); END;"
                )
                db.commit()
            with pytest.raises(SourceWorkFailure, match="batch"):
                await loop.import_local(session, seeds)
            assert not reads and not session.ledger.snapshot()
            assert not session._local_frontier and not session._frontier
            report = read_source_work(cfg, "rejected-batch")
            assert not report.frontier and not report.operations
            assert session.source_work._frontier.payload_bytes == 0
        finally:
            session.close()

    asyncio.run(run())


def test_quiescent_restore_reads_only_the_pending_file_and_keeps_native_spend(tmp_path):
    cfg = configured(tmp_path)
    seeds, originals = batch(tmp_path)

    class RefuseFirst(DocumentExtractor):
        async def extract(self, page):
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)

    loop = collector(cfg, extractor=RefuseFirst(cfg))

    async def first():
        session = await loop.open(Goal(text="ports"), run_id="resume-batch")
        try:
            with pytest.raises(GhimeraRefused):
                await loop.import_local(session, seeds)
            state, material = session.checkpoint_state(), loop.snapshot(session)
            assert tuple(item.seed for item in state.local_frontier) == seeds[1:]
            assert material.receipt.bytes_read == len(originals[0])
            assert session._frontier == []  # Local input never acquires a web scope.
            return state, material
        finally:
            session.close()

    state, material = asyncio.run(first())
    seeds[0].path.unlink()  # The acknowledged refused original must never be reopened.
    loop = collector(cfg)

    async def resume():
        restored = await loop.restore(
            "resume-batch", material, state, search_calls=0, downtime_seconds=0
        )
        try:
            await loop.import_local(restored, ())  # Explicitly drain restored pending work.
            assert not restored.checkpoint_state().local_frontier
            return loop.finish(restored, "frontier_empty")
        finally:
            restored.close()

    result = asyncio.run(resume())
    assert result.documents[0].raw == originals[1]
    assert result.receipt.fetches == 0
    assert result.receipt.bytes_read == sum(map(len, originals))
    report = read_source_work(cfg, "resume-batch")
    assert [item.state for item in report.operations] == ["refused", "processed"]
    assert not report.queued and not report.queued_local and not report.unresolved


@pytest.mark.parametrize("mutation", ("missing", "reordered", "changed_pin", "invented"))
def test_checkpoint_must_preserve_pending_local_order_and_pins(tmp_path, mutation):
    cfg = configured(tmp_path)
    seeds, _ = batch(tmp_path)

    async def run():
        loop = collector(cfg)
        session = await loop.open(Goal(text="ports"), run_id="checkpoint-batch")
        try:
            requests = tuple(
                LocalSourceRequest(
                    schema="ghimera.local-source-request/1",
                    seed=item,
                    policy_digest=cfg.local_inputs.content_digest(),
                )
                for item in seeds
            )
            session.source_work.enqueue_local_batch(requests, session.ledger.next_sequence)
            session._local_frontier = list(requests)
            state = session.checkpoint_state()
            if mutation == "missing":
                pending = requests[:1]
            elif mutation == "reordered":
                pending = requests[::-1]
            elif mutation == "changed_pin":
                changed = requests[0].model_copy(
                    update={"seed": seeds[0].model_copy(update={"sha256": "0" * 64})}
                )
                pending = (changed, requests[1])
            else:
                pending = (*requests, requests[0])
            altered = type(state).model_validate(state.model_dump() | {"local_frontier": pending})
            with pytest.raises(SourceWorkFailure, match="local pending"):
                session.source_work.verify_frontier(altered)
            session.source_work.verify_frontier(state)
        finally:
            session.close()

    asyncio.run(run())


def test_storage_tail_loss_of_an_unstarted_local_file_refuses_inspection(tmp_path):
    cfg = configured(tmp_path)
    seeds, _ = batch(tmp_path)

    async def run():
        loop = collector(cfg)
        session = await loop.open(Goal(text="ports"), run_id="tampered-batch")
        try:
            requests = tuple(
                LocalSourceRequest(
                    schema="ghimera.local-source-request/1",
                    seed=item,
                    policy_digest=cfg.local_inputs.content_digest(),
                )
                for item in seeds
            )
            session.source_work.enqueue_local_batch(requests, session.ledger.next_sequence)
        finally:
            session.close()

    asyncio.run(run())
    path = tmp_path / "runs" / "tampered-batch" / "source-work" / "operations.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM frontier WHERE sequence=1")
    with pytest.raises(ValueError, match="acknowledged frontier entry"):
        read_source_work(cfg, "tampered-batch")


def test_private_pending_paths_are_not_printed_by_inspection(tmp_path):
    cfg = configured(tmp_path)
    seeds, _ = batch(tmp_path)

    async def run():
        loop = collector(cfg)
        session = await loop.open(Goal(text="ports"), run_id="inspect-batch")
        try:
            requests = tuple(
                LocalSourceRequest(
                    schema="ghimera.local-source-request/1",
                    seed=item,
                    policy_digest=cfg.local_inputs.content_digest(),
                )
                for item in seeds
            )
            session.source_work.enqueue_local_batch(requests, session.ledger.next_sequence)
        finally:
            session.close()

    asyncio.run(run())
    for item in seeds:
        item.path.unlink()  # Inspection is metadata-only, even with missing pending files.
    path = tmp_path / "recipe.json"
    path.write_text(cfg.model_dump_json())
    process = subprocess.run(
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
sys.argv = ['ghimera.source_work', '--config', sys.argv[1], '--run-id', 'inspect-batch']
raise SystemExit(main())
""",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert process.stdout == (
        "run_id=inspect-batch operations=0 writer_active=False unresolved=0 "
        "queued=0 local_queued=2\n"
    )
    assert not process.stderr and str(tmp_path) not in process.stdout


def test_mixed_frontier_keeps_first_local_order_without_conferring_web_scope(tmp_path):
    cfg = configured(tmp_path)
    seeds, _ = batch(tmp_path)

    async def run():
        loop = collector(cfg)
        session = await loop.open(Goal(text="ports"), run_id="mixed-batch")
        try:
            web = SourceCoordinates(
                url="https://example.org/page",
                scope=scope(),
                depth=0,
                reference_hops=0,
                reference_origin=None,
            )
            session.source_work.enqueue(web, -1, session.ledger.next_sequence)
            session._frontier = [(-1, web.url, 0)]
            requests = tuple(
                LocalSourceRequest(
                    schema="ghimera.local-source-request/1",
                    seed=item,
                    policy_digest=cfg.local_inputs.content_digest(),
                )
                for item in seeds
            )
            session.source_work.enqueue_local_batch(requests, session.ledger.next_sequence)
            first = read_source_work(cfg, "mixed-batch").frontier
            session.source_work.enqueue_local_batch(
                (requests[1], requests[0], requests[1]), session.ledger.next_sequence
            )
            report = read_source_work(cfg, "mixed-batch")
            assert report.frontier == first
            assert tuple(entry.request for entry in report.queued) == (web,)
            assert report.queued_local == requests
            session._local_frontier = list(requests)
            session.source_work.verify_frontier(session.checkpoint_state())
            assert not report.operations and not session.ledger.snapshot()
            assert all(not web.scope.permits(item.url) for item in requests)
        finally:
            session.close()

    asyncio.run(run())
