"""Native retained model returns: replay is local evidence, not another service call."""

import asyncio
import base64
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeJudge
from ghimera.journal import DirectoryLedgerSink, read_journal
from ghimera.journal_types import JournalReport
from ghimera.ledger import Ledger
from ghimera.loop import CollectionSession
from ghimera.model_http import ModelHttpResponse
from ghimera.model_work import FatalModelWorkFailure, ModelInvocation
from ghimera.models import Goal, Receipt
from ghimera.refusals import GhimeraRefused
from ghimera.research import ModelCalls
from ghimera.research_types import PlanningRequest
from tests.test_intent_research import PlannerFixture
from tests.test_model_work import configured, invocation, opened


def receipt(cfg, calls):
    return Receipt(
        fetches=0,
        bytes_read=0,
        judge_calls=calls,
        accepted_documents=0,
        elapsed_seconds=0,
        stop_reason="budget_exhausted",
        effective_config=cfg,
        judge=FakeJudge().model,
    )


def retained_config(tmp_path, **bounds):
    raw = configured(tmp_path).model_dump(by_alias=True)
    results = dict(
        schema="ghimera.model-results/1",
        max_result_bytes=4096,
        max_total_result_bytes=16384,
    )
    results.update(bounds)
    raw["model_work"]["results"] = results
    return GhimeraConfig.model_validate(raw)


def reopen(cfg, report):
    budget = RunBudget(cfg, time.monotonic)
    budget.restore(receipt(cfg, len([r for r in report.rows if r.model_intent])), report.rows, 0, 0)
    sink = DirectoryLedgerSink(
        cfg, report.header.run_id, report.header.goal, report.header.judge, resume_rows=report.rows
    )
    return budget, Ledger(sink=sink, restored_rows=report.rows)


def test_budget_restore_rejects_inconsistent_call_count_before_mutating(tmp_path):
    cfg = configured(tmp_path)
    budget, ledger = opened(cfg)

    async def call():
        return "answer"

    asyncio.run(invocation(budget, ledger).invoke(call, str.encode))
    ledger.close()
    rows = read_journal(cfg.journal, "operation").rows
    restored = RunBudget(cfg, time.monotonic)
    started = restored.started
    with pytest.raises(ValueError, match="reservation"):
        restored.restore(receipt(cfg, 0), rows, 0, 0)
    assert restored.judge_calls == 0 and restored.started == started


def test_new_call_refuses_a_non_restored_budget_before_contact(tmp_path):
    cfg = configured(tmp_path)
    budget, ledger = opened(cfg)

    async def call():
        return "answer"

    asyncio.run(invocation(budget, ledger).invoke(call, str.encode))
    fresh = RunBudget(cfg, time.monotonic)
    before = ledger.snapshot()
    with pytest.raises(FatalModelWorkFailure, match="reservation"):
        invocation(fresh, ledger)
    assert ledger.snapshot() == before and fresh.judge_calls == 0
    ledger.close()


def test_acknowledged_payload_replays_from_native_reopened_run_without_contact(tmp_path):
    cfg = retained_config(tmp_path)
    budget, ledger = opened(cfg)
    contacts = []

    async def call():
        contacts.append(True)
        return "native answer 中文"

    expected = asyncio.run(invocation(budget, ledger).invoke(call, str.encode))
    ledger.close()
    report = read_journal(cfg.journal, "operation")
    assert report.rows[1].model_ack.stored_output.body() == expected.encode()
    restored, resumed = reopen(cfg, report)
    replay = ModelInvocation(
        restored,
        resumed,
        phase="plan",
        model=PlannerFixture.model,
        request=b"request",
        replay_intent_sequence=0,
    )
    observed = replay.replay(lambda stored: stored.body().decode())
    assert observed == expected and contacts == [True] and restored.judge_calls == 1
    with pytest.raises(FatalModelWorkFailure, match="reused"):
        replay.replay(lambda stored: stored.body().decode())
    resumed.close()
    final = read_journal(cfg.journal, "operation")
    assert [r.event for r in final.rows] == ["model_intent", "model_ack", "model_replay"]
    assert final.rows[-1].model_replay.ack_sequence == 1
    assert final.uncertain_model_calls == ()


