"""Prepared native process-death witnesses; fixtures are not model quality.

The operator schedules this finite module after the parent gate is terminal.
Every contact is a loopback protocol fixture and every consumer is a fresh
exact-source interpreter invoking the ordinary public Collector facade.
"""

import asyncio
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.documents import DOCX_TYPE
from ghimera.journal import _run_path, read_journal
from ghimera.model_http import ModelHttpResponse, PinnedModelHttp
from ghimera.refusals import GhimeraRefused
from ghimera.research_recovery_config import ResearchRecoveryConfig
from ghimera.research_types import ResearchRequest, ResearchResult
from ghimera.source_processing import SourceProcessingCursor
from ghimera.source_work import SourceWorkStore, read_source_work
from tests.test_collector import assembled
from tests.test_document_extraction import config as document_config
from tests.test_document_extraction import docx
from tests.test_embedding_scoring import endpoint as encoder_endpoint
from tests.test_http_fetch import site as source_site
from tests.test_local_inputs import recipe, seed
from tests.test_search_conformance import endpoint as search_endpoint
from tests.test_served_models import endpoint as model_endpoint
from tests.test_source_acquisition_recovery import configured as source_config

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


class FirstHoldHttp:
    """Controlled wire fixture, not an unchanged real model response.

    Native model decoding/ACK storage observes these exact controlled bytes.
    The unchanged second-look wire goes through the native pinned delegate.
    """

    def __init__(self, config):
        self.config = config
        self.delegate = PinnedModelHttp(config)

    async def post(self, body):
        response = await self.delegate.post(body)
        packet = json.loads(json.loads(body)["messages"][1]["content"])
        if packet["task"] != "verdict" or packet["second_look"]:
            return response
        wire = json.loads(response.body)
        native = json.loads(wire["choices"][0]["message"]["content"])
        native["decision"] = "hold"
        wire["choices"][0]["message"]["content"] = json.dumps(native)
        return ModelHttpResponse(response.status, json.dumps(wire).encode(), response.content_type)


def configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint):
    original, _ = assembled(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    native = source_config(tmp_path)
    data = original.model_dump()
    data.update(
        journal=native.journal,
        source_work=native.source_work,
        model_work=native.model_work,
        graph=native.graph,
        document_extraction=document_config(tmp_path).document_extraction,
        local_inputs=recipe(tmp_path),
        wall_seconds=300.0,
        judge_budget=40,
    )
    data["research"].update(max_rounds=1, max_pages_per_round=1)
    data["research"]["content_types"] = tuple(
        dict.fromkeys((*data["research"]["content_types"], DOCX_TYPE))
    )
    scoring = data["scoring"]
    scoring.update(
        schema="ghimera.scoring/3",
        query_encoder=dict(scoring["encoder"], text_prefix="query: "),
        run_encoding_recovery=dict(
            schema="ghimera.run-encoding-recovery/1",
            max_result_bytes=200000,
            max_total_result_bytes=2000000,
            unknown_policy="hold",
        ),
    )
    data["research_recovery"] = dict(
        schema="ghimera.research-recovery/7",
        max_snapshot_bytes=4000000,
        clock_policy="include_downtime",
        tail_policy="acknowledged_model_return",
        source_acquisition=dict(
            schema="ghimera.source-acquisition-recovery/1",
            execution="serial",
            max_capsule_bytes=2000000,
        ),
        source_processing=dict(
            schema="ghimera.source-processing-recovery/1",
            execution="serial",
            max_capsule_bytes=4000000,
            unknown_policy="hold",
        ),
    )
    return GhimeraConfig.model_validate(data)


ENTRY = r"""
import asyncio, os, sys
from pathlib import Path
from ghimera import Collector
from ghimera.config import GhimeraConfig
from ghimera.documents import DocumentExtractor
from ghimera.graph import DirectoryGraphSink
from ghimera.journal import DirectoryLedgerSink
from ghimera.research_types import ResearchRequest
from ghimera.source_work import SourceWorkStore
from tests.test_http_fetch import ResolverFixture
from tests.test_source_processing_recovery import FirstHoldHttp

root = Path(sys.argv[1])
mode = sys.argv[2]
cfg = GhimeraConfig.model_validate_json((root / 'config.json').read_bytes())
request = ResearchRequest.model_validate_json((root / 'request.json').read_bytes())
if mode != 'resume':
    save = SourceWorkStore.save_processing
    def saved(self, cursor):
        save(self, cursor)
        if mode == cursor.stage and mode in {'parsing', 'parsed', 'scored', 'judged'}:
            os._exit(81)
    SourceWorkStore.save_processing = saved
    append = DirectoryLedgerSink.append
    def appended(self, row):
        append(self, row)
        if mode == 'encoding_intent' and row.run_encoding_intent is not None:
            os._exit(82)
        if mode == 'vector_ack' and row.run_encoding_ack is not None:
            sequence = row.run_encoding_ack.original_intent_sequence
            original = self.committed_rows[sequence].run_encoding_intent
            if original is not None and original.purpose == 'source':
                os._exit(83)
        if (mode == 'verdict_intent' and row.model_intent is not None
                and row.model_intent.phase == 'verdict'):
            os._exit(84)
        if mode in {'verdict_ack', 'second_verdict_ack'} and row.model_ack is not None:
            original = self.committed_rows[row.model_ack.intent_sequence].model_intent
            if original is not None and original.phase == 'verdict':
                prior = sum(r.model_intent is not None and r.model_intent.phase == 'verdict'
                            for r in self.committed_rows)
                if mode == 'verdict_ack' or prior == 2:
                    os._exit(85)
    DirectoryLedgerSink.append = appended
    write = DirectoryGraphSink._write
    def written(self, batch):
        result = write(self, batch)
        if mode == 'graph_ack' and any(node.content_sha256 is not None for node in batch.nodes):
            os._exit(86)
        return result
    DirectoryGraphSink._write = written
    ports = {'judge': FirstHoldHttp(cfg.models.judge)} if mode == 'second_verdict_ack' else None
    asyncio.run(Collector(cfg, source_resolver=ResolverFixture(), model_http=ports).run(
        request, run_id='cut'))
    raise AssertionError('requested native cut was not observed')
else:
    cut = SourceWorkStore.processing_read(cfg, 'cut')
    extract = DocumentExtractor.extract
    async def guarded(self, page):
        if page.body == cut.operation.page.body:
            raise AssertionError('retained parser reading was recomputed')
        return await extract(self, page)
    DocumentExtractor.extract = guarded
    result = asyncio.run(Collector(cfg, source_resolver=ResolverFixture()).recover('cut',
        snapshot_sha256=cut.sha256, boundary='source_processing'))
    (root / 'result.json').write_text(result.model_dump_json())
"""


def subprocess_cut(tmp_path, fixtures, mode):
    cfg = configured(tmp_path, *fixtures)
    raw = docx()
    path = tmp_path / "owned.docx"
    path.write_bytes(raw)
    request = ResearchRequest(intent="find ports", local_documents=(seed(path, raw),))
    (tmp_path / "config.json").write_text(cfg.model_dump_json())
    (tmp_path / "request.json").write_text(request.model_dump_json())
    child = subprocess.run(
        [sys.executable, "-c", ENTRY, str(tmp_path), mode], capture_output=True, timeout=100
    )
    assert child.returncode in {81, 82, 83, 84, 85, 86}, child.stderr.decode()
    path.unlink()
    return cfg, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "mode",
    ["parsed", "graph_ack", "vector_ack", "scored", "verdict_ack", "second_verdict_ack", "judged"],
)
def test_fresh_public_collector_adopts_exact_original_source_acknowledgements(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, mode
):
    fixtures = source_site, search_endpoint, model_endpoint, encoder_endpoint
    cfg, source_sha = subprocess_cut(tmp_path, fixtures, mode)
    before = read_journal(cfg.journal, "cut")
    original = tuple(
        (row.sequence, row.run_encoding_intent)
        for row in before.rows
        if row.run_encoding_intent is not None
    )
    original_verdicts = tuple(
        row.sequence
        for row in before.rows
        if row.model_intent is not None and row.model_intent.phase == "verdict"
    )
    resumed = subprocess.run(
        [sys.executable, "-c", ENTRY, str(tmp_path), "resume"], capture_output=True, timeout=100
    )
    assert resumed.returncode == 0, resumed.stderr.decode()
    result = ResearchResult.model_validate_json((tmp_path / "result.json").read_bytes())
    assert result.status == "answered"
    assert any(doc.sha256 == source_sha for doc in result.harvest.documents)
    assert sum(row.event == "local_input" for row in result.harvest.ledger) == 1
    assert result.harvest.receipt.encoding_calls == len(encoder_endpoint[1])
    charged_encoding = tuple(
        row.run_encoding_intent
        for row in result.harvest.ledger
        if row.run_encoding_intent is not None
    )
    assert result.harvest.receipt.encoding_calls == len(charged_encoding)
    assert result.harvest.receipt.encoding_chars == sum(
        intent.input_chars for intent in charged_encoding
    )
    assert result.harvest.receipt.judge_calls == len(model_endpoint[1])
    for sequence, intent in original:
        assert result.harvest.ledger[sequence].run_encoding_intent == intent
    for sequence in original_verdicts:
        assert (
            sum(
                row.model_ack is not None and row.model_ack.intent_sequence == sequence
                for row in result.harvest.ledger
            )
            == 1
        )
    if mode in {"verdict_ack", "second_verdict_ack"}:
        assert any(
            row.model_replay is not None
            and row.model_replay.intent_sequence == original_verdicts[-1]
            for row in result.harvest.ledger
        )
    if mode == "second_verdict_ack":
        assert len(original_verdicts) == 2
        assert any(
            row.event == "verdict" and row.reason.startswith("hold:")
            for row in result.harvest.ledger
        )


