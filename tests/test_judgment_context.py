"""Native protocol/provenance witnesses, never real model-quality acceptance.

The retained fixture contains the WHOLE unchanged 24,400-character native reading,
vendor layout and original scoring observations from the public constitution PDF.
No new model/encoder/source contact is made. Responses below are protocol fixtures.
"""

import asyncio
import hashlib
import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor
from ghimera.embedding import SelfHostedEncoder
from ghimera.journal import DirectoryLedgerSink, read_journal
from ghimera.judgment_context import (
    native_scoring_reading,
    scoring_source_binding,
    select_scored_windows,
)
from ghimera.judgment_validation import (
    validate_judgment_rows,
    validate_scored_context,
    validate_scoring_readings,
)
from ghimera.ledger import Ledger
from ghimera.loop import CollectionSession, GoalLoop
from ghimera.model_client import SelfHostedModel
from ghimera.model_http import ModelHttpResponse
from ghimera.model_work import uncertain_model_sequences
from ghimera.models import Extracted, Goal, LedgerRow, Verdict
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_scoring import EmbeddingScorer
from tests.test_c0 import config as legacy_config
from tests.test_served_models import service

POLICY = dict(
    schema="ghimera.document-judgment/1",
    selection="scored_native_windows",
    max_windows=3,
    first_look_max_chars=1500,
    expanded_look_max_chars=4000,
    padding_chars=450,
    incomplete_rejection="hold",
)


def retained():
    return json.loads(
        (Path(__file__).parent / "fixtures/retained_organization_judgment.json").read_text()
    )


def prepared(tmp_path, *, before_scoring=False, journal_updates=None, judge_budget=None):
    actual = retained()
    bound = service(
        9,
        context={
            "schema": "chimera.evidence-context/1",
            "max_documents": 1,
            "max_chars": 4000,
            "window_chars": 1500,
            "max_windows_per_document": 3,
            "overlap_chars": 100,
        },
    )
    raw = actual["config"]
    raw["models"] = {
        "schema": "chimera.model-bindings/1",
        **{role: bound.model_dump() for role in ("judge", "planner", "analyst", "reviewer")},
    }
    raw["journal"]["directory"] = str(tmp_path / "journal")
    raw["journal"].update(journal_updates or {})
    if journal_updates:
        raw["model_work"]["results"]["max_result_bytes"] = 10000
    if judge_budget is not None:
        raw["judge_budget"] = judge_budget
    raw["document_judgment"] = POLICY
    cfg = GhimeraConfig.model_validate(raw)
    goal, extracted = (
        Goal.model_validate(actual["goal"]),
        Extracted.model_validate(actual["extracted"]),
    )
    judge = SelfHostedModel(cfg, bound, http=Reply(bound))
    ledger = Ledger(sink=DirectoryLedgerSink(cfg, "native-reading", goal, judge.model))
    for row in actual["rows"]:
        ledger.append(LedgerRow.model_validate(row))
    if before_scoring:
        return cfg, goal, extracted, ledger, judge
    reading = native_scoring_reading(ledger.snapshot(), extracted)
    ledger.append(
        LedgerRow(
            sequence=ledger.next_sequence,
            event="scoring_source",
            url=reading.source_url,
            scoring_reading=reading,
            reason="private_native_reading_before_encoding_not_accepted_content",
        )
    )
    score = LedgerRow.model_validate(actual["rows"][-1]).model_copy(
        update={
            "sequence": ledger.next_sequence,
            "scoring_source": scoring_source_binding(ledger.snapshot(), extracted),
        }
    )
    ledger.append(LedgerRow.model_validate(score.model_dump()))
    return cfg, goal, extracted, ledger, judge


class Reply:
    def __init__(self, bound, *, unknown=False):
        self.config, self.requests, self.unknown = bound, [], unknown

    async def post(self, body):
        self.requests.append(json.loads(body))
        if self.unknown:
            raise RuntimeError("protocol fixture lost reply")
        return ModelHttpResponse(
            200,
            json.dumps(
                {
                    "id": "fixture-only-judgment",
                    "model": self.config.served_model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "decision": "reject",
                                        "kind": "protocol fixture",
                                        "publisher": "protocol fixture",
                                        "language": "zh",
                                        "reason": "fixture rejected only its excerpts",
                                    }
                                ),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode(),
            "application/json",
        )


def selected(cfg, goal, original, rows, **changes):
    proof = rows[-1].scoring_source
    assert proof is not None
    arguments = dict(
        rows=rows,
        source_url=proof.source_url,
        source_sha256=proof.source_sha256,
        extracted=original,
        goal_text=goal.text,
        scoring_row=rows[-1],
        max_windows=3,
        max_chars=1500,
        window_chars=1500,
        padding_chars=450,
    )
    arguments.update(changes)
    return select_scored_windows(**arguments)