@pytest.mark.parametrize("mismatch", ["request", "phase", "model", "url", "sequence"])
def test_replay_requires_original_input_phase_identity_and_sequence(tmp_path, mismatch):
    cfg = retained_config(tmp_path)
    budget, ledger = opened(cfg)

    async def call():
        return "answer"

    asyncio.run(invocation(budget, ledger).invoke(call, str.encode))
    args = dict(
        phase="plan", model=PlannerFixture.model, request=b"request", replay_intent_sequence=0
    )
    if mismatch == "request":
        args["request"] = b"changed"
    elif mismatch == "phase":
        args["phase"] = "assessment"
    elif mismatch == "model":
        args["model"] = PlannerFixture.model.model_copy(update={"revision": "other"})
    elif mismatch == "url":
        args["url"] = "https://other.example/page"
    else:
        args["replay_intent_sequence"] = 1
    before = ledger.snapshot()
    with pytest.raises(FatalModelWorkFailure):
        ModelInvocation(budget, ledger, **args)
    assert ledger.snapshot() == before and budget.judge_calls == 1
    ledger.close()


def test_result_quota_reserves_inflight_space_before_starting_another_call(tmp_path):
    cfg = retained_config(tmp_path, max_result_bytes=16, max_total_result_bytes=32)
    budget, ledger = opened(cfg)
    first, second = invocation(budget, ledger), invocation(budget, ledger)
    before = ledger.snapshot()
    with pytest.raises(GhimeraRefused):
        invocation(budget, ledger)
    assert ledger.snapshot() == before and budget.judge_calls == 2

    async def call():
        return "answer"

    asyncio.run(first.invoke(call, str.encode))
    asyncio.run(second.invoke(call, str.encode))
    third = invocation(budget, ledger)
    asyncio.run(third.invoke(call, str.encode))
    ledger.close()
    assert read_journal(cfg.journal, "operation").uncertain_model_calls == ()


def test_oversized_return_is_observed_but_held_not_truncated_or_replayable(tmp_path):
    cfg = retained_config(tmp_path, max_result_bytes=4, max_total_result_bytes=16)
    budget, ledger = opened(cfg)

    async def call():
        return "long answer"

    with pytest.raises(GhimeraRefused):
        asyncio.run(invocation(budget, ledger).invoke(call, str.encode))
    ledger.close()
    report = read_journal(cfg.journal, "operation")
    ack = report.rows[-1].model_ack
    assert ack.output_sha256 == hashlib.sha256(b"long answer").hexdigest()
    assert ack.stored_output is None and report.uncertain_model_calls == (0,)


def test_wire_replay_preserves_original_bytes_status_and_media_type(tmp_path):
    from ghimera.model_work import wire_observation

    cfg = retained_config(tmp_path)
    budget, ledger = opened(cfg)
    original = ModelHttpResponse(429, b'{"message":"try later"}', "application/json; charset=utf-8")

    async def call():
        return original

    first = invocation(budget, ledger, scope="wire_request")
    assert asyncio.run(first.invoke(call, wire_observation)) == original
    again = ModelInvocation(
        budget,
        ledger,
        phase="plan",
        model=PlannerFixture.model,
        request=b"request",
        scope="wire_request",
        replay_intent_sequence=0,
    )

    def decode(stored):
        return ModelHttpResponse(stored.wire.status, stored.body(), stored.wire.content_type)

    assert again.replay(decode) == original and budget.judge_calls == 1
    ledger.close()


def test_reader_rejects_tampered_payload_and_missing_declared_retention(tmp_path):
    cfg = retained_config(tmp_path)
    budget, ledger = opened(cfg)

    async def call():
        return "answer"

    asyncio.run(invocation(budget, ledger).invoke(call, str.encode))
    ledger.close()
    raw = read_journal(cfg.journal, "operation").model_dump(by_alias=True)
    raw["rows"][1]["model_ack"]["stored_output"]["body_base64"] = base64.b64encode(
        b"wrong"
    ).decode()
    with pytest.raises(ValidationError):
        JournalReport.model_validate(raw)
    raw = read_journal(cfg.journal, "operation").model_dump(by_alias=True)
    del raw["rows"][1]["model_ack"]["stored_output"]
    with pytest.raises(ValidationError):
        JournalReport.model_validate(raw)


def test_research_owner_decodes_retained_plan_without_invoking_its_planner(tmp_path):
    cfg = retained_config(tmp_path)
    budget, ledger = opened(cfg)
    session = CollectionSession(Goal(text="find ports"), budget, ledger, None)
    request = PlanningRequest(
        intent=session.goal.text,
        questions=(),
        documents=(),
        assessment=None,
        max_questions=cfg.research.max_questions,
        max_queries=cfg.research.max_queries_per_round,
        max_query_chars=cfg.research.max_query_chars,
    )
    calls = ModelCalls(session, cfg.research)
    expected = asyncio.run(
        calls.invoke("plan", PlannerFixture.model, request, PlannerFixture().plan)
    )
    session.close()
    restored, resumed = reopen(cfg, read_journal(cfg.journal, "operation"))
    owner = ModelCalls(CollectionSession(session.goal, restored, resumed, None), cfg.research)
    observed = owner.replay("plan", PlannerFixture.model, request, intent_sequence=0)
    assert observed == expected and restored.judge_calls == 1
    resumed.close()


