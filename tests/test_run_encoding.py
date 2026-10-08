"""Native process-death and vector replay fixtures, not encoder quality evidence."""

import asyncio
import hashlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeJudge
from ghimera.embedding_types import EncodingBatch, EncodingCall, encoding_request
from ghimera.journal import DirectoryLedgerSink, read_journal
from ghimera.ledger import Ledger
from ghimera.models import Extracted, Goal, Harvest, LedgerRow, ModelIdentity, Receipt
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.run_encoding import encoding_usage, validate_run_encoding_rows
from ghimera.run_encoding_types import RunEncodingDecision
from ghimera.semantic_scoring import EmbeddingScorer
from ghimera.source_feed_parse import parse_feed
from tests.test_embedding_scoring import intent_policy, policy, references, service


def prepared(tmp_path, *, calls=20, recovery_updates=None):
    encoder = service(9, max_batch_texts=20, text_prefix="passage: ")
    recovery = dict(
        schema="ghimera.run-encoding-recovery/1",
        max_result_bytes=20000,
        max_total_result_bytes=100000,
        unknown_policy="hold",
    )
    recovery.update(recovery_updates or {})
    scoring = policy(
        encoder,
        references(encoder),
        schema="ghimera.scoring/3",
        reference_source="intent",
        references_sha256=None,
        query_encoder=service(9, text_prefix="query: "),
        max_windows=1,
        encoding_call_budget=calls,
        run_encoding_recovery=recovery,
    )
    feed = tomllib.loads(Path("examples/source-feeds.toml").read_text())
    feed.update(worker_python=sys.executable, work_directory=str(tmp_path / "feed"))
    raw = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump()
    raw.update(
        scoring=scoring,
        source_feeds=feed,
        journal=dict(
            schema="chimera.run-journal-config/1",
            directory=str(tmp_path / "journal"),
            max_record_bytes=100000,
            max_journal_bytes=1000000,
            max_summary_bytes=100000,
            max_records=100,
        ),
    )
    cfg = GhimeraConfig.model_validate(raw)
    reading = parse_feed(
        b'<rss version="2.0"><channel><title>ports</title><item><title>native source</title>'
        b"<description>ports evidence remains native</description></item></channel></rss>",
        "https://example.org/feed",
        "application/rss+xml",
        cfg.source_feeds,
    )
    doc = Extracted(
        title=reading.reading_title,
        text=reading.text,
        language="und",
        links=(),
        source_feed=reading,
    )
    return cfg, Goal(text="ports"), doc