def loop_for(cfg, judge):
    # No fetch route is used by the private native verdict owner in these tests.
    encoder = SelfHostedEncoder(cfg.scoring.encoder, http=NoEncoding(cfg.scoring.encoder))
    query_encoder = SelfHostedEncoder(
        cfg.scoring.query_encoder, http=NoEncoding(cfg.scoring.query_encoder)
    )
    return GoalLoop(
        config=cfg,
        fetcher=None,
        extractor=FakeExtractor(),
        judge=judge,
        scorer=EmbeddingScorer(cfg.scoring, encoder, query_encoder=query_encoder),
        semantic_extractor=judge,
    )


class NoEncoding:
    def __init__(self, bound):
        self.config, self.calls = bound, 0

    async def post(self, body):
        self.calls += 1
        raise AssertionError("encoder contact is forbidden in retained-score witnesses")


def test_private_reading_capacity_refuses_before_encoder_or_judge(tmp_path):
    cfg, goal, extracted, ledger, judge = prepared(
        tmp_path, before_scoring=True, journal_updates={"max_record_bytes": 50000}
    )
    native = native_scoring_reading(ledger.snapshot(), extracted)
    assert len(native.model_dump_json().encode()) > cfg.journal.max_record_bytes
    encoder_http, query_http = (
        NoEncoding(cfg.scoring.encoder),
        NoEncoding(cfg.scoring.query_encoder),
    )
    scorer = EmbeddingScorer(
        cfg.scoring,
        SelfHostedEncoder(cfg.scoring.encoder, http=encoder_http),
        query_encoder=SelfHostedEncoder(cfg.scoring.query_encoder, http=query_http),
    )
    budget = RunBudget(cfg, time.monotonic)
    try:
        with pytest.raises(GhimeraRefused):
            asyncio.run(scorer.rank(goal, extracted, budget, ledger))
        assert encoder_http.calls == query_http.calls == budget.judge_calls == 0
        assert budget.encoding_calls == 0 and not judge._http.requests
        assert not any(row.scoring_reading for row in ledger.snapshot())
    finally:
        ledger.close()


def test_private_reading_without_opt_in_and_orphan_stale_parser_refused(tmp_path):
    cfg, _, _, ledger, _ = prepared(tmp_path)
    try:
        rows = ledger.snapshot()[:-1]  # Full reading retained but no score/intent/ACK yet.
        validate_scoring_readings(cfg, rows)
        raw = cfg.model_dump()
        raw.pop("document_judgment")
        disabled = GhimeraConfig.model_validate(raw)
        with pytest.raises(ValueError, match="explicit native selection"):
            validate_scoring_readings(disabled, rows)
        bad = rows[-1].scoring_reading.model_copy(update={"parser_sha256": "a" * 64})
        rows = rows[:-1] + (rows[-1].model_copy(update={"scoring_reading": bad}),)
        with pytest.raises(ValueError, match="parser evidence"):
            validate_scoring_readings(cfg, rows)
    finally:
        ledger.close()


def test_real_native_offsets_select_article23_and_keep_whole_source(tmp_path):
    cfg, goal, extracted, ledger, judge = prepared(tmp_path)
    try:
        assert len(extracted.text) == 24400
        assert extracted.text.index("第二十三条") == 15936
        assert hashlib.sha256(extracted.text.encode()).hexdigest() == (
            "5e6f0135e95fb1977eb4df4a75a7136155fdf5d4bbcfdf95594a305d7c58f936"
        )
        context = selected(cfg, goal, extracted, ledger.snapshot())
        assert any(window.start <= 15936 < window.end for window in context.windows)
        assert context.selected_chars <= 1500
        assert context.selected_chars + context.omitted_chars == 24400
        assert sum(len(window.text) for window in context.windows) == context.selected_chars
        validate_scored_context(
            ledger.snapshot(),
            context,
            goal.text,
            max_windows=3,
            max_chars=1500,
            window_chars=1500,
            padding_chars=450,
        )
        assert not judge._http.requests
    finally:
        ledger.close()


@pytest.mark.parametrize("defect", ["source", "text", "goal", "reference", "parser", "legacy"])
def test_drift_and_legacy_scoring_refused_before_contact(tmp_path, defect):
    cfg, goal, extracted, ledger, judge = prepared(tmp_path)
    try:
        rows = ledger.snapshot()
        changes = {}
        if defect == "source":
            changes["source_sha256"] = "a" * 64
        elif defect == "text":
            changes["extracted"] = extracted.model_copy(update={"text": "x" + extracted.text[1:]})
        elif defect == "goal":
            changes["goal_text"] = "different original intent"
        elif defect == "reference":
            similarity = rows[-1].similarity
            similarity = similarity.model_copy(
                update={
                    "windows": tuple(
                        seed.model_copy(update={"reference_text_sha256": "b" * 64})
                        for seed in similarity.windows
                    )
                }
            )
            changes["scoring_row"] = rows[-1].model_copy(update={"similarity": similarity})
            rows = rows[:-1] + (changes["scoring_row"],)
        elif defect == "parser":
            changes["extracted"] = extracted.model_copy(
                update={
                    "document_parse": extracted.document_parse.model_copy(
                        update={"config_digest": "c" * 64}
                    )
                }
            )
        else:
            changes["scoring_row"] = rows[16]
        with pytest.raises(ValueError):
            selected(cfg, goal, extracted, rows, **changes)
        assert not judge._http.requests
    finally:
        ledger.close()