def test_optional_retention_does_not_change_published_nonretaining_recipe(tmp_path):
    cfg = configured(tmp_path)
    assert "results" not in cfg.model_work.model_dump()
    raw = cfg.model_dump(by_alias=True)
    raw["model_work"]["results"] = dict(
        schema="ghimera.model-results/1",
        max_result_bytes=2,
        max_total_result_bytes=1,
    )
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)


def planning_request(cfg):
    return PlanningRequest(
        intent="find ports",
        questions=(),
        documents=(),
        assessment=None,
        max_questions=cfg.research.max_questions,
        max_queries=cfg.research.max_queries_per_round,
        max_query_chars=cfg.research.max_query_chars,
    )


def test_fresh_process_exit_after_answer_ack_replays_before_phase_apply(tmp_path):
    """Terminate only our child, after native fsync and before ModelCalls' phase row."""
    cfg = retained_config(tmp_path)
    source = r"""
import asyncio, os, sys, time
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.budget import RunBudget
from ghimera.journal import DirectoryLedgerSink
from ghimera.ledger import Ledger
from ghimera.loop import CollectionSession
from ghimera.models import Goal, ModelIdentity
from ghimera.research import ModelCalls
from ghimera.research_types import PlanningRequest, ResearchPlan, Question
cfg = GhimeraConfig.model_validate_json(sys.argv[1])
judge = ModelIdentity(model_id="offline-test-judge", revision="1", location="test_double")
model = ModelIdentity(model_id="planner", revision="1", location="test_double")
class CutAfterAck(DirectoryLedgerSink):
    def append(self, row):
        super().append(row)
        if row.event == "model_ack":
            os._exit(89)
goal = Goal(text="find ports")
ledger = Ledger(sink=CutAfterAck(cfg, "interrupted", goal, judge))
budget = RunBudget(cfg, time.monotonic)
session = CollectionSession(goal, budget, ledger, None)
request = PlanningRequest(intent=goal.text, questions=(), documents=(), assessment=None,
    max_questions=cfg.research.max_questions, max_queries=cfg.research.max_queries_per_round,
    max_query_chars=cfg.research.max_query_chars)
async def call(request):
    fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, b"one controlled contact\n")
        os.fsync(fd)
    finally:
        os.close(fd)
    return ResearchPlan(questions=(Question(id="q1", text="Which port?"),), queries=())
asyncio.run(ModelCalls(session, cfg.research).invoke("plan", model, request, call))
"""
    witness = tmp_path / "contacts"
    env = dict(os.environ, PYTHONPATH=str(Path.cwd() / "src"))
    child = subprocess.run(
        [sys.executable, "-c", source, cfg.model_dump_json(), str(witness)],
        env=env,
        capture_output=True,
        timeout=20,
    )
    assert child.returncode == 89, child.stderr.decode()
    report = read_journal(cfg.journal, "interrupted")
    assert [row.event for row in report.rows] == ["model_intent", "model_ack"]
    assert not report.incomplete_tail and report.uncertain_model_calls == ()
    restored, ledger = reopen(cfg, report)
    owner = ModelCalls(CollectionSession(report.header.goal, restored, ledger, None), cfg.research)
    observed = owner.replay("plan", PlannerFixture.model, planning_request(cfg), intent_sequence=0)
    assert observed.questions[0].text == "Which port?"
    assert restored.judge_calls == 1 and witness.read_bytes() == b"one controlled contact\n"
    ledger.close()
    final = read_journal(cfg.journal, "interrupted")
    assert [row.event for row in final.rows] == ["model_intent", "model_ack", "model_replay"]


def test_borrowed_rows_cannot_be_replayed_into_another_native_run(tmp_path):
    cfg = retained_config(tmp_path)
    budget, ledger = opened(cfg)

    async def call():
        return "answer"

    asyncio.run(invocation(budget, ledger).invoke(call, str.encode))
    rows = ledger.snapshot()
    ledger.close()
    restored = RunBudget(cfg, time.monotonic)
    restored.restore(receipt(cfg, 1), rows, 0, 0)
    sink = DirectoryLedgerSink(cfg, "other", Goal(text="find ports"), FakeJudge().model)
    borrowed = Ledger(sink=sink, restored_rows=rows)
    with pytest.raises(FatalModelWorkFailure, match="committed prefix"):
        ModelInvocation(
            restored,
            borrowed,
            phase="plan",
            model=PlannerFixture.model,
            request=b"request",
            replay_intent_sequence=0,
        )
    borrowed.close()
    assert read_journal(cfg.journal, "other").rows == ()


