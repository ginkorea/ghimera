"""Historical capsule capture before current graph/model work; no fake fresh fetch."""

import asyncio
import hashlib
import subprocess
import sys

import pytest
from pydantic import ValidationError

from ghimera import CorpusEvidenceReader, GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.graph import DirectoryGraphSink, MemoryGraphSink
from ghimera.journal import read_journal
from ghimera.loop import GoalLoop
from ghimera.model_client import SelfHostedModel
from ghimera.models import Goal
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research_reuse import RetainedResearchSession
from ghimera.source_work import SourceWorkFailure, SourceWorkStore, read_source_work
from ghimera.source_work_types import RetainedSourceOperation, SourceOperation
from tests.test_c0 import scope
from tests.test_corpus_evidence import policy as reader_policy
from tests.test_evidence_corpus import config as corpus_config
from tests.test_evidence_corpus import corpus, endpoint, harvest
from tests.test_pdf_transcription_corpus import reviewed_harvest
from tests.test_retained_graph import configured as graph_configured
from tests.test_semantic_graph import SemanticWire

__all__ = ["endpoint"]


def configured(tmp_path, reader, **updates):
    data = graph_configured(tmp_path, reader).model_dump()
    work = dict(
        schema="ghimera.source-work/1",
        max_operations=10,
        max_page_bytes=8000000,
        max_result_bytes=8000000,
        max_operation_bytes=17000000,
        max_store_bytes=200000000,
        database_timeout_seconds=2,
    )
    work.update(updates)
    data["source_work"] = work
    return GhimeraConfig.model_validate(data)


async def original(tmp_path, endpoint, *, pdf=False, **updates):
    material = (
        await reviewed_harvest(tmp_path)
        if pdf
        else await harvest(("zh", "港口：甲委員會隸屬乙委員會。"))
    )
    store = corpus(corpus_config(tmp_path, endpoint, max_document_bytes=2000000), create=True)
    try:
        await store.append(material)
        reader = CorpusEvidenceReader(
            reader_policy(store, max_original_bytes=2000000, max_response_bytes=4000000), store
        )
        cfg = configured(tmp_path, reader, **updates)
        intent = "unrelated" if pdf else "find ports"
        reuse = RetainedResearchSession(cfg.research.retained_evidence, reader, intent)
        await reuse.query(intent, remaining_seconds=30)
        (item,) = reuse.report.graph_originals
        return cfg, item
    finally:
        store.close()


def collector(cfg, *, sink=None, wire=None):
    if wire is None and cfg.semantics is not None:
        wire = SemanticWire(cfg.models.analyst)
    return GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        graph_sink=sink,
        semantic_extractor=SelfHostedModel(cfg, cfg.models.analyst, http=wire)
        if wire is not None
        else None,
    )