def test_identical_text_wrong_raw_source_cannot_borrow_scoring(tmp_path):
    cfg, goal, extracted, ledger, judge = prepared(tmp_path)
    try:
        foreign = extracted.model_copy(
            update={
                "document_parse": extracted.document_parse.model_copy(
                    update={"source_sha256": "d" * 64}
                )
            }
        )
        with pytest.raises(ValueError):
            selected(cfg, goal, foreign, ledger.snapshot(), source_sha256="d" * 64)
        assert not judge._http.requests
    finally:
        ledger.close()


def test_native_ack_remains_reject_but_partial_reading_client_holds(tmp_path):
    cfg, goal, extracted, ledger, judge = prepared(tmp_path)
    budget = RunBudget(cfg, time.monotonic)
    session = CollectionSession(goal, budget, ledger, None)
    loop = loop_for(cfg, judge)
    try:
        for second in (False, True):
            verdict, disposition = asyncio.run(
                loop._document_verdict(
                    session,
                    extracted,
                    extracted.document_parse.source_url,
                    second,
                    source_sha256=extracted.document_parse.source_sha256,
                )
            )
            assert verdict.decision == "reject" and disposition == "hold"
            evidence = ledger.snapshot()[-1].document_judgment
            assert evidence.original_model_decision == "reject"
            assert evidence.client_disposition == "hold"
            ack = ledger.snapshot()[evidence.ack_sequence].model_ack
            assert Verdict.model_validate_json(ack.stored_output.body()) == verdict
            packet = json.loads(judge._http.requests[-1]["messages"][1]["content"])
            assert "document" not in packet
            assert "native_text" not in json.dumps(packet)
            assert packet["scored_document"]["omitted_chars"] > 0
            assert verdict.model_call.selected_spans == tuple(
                (evidence.context.source_sha256, window.start, window.end)
                for window in evidence.context.windows
            )
        assert budget.judge_calls == 2
        validate_judgment_rows(cfg, goal.text, ledger.snapshot())
        rows = ledger.snapshot()
        tampered = rows[-1].document_judgment.model_copy(update={"client_disposition": "accept"})
        with pytest.raises(ValueError, match="original model decision"):
            validate_judgment_rows(
                cfg,
                goal.text,
                rows[:-1] + (rows[-1].model_copy(update={"document_judgment": tampered}),),
            )
    finally:
        session.close()
    report = read_journal(cfg.journal, "native-reading")
    assert (
        report.state == "unsealed"
        and report.rows[-1].document_judgment.client_disposition == "hold"
    )


def test_native_unknown_is_charged_and_survives_journal_reopen(tmp_path):
    cfg, goal, extracted, ledger, judge = prepared(tmp_path)
    transport = Reply(cfg.models.judge, unknown=True)
    judge = SelfHostedModel(cfg, cfg.models.judge, http=transport)
    budget = RunBudget(cfg, time.monotonic)
    session = CollectionSession(goal, budget, ledger, None)
    try:
        with pytest.raises(RuntimeError, match="lost reply"):
            asyncio.run(
                loop_for(cfg, judge)._document_verdict(
                    session,
                    extracted,
                    extracted.document_parse.source_url,
                    False,
                    source_sha256=extracted.document_parse.source_sha256,
                )
            )
        assert budget.judge_calls == 1 and len(transport.requests) == 1
        assert len(uncertain_model_sequences(ledger.snapshot())) == 1
    finally:
        session.close()
    report = read_journal(cfg.journal, "native-reading")
    assert len(uncertain_model_sequences(report.rows)) == 1


def test_wrong_padding_with_coherent_local_hash_is_refused_on_replay(tmp_path):
    cfg, goal, extracted, ledger, judge = prepared(tmp_path)
    try:
        context = selected(cfg, goal, extracted, ledger.snapshot())
        window = context.windows[0]
        # Edit only padding outside scored anchors, retaining all real anchor hashes.
        text = "X" + window.text[1:]
        wrong = window.model_copy(
            update={"text": text, "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
        )
        context = context.model_copy(update={"windows": (wrong,) + context.windows[1:]})
        with pytest.raises(ValueError, match="padding"):
            validate_scored_context(
                ledger.snapshot(),
                context,
                goal.text,
                max_windows=3,
                max_chars=1500,
                window_chars=1500,
                padding_chars=450,
            )
        assert not judge._http.requests
    finally:
        ledger.close()


def test_disabled_recipe_bytes_and_wrong_policy_limits_are_preserved(tmp_path):
    assert "document_judgment" not in legacy_config().model_dump()
    assert (
        "judgment_context"
        not in LedgerRow(sequence=0, event="policy", reason="legacy").model_dump()
    )
    cfg, _, _, ledger, _ = prepared(tmp_path)
    ledger.close()
    raw = cfg.model_dump()
    raw["document_judgment"]["expanded_look_max_chars"] = 4001
    with pytest.raises(ValidationError, match="original scoring/judge context"):
        GhimeraConfig.model_validate(raw)