@pytest.mark.parametrize("mode", ["parsing", "encoding_intent", "verdict_intent"])
def test_unknown_parser_encoding_or_model_stays_held_without_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, mode
):
    cfg, _ = subprocess_cut(
        tmp_path, (source_site, search_endpoint, model_endpoint, encoder_endpoint), mode
    )
    before = len(model_endpoint[1]), len(encoder_endpoint[1]), len(search_endpoint[1])
    with pytest.raises(ValueError):
        SourceWorkStore.processing_read(cfg, "cut")
    assert before == (len(model_endpoint[1]), len(encoder_endpoint[1]), len(search_endpoint[1]))
    journal = read_journal(cfg.journal, "cut")
    assert journal.state == "unsealed"
    if mode != "parsing":
        assert any(
            row.run_encoding_intent is not None or row.model_intent is not None
            for row in journal.rows
        )


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6])
def test_processing_is_not_implicitly_admitted_in_old_recovery_profiles(version):
    raw = dict(
        schema=f"ghimera.research-recovery/{version}",
        max_snapshot_bytes=10000,
        clock_policy="include_downtime",
        tail_policy="acknowledged_model_return",
        source_processing=None,
    )
    with pytest.raises(ValidationError, match="source processing requires"):
        ResearchRecoveryConfig.model_validate(raw)


def test_old_recovery_dump_remains_exact_and_processing_requires_both_policies():
    raw = dict(
        schema="ghimera.research-recovery/1",
        max_snapshot_bytes=10000,
        clock_policy="include_downtime",
        tail_policy="acknowledged_model_return",
    )
    original = ResearchRecoveryConfig.model_validate(raw)
    expected = (
        b'{"schema":"ghimera.research-recovery/1","max_snapshot_bytes":10000,'
        b'"clock_policy":"include_downtime","tail_policy":"acknowledged_model_return"}'
    )
    assert original.model_dump_json().encode() == expected
    with pytest.raises(ValidationError, match="requires acquisition and processing"):
        ResearchRecoveryConfig.model_validate(dict(raw, schema="ghimera.research-recovery/7"))


@pytest.mark.parametrize("orphan", ["model_sequence", "model_request_sha256"])
def test_nonpending_cursor_refuses_either_orphan_model_coordinate(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, orphan
):
    cfg, _ = subprocess_cut(
        tmp_path, (source_site, search_endpoint, model_endpoint, encoder_endpoint), "parsed"
    )
    original = SourceWorkStore.processing_read(cfg, "cut")
    raw = original.cursor.model_dump()
    raw[orphan] = 0 if orphan == "model_sequence" else "0" * 64
    with pytest.raises(ValidationError, match="exact original reservation coordinates"):
        SourceProcessingCursor.model_validate(raw)