@pytest.mark.parametrize("reading", ("native", "reviewed_pdf"))
def test_retained_capsule_survives_admission_and_reopen_without_fresh_work(
    tmp_path, endpoint, reading
):
    async def run():
        cfg, item = await original(tmp_path, endpoint[0], pdf=reading == "reviewed_pdf")
        # No new semantic calls in this witness; old calls stay inside the capsule.
        raw = cfg.model_dump()
        raw.pop("semantics", None)
        raw["research"].pop("graph_context", None)
        cfg = GhimeraConfig.model_validate(raw)
        loop = collector(cfg)
        session = await loop.open(Goal(text="find ports"), run_id="retained")
        try:
            await loop.admit_retained(session, item)
            await loop.admit_retained(session, item)  # No duplicate admission or calls.
            result = loop.finish(session, "frontier_empty")
        finally:
            session.close()
        report = read_source_work(cfg, "retained")
        (operation,) = report.operations
        assert isinstance(operation, RetainedSourceOperation)
        assert operation.state == "processed" and operation.original == item
        assert operation.request.origin == item.origin
        assert result.retained_sources == (item,)
        assert (
            result.receipt.fetches == result.receipt.bytes_read == result.receipt.judge_calls == 0
        )
        assert not any(
            row.event == "fetch" or row.local_input or row.transcription_call
            for row in result.ledger
        )
        assert operation.ledger_end <= len(result.ledger)
        assert not report.unresolved and not report.writer_active
        assert await DirectoryGraphSink(cfg.graph, "retained").replay()
        if reading == "reviewed_pdf":
            assert operation.original.document.extracted.pdf_transcription.pages[0].calls
        path = tmp_path / "config.json"
        path.write_text(cfg.model_dump_json())
        checked = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.source_work import read_source_work
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
print(read_source_work(cfg, 'retained').model_dump_json())
""",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        assert type(report).model_validate_json(checked.stdout) == report
        return operation

    operation = asyncio.run(run())
    malformed = operation.model_dump()
    malformed["request"]["url"] += "/another"
    with pytest.raises(ValidationError, match="historical original"):
        RetainedSourceOperation.model_validate(malformed)


def test_capture_capacity_precedes_current_graph_or_model_side_effects(tmp_path, endpoint):
    async def run():
        cfg, item = await original(tmp_path, endpoint[0], max_page_bytes=1)
        sink, wire = MemoryGraphSink(), SemanticWire(cfg.models.analyst)
        loop = collector(cfg, sink=sink, wire=wire)
        session = await loop.open(Goal(text="find ports"), run_id="capacity-retained")
        try:
            before = await sink.replay()
            with pytest.raises(SourceWorkFailure):
                await loop.admit_retained(session, item)
            assert await sink.replay() == before
            assert not wire.requests and not session.ledger.snapshot()
            assert not read_source_work(cfg, "capacity-retained").operations
        finally:
            session.close()

    asyncio.run(run())


def test_historical_and_fresh_operations_share_one_ordered_inspection(tmp_path, endpoint):
    async def run():
        cfg, item = await original(tmp_path, endpoint[0])
        raw = cfg.model_dump()
        raw.pop("semantics", None)
        raw["research"].pop("graph_context", None)
        raw["source_work"]["frontier"] = dict(
            schema="ghimera.source-frontier/1",
            max_entries=30,
            max_entry_bytes=4000,
            max_frontier_bytes=100000,
        )
        cfg = GhimeraConfig.model_validate(raw)
        loop = collector(cfg)
        session = await loop.open(Goal(text="ports"), run_id="mixed-originals")
        try:
            await loop.admit_retained(session, item)
            await loop.collect(
                session, scope(), ("https://example.org/fresh",), fetch_limit=1, allow_grade=False
            )
            snapshot = session.checkpoint_state()
            prefix = len(session.ledger.snapshot())
        finally:
            session.close()
        report = read_source_work(cfg, "mixed-originals")
        historical, fresh = report.operations
        assert isinstance(historical, RetainedSourceOperation)
        assert isinstance(fresh, SourceOperation)
        assert historical.sequence == 0 and fresh.sequence == 1
        assert historical.original == item
        assert fresh.page.url == "https://example.org/fresh"
        assert fresh.result.raw == fresh.page.body
        assert not report.unresolved and not report.writer_active
        reopened = SourceWorkStore.resume(cfg, "mixed-originals", prefix)
        try:
            reopened.verify_frontier(snapshot)
            assert reopened.report().operations == report.operations
            assert reopened.report().queued == report.queued
            assert report.queued
        finally:
            reopened.close()

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ("refused", "cancelled"))
def test_acknowledged_model_failure_retains_original_and_current_call_evidence(
    tmp_path, endpoint, outcome
):
    async def run():
        cfg, item = await original(tmp_path, endpoint[0])

        class Wire(SemanticWire):
            async def post(self, body):
                if outcome == "cancelled":
                    raise asyncio.CancelledError
                return await super().post(body)

        wire = Wire(cfg.models.analyst, wrong_surface=True)
        loop = collector(cfg, wire=wire)
        session = await loop.open(Goal(text="find ports"), run_id="failed-retained")
        try:
            expected = asyncio.CancelledError if outcome == "cancelled" else GhimeraRefused
            with pytest.raises(expected):
                await loop.admit_retained(session, item)
            result = loop.finish(session, "frontier_empty")
        finally:
            session.close()
        report = read_source_work(cfg, "failed-retained")
        (operation,) = report.operations
        assert operation.state == outcome and operation.original == item
        assert operation.reason and not report.unresolved
        calls = tuple(row.model_call for row in result.ledger if row.model_call)
        assert len(calls) == result.receipt.judge_calls == 1
        assert calls[0].outcome == ("cancelled" if outcome == "cancelled" else "success")
        assert result.receipt.fetches == result.receipt.bytes_read == 0
        assert result.retained_sources == (item,)
        assert any(row.retained_failure == item.origin for row in result.ledger)

    asyncio.run(run())


def test_lost_graph_acknowledgement_retains_original_without_a_false_terminal_result(
    tmp_path, endpoint
):
    async def run():
        cfg, item = await original(tmp_path, endpoint[0])

        class UncertainSink(MemoryGraphSink):
            async def append(self, batch):
                if batch.sequence == 1:
                    (operation,) = read_source_work(cfg, "uncertain-retained").operations
                    assert operation.state == "processing" and operation.original == item
                    await super().append(batch)
                    raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
                return await super().append(batch)

        sink = UncertainSink()
        loop = collector(cfg, sink=sink)
        session = await loop.open(Goal(text="find ports"), run_id="uncertain-retained")
        try:
            with pytest.raises(GhimeraRefused) as refused:
                await loop.admit_retained(session, item)
            assert refused.value.code == RefusalCode.GRAPH_SINK_FAILED
            with pytest.raises(SourceWorkFailure, match="unacknowledged"):
                session.checkpoint_state()
        finally:
            session.close()
        (pending,) = read_source_work(cfg, "uncertain-retained").unresolved
        assert pending.state == "processing" and pending.original == item
        assert pending.reason is None and pending.ledger_end is None
        assert len(await sink.replay()) == 2
        assert read_journal(cfg.journal, "uncertain-retained").rows == ()
        with pytest.raises(ValueError, match="explicit reconciliation"):
            SourceWorkStore.resume(cfg, "uncertain-retained", 0)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ("captured", "graph", "completed_prefix"))
def test_actual_process_loss_preserves_retained_original_without_replaying_it(
    tmp_path, endpoint, phase
):
    cfg, item = asyncio.run(original(tmp_path, endpoint[0]))
    path, capsule = tmp_path / "config.json", tmp_path / "original.json"
    path.write_text(cfg.model_dump_json())
    capsule.write_text(item.model_dump_json())
    checked = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, os, sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.graph import DirectoryGraphSink
from ghimera.loop import GoalLoop
from ghimera.models import Goal, RetainedOriginal
from ghimera.source_work import SourceWorkStore
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
item = RetainedOriginal.model_validate_json(Path(sys.argv[2]).read_bytes())
phase = sys.argv[3]
capture = SourceWorkStore.capture_retained
def captured(self, original, ledger_start):
    token = capture(self, original, ledger_start)
    if phase == 'captured':
        os._exit(23)
    return token
SourceWorkStore.capture_retained = captured
class Sink(DirectoryGraphSink):
    async def append(self, batch):
        checkpoint = await super().append(batch)
        if phase == 'graph' and batch.sequence == 1:
            os._exit(23)
        return checkpoint
# Keep the exact configured recipe and its named semantic collaborator.
from ghimera.model_client import SelfHostedModel
from tests.test_semantic_graph import SemanticWire
loop = GoalLoop(config=cfg, fetcher=FetchLadder((FakeRoute(),)), extractor=FakeExtractor(),
    scorer=KeywordScorer(), judge=FakeJudge(), graph_sink=Sink(cfg.graph, 'lost-retained'),
    semantic_extractor=SelfHostedModel(cfg, cfg.models.analyst,
        http=SemanticWire(cfg.models.analyst)))
async def run():
    session = await loop.open(Goal(text='find ports'), run_id='lost-retained')
    await loop.admit_retained(session, item)
    os._exit(23)
asyncio.run(run())
""",
            str(path),
            str(capsule),
            phase,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert checked.returncode == 23, checked.stderr
    report = read_source_work(cfg, "lost-retained")
    (operation,) = report.operations
    assert operation.original == item and not report.writer_active
    if phase == "completed_prefix":
        assert operation.state == "processed" and not report.unresolved
        assert operation.ledger_end <= len(read_journal(cfg.journal, "lost-retained").rows)
    else:
        assert operation.state == ("acquired" if phase == "captured" else "processing")
        assert report.unresolved == (operation,)
        assert operation.ledger_end is None
        with pytest.raises(ValueError, match="explicit reconciliation"):
            SourceWorkStore.resume(
                cfg, "lost-retained", len(read_journal(cfg.journal, "lost-retained").rows)
            )
    assert not any(
        row.event == "fetch" or row.local_input or row.transcription_call
        for row in read_journal(cfg.journal, "lost-retained").rows
    )
    assert hashlib.sha256(operation.original.document.raw).hexdigest() == item.document.sha256