class EncoderFixture:
    def __init__(self, cfg, contacts):
        self.config, self.contacts = cfg, contacts
        self.model = ModelIdentity(
            model_id=cfg.model_id, revision=cfg.revision, location="self_hosted"
        )

    async def encode_batch(self, texts):
        with self.contacts.open("ab") as stream:
            stream.write(encoding_request(self.config, texts) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        return EncodingBatch(
            vectors=((1.0, 0.0),) * len(texts),
            call=EncodingCall(
                schema="chimera.encoding-call/1",
                service=self.config,
                request_sha256=hashlib.sha256(encoding_request(self.config, texts)).hexdigest(),
                response_sha256=hashlib.sha256(b"controlled-vector-fixture").hexdigest(),
                response_bytes=25,
                input_sha256=tuple(
                    hashlib.sha256((self.config.text_prefix + text).encode()).hexdigest()
                    for text in texts
                ),
                input_chars=sum(len(self.config.text_prefix) + len(text) for text in texts),
                status=200,
                latency_seconds=0.0,
                usage=dict(prompt_tokens=1, total_tokens=1),
                outcome="success",
            ),
        )


class CrashSink(DirectoryLedgerSink):
    def __init__(self, *args, crash=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.crash = crash

    def append(self, row):
        super().append(row)
        if self.crash == "intent" and row.run_encoding_intent is not None:
            os._exit(73)
        if self.crash == "ack" and row.run_encoding_ack is not None:
            os._exit(74)
        if self.crash == "source_ack" and row.run_encoding_ack is not None:
            intent = self.committed_rows[
                row.run_encoding_ack.original_intent_sequence
            ].run_encoding_intent
            if intent is not None and intent.purpose == "source":
                os._exit(75)


def exercise(root, mode):
    cfg = GhimeraConfig.model_validate_json((root / "config.json").read_text())
    goal = Goal(text="ports")
    doc = Extracted.model_validate_json((root / "document.json").read_text())
    restored = (
        () if mode in {"intent", "ack", "source_ack"} else read_journal(cfg.journal, "run").rows
    )
    ledger = Ledger(
        sink=CrashSink(
            cfg,
            "run",
            goal,
            FakeJudge().model,
            resume_rows=restored if restored else None,
            crash=mode,
        ),
        restored_rows=restored,
    )
    if not restored:
        ledger.append(
            LedgerRow(
                sequence=0,
                event="extraction",
                url=doc.source_feed.source_url,
                reason="source-feed-parser/1",
                source_feed=doc.source_feed,
            )
        )
    budget = RunBudget(cfg, lambda: 0.0)
    if restored:
        calls, chars = encoding_usage(cfg, restored)
        receipt = Receipt(
            fetches=0,
            bytes_read=0,
            judge_calls=0,
            encoding_calls=calls,
            encoding_chars=chars,
            accepted_documents=0,
            elapsed_seconds=1.0,
            stop_reason="failed",
            effective_config=cfg,
            judge=FakeJudge().model,
        )
        budget.restore(receipt, restored, 0, 2.0)
    decisions = None
    if mode == "replay":
        if any(row.intent_reference is not None for row in restored):
            original = next(
                row.sequence
                for row in restored
                if row.run_encoding_intent is not None
                and row.run_encoding_intent.purpose == "source"
            )
            decisions = (RunEncodingDecision(mode="replay", original_intent_sequence=original),)
        else:
            original = next(row.sequence for row in restored if row.run_encoding_intent is not None)
            decisions = (
                RunEncodingDecision(mode="replay", original_intent_sequence=original),
                RunEncodingDecision(mode="fresh"),
            )
    scorer = EmbeddingScorer(
        cfg.scoring,
        EncoderFixture(cfg.scoring.encoder, root / "contacts"),
        query_encoder=EncoderFixture(cfg.scoring.query_encoder, root / "contacts"),
        encoding_decisions=decisions,
    )
    try:
        asyncio.run(scorer.score(goal, doc, budget, ledger))
    finally:
        ledger.close()


def launch(root, mode):
    return subprocess.run(
        [sys.executable, "-m", "tests.test_run_encoding", str(root), mode],
        capture_output=True,
        text=True,
        timeout=25,
    )


@pytest.mark.parametrize(
    "cut,exitcode,contacts", [("intent", 73, 0), ("ack", 74, 1), ("source_ack", 75, 2)]
)
def test_actual_process_death_charges_original_and_explicit_ack_replay(
    tmp_path, cut, exitcode, contacts
):
    cfg, _, doc = prepared(tmp_path)
    (tmp_path / "config.json").write_text(cfg.model_dump_json())
    (tmp_path / "document.json").write_text(doc.model_dump_json())
    result = launch(tmp_path, cut)
    assert result.returncode == exitcode, result.stderr
    report = read_journal(cfg.journal, "run")
    assert report.state == "unsealed" and not report.incomplete_tail
    calls, chars = encoding_usage(cfg, report.rows)
    assert calls == (2 if cut == "source_ack" else 1)
    assert chars >= len("query: ports")
    if contacts:
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == contacts
    else:
        assert not (tmp_path / "contacts").exists()
    resumed = launch(tmp_path, "fresh" if cut == "intent" else "replay")
    if cut == "intent":
        assert resumed.returncode != 0 and "unknown" in resumed.stderr.lower()
        assert not (tmp_path / "contacts").exists()
    else:
        assert resumed.returncode == 0, resumed.stderr
        replayed = read_journal(cfg.journal, "run")
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == 2
        assert encoding_usage(cfg, replayed.rows)[0] == 2
        assert sum(row.run_encoding_replay is not None for row in replayed.rows) == 1
        receipt = Receipt(
            fetches=0,
            bytes_read=0,
            judge_calls=0,
            encoding_calls=2,
            encoding_chars=encoding_usage(cfg, replayed.rows)[1],
            accepted_documents=0,
            elapsed_seconds=3.0,
            stop_reason="failed",
            effective_config=cfg,
            judge=FakeJudge().model,
        )
        harvest = Harvest(
            schema="chimera.harvest/1",
            goal=report.header.goal,
            documents=(),
            ledger=replayed.rows,
            receipt=receipt,
        )
        assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
        restored = RunBudget(cfg, lambda: 0.0)
        restored.restore(receipt, replayed.rows, 0, 4.0)
        assert restored.encoding_calls == 2 and restored.elapsed == 7.0


def test_legacy_profiles_preserve_dump_and_deny_even_null_new_policy():
    cfg = service(9)
    old = policy(cfg, references(cfg))
    assert hashlib.sha256(old.model_dump_json().encode()).hexdigest() == (
        "419ab26d19321a8ac22a3cdc36a0f9301d81fe551194a006eb4346c3c9577d4a"
    )
    old_two = intent_policy(
        cfg, schema="ghimera.scoring/2", query_encoder=service(9, text_prefix="query: ")
    )
    assert hashlib.sha256(old_two.model_dump_json().encode()).hexdigest() == (
        "37c2e80521b9b14d7ae408a2e624c9b4f022b73ece57ccdb400b9d50751fdcde"
    )
    assert "run_encoding_recovery" not in old.model_dump()
    for version in ("chimera.scoring/1", "ghimera.scoring/2"):
        raw = old.model_dump()
        raw.update(schema=version, run_encoding_recovery=None)
        if version.endswith("/2"):
            raw.update(reference_source="intent", references_sha256=None, query_encoder=cfg)
        with pytest.raises(ValidationError):
            type(old).model_validate(raw)


def test_missing_real_native_journal_refuses_before_contact(tmp_path):
    cfg, goal, doc = prepared(tmp_path)
    encoder = EncoderFixture(cfg.scoring.encoder, tmp_path / "contacts")
    query = EncoderFixture(cfg.scoring.query_encoder, tmp_path / "contacts")
    scorer = EmbeddingScorer(cfg.scoring, encoder, query_encoder=query)
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(scorer.score(goal, doc, RunBudget(cfg, lambda: 0.0), Ledger()))
    assert not (tmp_path / "contacts").exists()


def live(tmp_path, *, calls=20, recovery_updates=None):
    cfg, goal, doc = prepared(tmp_path, calls=calls, recovery_updates=recovery_updates)
    ledger = Ledger(sink=DirectoryLedgerSink(cfg, "live", goal, FakeJudge().model))
    ledger.append(
        LedgerRow(
            sequence=0,
            event="extraction",
            url=doc.source_feed.source_url,
            reason="source-feed-parser/1",
            source_feed=doc.source_feed,
        )
    )
    budget = RunBudget(cfg, lambda: 0.0)
    scorer = EmbeddingScorer(
        cfg.scoring,
        EncoderFixture(cfg.scoring.encoder, tmp_path / "contacts"),
        query_encoder=EncoderFixture(cfg.scoring.query_encoder, tmp_path / "contacts"),
    )
    return cfg, goal, doc, ledger, budget, scorer


def test_original_quota_and_unknown_receipt_cannot_reset(tmp_path):
    cfg, goal, doc, ledger, budget, scorer = live(tmp_path, calls=1)
    try:
        with pytest.raises(GhimeraRefused, match="budget_exhausted"):
            asyncio.run(scorer.score(goal, doc, budget, ledger))
        assert budget.encoding_calls == 1
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == 1
        assert encoding_usage(cfg, ledger.snapshot())[0] == 1
        with pytest.raises(ValueError, match="counters"):
            asyncio.run(scorer.score(goal, doc, RunBudget(cfg, lambda: 0.0), ledger))
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == 1
    finally:
        ledger.close()


@pytest.mark.parametrize("drift", ["source", "reading", "purpose", "output", "header", "scope"])
def test_native_readback_refuses_original_identity_drift(tmp_path, drift):
    cfg, goal, doc, ledger, budget, scorer = live(tmp_path)
    try:
        asyncio.run(scorer.score(goal, doc, budget, ledger))
        rows = list(ledger.snapshot())
        original = next(row for row in rows if row.run_encoding_intent is not None)
        intent = original.run_encoding_intent
        raw = original.model_dump()
        if drift == "source":
            raw["run_encoding_intent"]["scope"]["source"]["source_sha256"] = "a" * 64
        elif drift == "reading":
            raw["run_encoding_intent"]["scope"]["source"]["reading_sequence"] = 0
        elif drift == "purpose":
            raw["run_encoding_intent"]["purpose"] = "source"
        elif drift == "header":
            raw["run_encoding_intent"]["header_sha256"] = "a" * 64
        elif drift == "scope":
            raw["run_encoding_intent"]["scope"]["document_sha256"] = "a" * 64
        else:
            acknowledged = next(row for row in rows if row.run_encoding_ack is not None)
            raw_ack = acknowledged.model_dump()
            raw_ack["run_encoding_ack"]["result"]["vectors"] = ((0.0, 1.0),)
            with pytest.raises(ValidationError, match="exact original"):
                LedgerRow.model_validate(raw_ack)
            return
        rows[original.sequence] = LedgerRow.model_validate(raw)
        with pytest.raises(ValueError):
            validate_run_encoding_rows(cfg, tuple(rows), header=ledger.run_header(cfg))
        assert intent is not None and budget.encoding_calls == 2
    finally:
        ledger.close()


def test_explicit_replay_schedule_cannot_change_source_or_replay_twice(tmp_path):
    cfg, goal, doc, ledger, budget, scorer = live(tmp_path)
    try:
        asyncio.run(scorer.score(goal, doc, budget, ledger))
        source = next(
            row.sequence
            for row in ledger.snapshot()
            if row.run_encoding_intent is not None and row.run_encoding_intent.purpose == "source"
        )
        replay = EmbeddingScorer(
            cfg.scoring,
            EncoderFixture(cfg.scoring.encoder, tmp_path / "contacts"),
            query_encoder=EncoderFixture(cfg.scoring.query_encoder, tmp_path / "contacts"),
            encoding_decisions=(
                RunEncodingDecision(mode="replay", original_intent_sequence=source),
            ),
        )
        with pytest.raises(ValueError, match="source/goal/reading"):
            asyncio.run(
                replay.score(goal, doc.model_copy(update={"title": "changed"}), budget, ledger)
            )
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == 2
        asyncio.run(replay.score(goal, doc, budget, ledger))
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == 2
        assert budget.encoding_calls == 2
        double = EmbeddingScorer(
            cfg.scoring,
            EncoderFixture(cfg.scoring.encoder, tmp_path / "contacts"),
            query_encoder=EncoderFixture(cfg.scoring.query_encoder, tmp_path / "contacts"),
            encoding_decisions=(
                RunEncodingDecision(mode="replay", original_intent_sequence=source),
            )
            * 2,
        )
        with pytest.raises(ValueError, match="entire batch plan"):
            asyncio.run(double.score(goal, doc, budget, ledger))
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == 2
    finally:
        ledger.close()


def test_unknown_native_harvest_and_restoration_keep_original_charge(tmp_path):
    cfg, goal, doc = prepared(tmp_path)
    (tmp_path / "config.json").write_text(cfg.model_dump_json())
    (tmp_path / "document.json").write_text(doc.model_dump_json())
    assert launch(tmp_path, "intent").returncode == 73
    report = read_journal(cfg.journal, "run")
    calls, chars = encoding_usage(cfg, report.rows)
    receipt = Receipt(
        fetches=0,
        bytes_read=0,
        judge_calls=0,
        encoding_calls=calls,
        encoding_chars=chars,
        accepted_documents=0,
        elapsed_seconds=3.0,
        stop_reason="failed",
        effective_config=cfg,
        judge=FakeJudge().model,
    )
    result = Harvest(
        schema="chimera.harvest/1", goal=goal, documents=(), ledger=report.rows, receipt=receipt
    )
    assert Harvest.model_validate_json(result.model_dump_json()) == result
    budget = RunBudget(cfg, lambda: 0.0)
    budget.restore(receipt, report.rows, 0, 4.0)
    assert (budget.encoding_calls, budget.encoding_chars, budget.elapsed) == (1, chars, 7.0)
    with pytest.raises(ValueError, match="original charged intents"):
        RunBudget(cfg, lambda: 0.0).restore(
            receipt.model_copy(update={"encoding_calls": 0}), report.rows, 0, 4.0
        )


@pytest.mark.parametrize("failure", ["nonfinite", "request", "refused", "cancelled"])
def test_invalid_or_refused_original_output_stays_charged_without_vector_ack(tmp_path, failure):
    cfg, goal, doc, ledger, budget, _ = live(tmp_path)

    class Broken(EncoderFixture):
        async def encode_batch(self, texts):
            original = await super().encode_batch(texts)
            if failure == "nonfinite":
                return original.model_copy(update={"vectors": ((float("nan"), 0.0),)})
            if failure == "request":
                return original.model_copy(
                    update={"call": original.call.model_copy(update={"request_sha256": "a" * 64})}
                )
            if failure == "cancelled":
                raise asyncio.CancelledError()
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    scorer = EmbeddingScorer(
        cfg.scoring,
        EncoderFixture(cfg.scoring.encoder, tmp_path / "contacts"),
        query_encoder=Broken(cfg.scoring.query_encoder, tmp_path / "contacts"),
    )
    try:
        with pytest.raises((GhimeraRefused, asyncio.CancelledError)):
            asyncio.run(scorer.score(goal, doc, budget, ledger))
        assert encoding_usage(cfg, ledger.snapshot())[0] == budget.encoding_calls == 1
        assert not any(row.run_encoding_ack is not None for row in ledger.snapshot())
        with pytest.raises(ValueError, match="unknown"):
            asyncio.run(scorer.score(goal, doc, budget, ledger))
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == 1
    finally:
        ledger.close()
    assert read_journal(cfg.journal, "live").state == "unsealed"


def test_native_capacity_refuses_before_reservation_or_contact(tmp_path):
    cfg, goal, doc = prepared(tmp_path)
    raw = cfg.model_dump()
    raw["journal"]["max_records"] = 2
    cfg = GhimeraConfig.model_validate(raw)
    ledger = Ledger(sink=DirectoryLedgerSink(cfg, "capacity", goal, FakeJudge().model))
    ledger.append(
        LedgerRow(
            sequence=0,
            event="extraction",
            url=doc.source_feed.source_url,
            reason="source-feed-parser/1",
            source_feed=doc.source_feed,
        )
    )
    scorer = EmbeddingScorer(
        cfg.scoring,
        EncoderFixture(cfg.scoring.encoder, tmp_path / "contacts"),
        query_encoder=EncoderFixture(cfg.scoring.query_encoder, tmp_path / "contacts"),
    )
    budget = RunBudget(cfg, lambda: 0.0)
    try:
        with pytest.raises(GhimeraRefused, match="ledger_sink_failed"):
            asyncio.run(scorer.score(goal, doc, budget, ledger))
        assert budget.encoding_calls == 0 and not (tmp_path / "contacts").exists()
    finally:
        ledger.close()


def test_new_mode_requires_explicit_policy_and_real_configured_journal(tmp_path):
    cfg, _, _ = prepared(tmp_path)
    raw = cfg.scoring.model_dump()
    raw.pop("run_encoding_recovery")
    with pytest.raises(ValidationError, match="explicit run-bound"):
        type(cfg.scoring).model_validate(raw)
    raw = cfg.model_dump()
    raw.pop("journal")
    with pytest.raises(ValidationError, match="configured run journal"):
        GhimeraConfig.model_validate(raw)


def test_inert_example_is_valid_and_explicit():
    from ghimera.scoring_config import ScoringConfig

    policy = ScoringConfig.model_validate(
        tomllib.loads(Path("examples/run-encoding.toml").read_text())
    )
    assert policy.schema_version == "ghimera.scoring/3"
    assert policy.run_encoding_recovery.unknown_policy == "hold"
    assert policy.encoder.endpoint.endswith(".invalid/v1/embeddings")


def test_concurrent_native_run_scores_share_intent_and_keep_owned_ack_order(tmp_path):
    cfg, goal, doc, ledger, budget, scorer = live(tmp_path)

    async def concurrent():
        return await asyncio.gather(
            scorer.score(goal, doc, budget, ledger), scorer.score(goal, doc, budget, ledger)
        )

    try:
        assert asyncio.run(concurrent()) == [(), ()]
        rows = ledger.snapshot()
        assert encoding_usage(cfg, rows)[0] == budget.encoding_calls == 3
        assert tuple(
            row.run_encoding_intent.reserved_call
            for row in rows
            if row.run_encoding_intent is not None
        ) == (1, 2, 3)
        assert sum(row.run_encoding_ack is not None for row in rows) == 3
        assert sum(row.intent_reference is not None for row in rows) == 1
        assert sum(row.similarity is not None for row in rows) == 2
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == 3
        assert not budget.encoding_score_lock.locked() and not budget.encoding_lock.locked()
    finally:
        ledger.close()
    assert read_journal(cfg.journal, "live").rows == rows


def test_cancelled_owned_encoding_releases_schedule_lock_but_not_charge(tmp_path):
    cfg, goal, doc, ledger, budget, _ = live(tmp_path)
    contacted = asyncio.Event()

    class Paused(EncoderFixture):
        async def encode_batch(self, texts):
            await super().encode_batch(texts)
            contacted.set()
            await asyncio.Event().wait()

    scorer = EmbeddingScorer(
        cfg.scoring,
        EncoderFixture(cfg.scoring.encoder, tmp_path / "contacts"),
        query_encoder=Paused(cfg.scoring.query_encoder, tmp_path / "contacts"),
    )

    async def cancelled():
        task = asyncio.create_task(scorer.score(goal, doc, budget, ledger))
        await asyncio.wait_for(contacted.wait(), 2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not budget.encoding_score_lock.locked() and not budget.encoding_lock.locked()
        with pytest.raises(ValueError, match="unknown"):
            await scorer.score(goal, doc, budget, ledger)

    try:
        asyncio.run(cancelled())
        assert budget.encoding_calls == encoding_usage(cfg, ledger.snapshot())[0] == 1
        assert not any(row.run_encoding_ack is not None for row in ledger.snapshot())
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == 1
    finally:
        ledger.close()


@pytest.mark.parametrize(
    "limits,contact", [({"max_result_bytes": 128}, True), ({"max_total_result_bytes": 20000}, True)]
)
def test_result_and_total_retained_bounds_never_fabricate_vector_ack(tmp_path, limits, contact):
    cfg, goal, doc, ledger, budget, scorer = live(tmp_path, recovery_updates=limits)
    try:
        with pytest.raises(GhimeraRefused):
            asyncio.run(scorer.score(goal, doc, budget, ledger))
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == 1
        assert budget.encoding_calls == encoding_usage(cfg, ledger.snapshot())[0] == 1
        if "max_result_bytes" in limits:
            assert not any(row.run_encoding_ack is not None for row in ledger.snapshot())
            call = next(
                row.encoding_call for row in ledger.snapshot() if row.encoding_call is not None
            )
            assert call.status == 200 and call.telemetry == "observed" and call.outcome == "refused"
        else:
            assert sum(row.run_encoding_ack is not None for row in ledger.snapshot()) == 1
    finally:
        ledger.close()


def test_tampered_fresh_decision_with_original_sequence_refuses_before_contact(tmp_path):
    cfg, goal, doc, ledger, budget, scorer = live(tmp_path)
    try:
        asyncio.run(scorer.score(goal, doc, budget, ledger))
        source = next(
            row.sequence
            for row in ledger.snapshot()
            if row.run_encoding_intent is not None and row.run_encoding_intent.purpose == "source"
        )
        malformed = RunEncodingDecision(mode="replay", original_intent_sequence=source).model_copy(
            update={"mode": "fresh"}
        )
        changed = EmbeddingScorer(
            cfg.scoring,
            EncoderFixture(cfg.scoring.encoder, tmp_path / "contacts"),
            query_encoder=EncoderFixture(cfg.scoring.query_encoder, tmp_path / "contacts"),
            encoding_decisions=(malformed,),
        )
        with pytest.raises(ValidationError, match="fresh forbids"):
            asyncio.run(changed.score(goal, doc, budget, ledger))
        assert len((tmp_path / "contacts").read_bytes().splitlines()) == 2
        assert encoding_usage(cfg, ledger.snapshot())[0] == budget.encoding_calls == 2
    finally:
        ledger.close()


def test_run_intent_mode_requires_explicit_query_encoder(tmp_path):
    cfg, _, _ = prepared(tmp_path)
    raw = cfg.scoring.model_dump()
    raw.pop("query_encoder")
    with pytest.raises(ValidationError, match="explicit query encoder"):
        type(cfg.scoring).model_validate(raw)


def test_run_pinned_mode_keeps_no_query_recipe_and_original_reference_identity(tmp_path):
    cfg, _, _ = prepared(tmp_path)
    raw = cfg.scoring.model_dump()
    raw.pop("query_encoder")
    raw.update(reference_source="pinned", references_sha256=references(cfg.scoring.encoder).sha256)
    pinned = type(cfg.scoring).model_validate(raw)
    assert pinned.query_encoder is None and "query_encoder" not in pinned.model_dump()
    assert pinned.references_sha256 == references(cfg.scoring.encoder).sha256


if __name__ == "__main__":
    exercise(Path(sys.argv[1]), sys.argv[2])