def test_cursor_refuses_graph_history_the_native_capture_owner_cannot_produce(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, _ = subprocess_cut(
        tmp_path, (source_site, search_endpoint, model_endpoint, encoder_endpoint), "scored"
    )
    original = SourceWorkStore.processing_read(cfg, "cut")
    raw = original.cursor.model_dump()
    assert len(raw["graph_commits"]) >= 2
    raw["graph_commits"][0]["acknowledged"] = False
    with pytest.raises(ValidationError, match="unacknowledged graph batch must be the final"):
        SourceProcessingCursor.model_validate(raw)


@pytest.mark.parametrize(
    "defect", ["source_text", "recipe", "prefix", "writer", "missing_graph_ack"]
)
def test_processing_drift_and_owner_refusals_do_not_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, defect
):
    cfg, _ = subprocess_cut(
        tmp_path, (source_site, search_endpoint, model_endpoint, encoder_endpoint), "scored"
    )
    original = SourceWorkStore.processing_read(cfg, "cut")
    before = len(model_endpoint[1]), len(encoder_endpoint[1]), len(search_endpoint[1])
    if defect == "recipe":
        data = cfg.model_dump()
        data["models"]["judge"]["revision"] = "changed-original-revision"
        with pytest.raises(ValueError):
            SourceWorkStore.processing_read(GhimeraConfig.model_validate(data), "cut")
    elif defect == "writer":
        store = SourceWorkStore.resume(cfg, "cut", len(original.journal.rows), processing=original)
        try:
            with pytest.raises((ValueError, OSError)):
                SourceWorkStore.processing_read(cfg, "cut")
        finally:
            store.close()
    elif defect == "missing_graph_ack":
        final = original.cursor.graph_commits[-1].batch
        (Path(cfg.graph.sink_path) / "cut" / f"{final.sequence:012d}.json").unlink()

        async def restore():
            from ghimera import Collector
            from tests.test_http_fetch import ResolverFixture

            with pytest.raises(ValueError):
                await Collector(cfg, source_resolver=ResolverFixture()).recover(
                    "cut", snapshot_sha256=original.sha256, boundary="source_processing"
                )

        asyncio.run(restore())
    else:
        # Persist deliberately corrupted bytes, rather than rejecting the
        # fixture through its write-time model validator before readback.
        cursor = original.cursor.model_dump(mode="json")
        if defect == "source_text":
            cursor["extracted"]["text"] += "changed retained native source"
        else:
            cursor["prefix_sha256"][-1] = "0" * 64
        altered = json.dumps(cursor, ensure_ascii=False, separators=(",", ":")).encode()
        with sqlite3.connect(
            _run_path(cfg.journal, "cut") / "source-work" / "operations.sqlite"
        ) as db:
            db.execute(
                "UPDATE source_processing SET payload=?,sha256=? WHERE id=1",
                (altered, hashlib.sha256(altered).hexdigest()),
            )
        with pytest.raises(ValueError):
            SourceWorkStore.processing_read(cfg, "cut")
    assert before == (len(model_endpoint[1]), len(encoder_endpoint[1]), len(search_endpoint[1]))


@pytest.mark.parametrize("stop", ["cancelled", "wall_exhausted"])
def test_processing_adoption_keeps_cancellation_and_original_wall_limits(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, monkeypatch, stop
):
    from ghimera import Collector
    from ghimera.loop import GoalLoop
    from tests.test_http_fetch import ResolverFixture

    cfg, _ = subprocess_cut(
        tmp_path, (source_site, search_endpoint, model_endpoint, encoder_endpoint), "parsed"
    )
    cut = SourceWorkStore.processing_read(cfg, "cut")
    before = len(model_endpoint[1]), len(encoder_endpoint[1]), len(search_endpoint[1])
    if stop == "cancelled":

        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr(GoalLoop, "_consume_page", cancelled)
        expected = asyncio.CancelledError
    else:
        monkeypatch.setattr(
            "ghimera.research.time.time",
            lambda: cut.cursor.original.saved_at + cfg.wall_seconds + 1,
        )
        expected = GhimeraRefused

    async def recover():
        with pytest.raises(expected):
            await Collector(cfg, source_resolver=ResolverFixture()).recover(
                "cut", snapshot_sha256=cut.sha256, boundary="source_processing"
            )

    asyncio.run(recover())
    assert before == (len(model_endpoint[1]), len(encoder_endpoint[1]), len(search_endpoint[1]))
    report = read_source_work(cfg, "cut")
    assert report.operations[-1].state == ("cancelled" if stop == "cancelled" else "refused")
    with pytest.raises(ValueError):
        SourceWorkStore.processing_read(cfg, "cut")