def test_decode_failure_does_not_contact_or_acknowledge_a_replayed_answer(tmp_path):
    cfg = retained_config(tmp_path)
    budget, ledger = opened(cfg)

    async def call():
        return "not a plan"

    asyncio.run(invocation(budget, ledger).invoke(call, str.encode))
    before = ledger.snapshot()
    owner = ModelCalls(
        CollectionSession(Goal(text="find ports"), budget, ledger, None), cfg.research
    )
    # This original request has a different shape, so refusal is before decode.
    with pytest.raises(FatalModelWorkFailure):
        owner.replay("plan", PlannerFixture.model, planning_request(cfg), intent_sequence=0)
    assert ledger.snapshot() == before and budget.judge_calls == 1
    attempt = ModelInvocation(
        budget,
        ledger,
        phase="plan",
        model=PlannerFixture.model,
        request=b"request",
        replay_intent_sequence=0,
    )

    def fail(stored):
        raise ValueError("decoder refused")

    with pytest.raises(ValueError, match="decoder refused"):
        attempt.replay(fail)
    assert ledger.snapshot() == before and budget.judge_calls == 1
    ledger.close()


def test_replay_cannot_invoke_the_service_callback(tmp_path):
    cfg = retained_config(tmp_path)
    budget, ledger = opened(cfg)
    contacts = []

    async def call():
        contacts.append(True)
        return "answer"

    asyncio.run(invocation(budget, ledger).invoke(call, str.encode))
    attempt = ModelInvocation(
        budget,
        ledger,
        phase="plan",
        model=PlannerFixture.model,
        request=b"request",
        replay_intent_sequence=0,
    )
    with pytest.raises(FatalModelWorkFailure):
        asyncio.run(attempt.invoke(call, str.encode))
    assert contacts == [True] and budget.judge_calls == 1
    assert attempt.replay(lambda stored: stored.body()) == b"answer"
    ledger.close()


def test_semantic_restore_counts_original_intents_not_later_consumer_rows(tmp_path):
    from tests.test_semantic_verification import reviewed_config

    raw = reviewed_config(tmp_path).model_dump(by_alias=True)
    storage = retained_config(tmp_path)
    raw.update(journal=storage.journal, model_work=storage.model_work)
    cfg = GhimeraConfig.model_validate(raw)
    budget, ledger = opened(cfg)

    async def call():
        return "semantic response"

    extract = invocation(budget, ledger, phase="semantic_extract", reserve=budget.reserve_semantic)
    asyncio.run(extract.invoke(call, str.encode))
    review = invocation(
        budget, ledger, phase="semantic_review", reserve=budget.reserve_semantic_review
    )
    asyncio.run(review.invoke(call, str.encode))
    assert budget.semantic_calls == budget.semantic_review_calls == 1
    ledger.close()
    report = read_journal(cfg.journal, "operation")
    restored, replayed = reopen(cfg, report)
    assert restored.semantic_calls == restored.semantic_review_calls == 1
    attempt = ModelInvocation(
        restored,
        replayed,
        phase="semantic_extract",
        model=PlannerFixture.model,
        request=b"request",
        replay_intent_sequence=0,
    )
    assert attempt.replay(lambda stored: stored.body()) == b"semantic response"
    replayed.close()
    final_budget = RunBudget(cfg, time.monotonic)
    final_budget.restore(receipt(cfg, 2), read_journal(cfg.journal, "operation").rows, 0, 0)
    assert final_budget.semantic_calls == final_budget.semantic_review_calls == 1


def test_storage_failure_after_return_keeps_intent_and_blocks_replay(tmp_path):
    cfg = retained_config(tmp_path)
    budget, ledger = opened(cfg)
    attempt = invocation(budget, ledger)
    sink = ledger._sink
    original_append = sink.append

    def fail_ack(row):
        if row.event == "model_ack":
            raise OSError("controlled write failure")
        original_append(row)

    sink.append = fail_ack

    async def call():
        return "answer"

    with pytest.raises(FatalModelWorkFailure, match="durably acknowledged"):
        asyncio.run(attempt.invoke(call, str.encode))
    with pytest.raises(FatalModelWorkFailure):
        ModelInvocation(
            budget,
            ledger,
            phase="plan",
            model=PlannerFixture.model,
            request=b"request",
            replay_intent_sequence=0,
        )
    ledger.close()
    report = read_journal(cfg.journal, "operation")
    assert report.uncertain_model_calls == (0,)
    assert [r.event for r in report.rows] == ["model_intent"]
