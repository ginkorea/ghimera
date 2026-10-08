"""Native invocation crash/ack evidence, not served-model quality or full recovery."""

import asyncio
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
from ghimera.graph import MemoryGraphSink, ResearchGraph
from ghimera.journal import DirectoryLedgerSink, read_journal
from ghimera.ledger import Ledger
from ghimera.loop import CollectionSession
from ghimera.model_client import SelfHostedModel
from ghimera.model_http import ModelHttpResponse
from ghimera.model_work import (
    FatalModelWorkFailure,
    ModelInvocation,
    port_input,
    uncertain_model_sequences,
    wire_output,
)
from ghimera.models import Goal, LedgerRow
from ghimera.page_renderer import PdfPageRenderer
from ghimera.page_transcriber import LocalPageTranscriber
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research import ModelCalls
from ghimera.research_types import PlanningRequest, ResearchRequest
from ghimera.semantic_graph import SemanticStage
from tests.test_c0 import config
from tests.test_document_extraction import native_pdf
from tests.test_intent_research import PlannerFixture, policy
from tests.test_page_transcription import ModelPort
from tests.test_page_transcription import policy as page_policy
from tests.test_research_continuation import assemble, suspend
from tests.test_semantic_graph import SemanticWire, document
from tests.test_semantic_verification import ReviewWire, reviewed_config


def configured(tmp_path, **updates):
    return config(
        journal=dict(
            schema="chimera.run-journal-config/1",
            directory=str(tmp_path / "journal"),
            max_record_bytes=2000000,
            max_journal_bytes=10000000,
            max_summary_bytes=2000000,
            max_records=2000,
        ),
        model_work=dict(
            schema="ghimera.model-work/1",
            max_input_bytes=4000000,
            max_unanswered_calls=4,
            uncertain_policy="hold",
        ),
        research=policy(),
        judge_budget=40,
        **updates,
    )


def opened(cfg, run_id="operation"):
    goal = Goal(text="find ports")
    ledger = Ledger(sink=DirectoryLedgerSink(cfg, run_id, goal, FakeJudge().model))
    return RunBudget(cfg, time.monotonic), ledger


def invocation(budget, ledger, **updates):
    values = dict(phase="plan", model=PlannerFixture.model, request=b"request")
    values.update(updates)
    return ModelInvocation(budget, ledger, **values)


def test_policy_requires_durability_and_does_not_change_old_recipe_identity(tmp_path):
    cfg = config()
    assert "model_work" not in cfg.model_dump()
    assert "model_intent" not in LedgerRow(sequence=0, event="policy", reason="old").model_dump()
    with pytest.raises(ValidationError, match="durable journal"):
        config(model_work=configured(tmp_path).model_work)
    raw = configured(tmp_path).model_dump(by_alias=True)
    raw["model_work"]["uncertain_policy"] = "retry"
    with pytest.raises(ValidationError):
        type(cfg).model_validate(raw)
    no_sink = Ledger()
    with pytest.raises(FatalModelWorkFailure):
        invocation(RunBudget(configured(tmp_path), time.monotonic), no_sink)


def test_wrong_journal_binding_and_non_durable_sink_refuse_before_reservation(tmp_path):
    cfg = configured(tmp_path)
    budget, ledger = opened(cfg)
    changed = GhimeraConfig.model_validate(dict(cfg.model_dump(), judge_budget=41))
    another_budget = RunBudget(changed, time.monotonic)
    with pytest.raises(FatalModelWorkFailure):
        invocation(another_budget, ledger)
    assert another_budget.judge_calls == 0 and ledger.snapshot() == ()
    ledger.close()

    class MemorySink:
        def append(self, row):
            pass

        def close(self):
            pass

        def finish(self, harvest):
            pass

    with pytest.raises(FatalModelWorkFailure):
        invocation(budget, Ledger(sink=MemorySink()))
    assert budget.judge_calls == 0


def test_a_port_return_without_http_status_is_not_a_remote_response(tmp_path):
    cfg = configured(tmp_path)
    budget, ledger = opened(cfg)
    attempt = invocation(budget, ledger, scope="wire_request")

    async def call():
        return ModelHttpResponse(None, b"", "")

    with pytest.raises(GhimeraRefused):
        asyncio.run(attempt.invoke(call, wire_output))
    ledger.close()
    report = read_journal(cfg.journal, "operation")
    assert report.uncertain_model_calls == (0,)
    assert report.rows[-1].model_ack.output_sha256 is None


def test_intent_is_native_and_durable_before_contact_then_linked_once(tmp_path):
    cfg = configured(tmp_path)
    budget, ledger = opened(cfg)

    async def call():
        report = read_journal(cfg.journal, "operation")
        assert report.uncertain_model_calls == (0,)
        assert report.rows[0].model_intent.judge_reservation == budget.judge_calls == 1
        return "response"

    attempt = invocation(budget, ledger)
    assert asyncio.run(attempt.invoke(call, str.encode)) == "response"
    ledger.close()
    report = read_journal(cfg.journal, "operation")
    assert report.uncertain_model_calls == ()
    intent, ack = report.rows
    assert intent.model_intent.input_sha256 == hashlib.sha256(b"request").hexdigest()
    assert ack.model_ack.intent_sequence == intent.sequence
    assert ack.model_ack.output_sha256 == hashlib.sha256(b"response").hexdigest()
    with pytest.raises(FatalModelWorkFailure, match="reused"):
        asyncio.run(attempt.invoke(call, str.encode))


@pytest.mark.parametrize("failure", ["refused", "cancelled", "raised"])
def test_ended_without_answer_retains_unknown_spend_and_never_fabricates_usage(tmp_path, failure):
    cfg = configured(tmp_path)
    budget, ledger = opened(cfg)

    async def call():
        if failure == "refused":
            raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE)
        if failure == "cancelled":
            raise asyncio.CancelledError
        raise RuntimeError("port crashed")

    with pytest.raises((GhimeraRefused, asyncio.CancelledError, RuntimeError)):
        asyncio.run(invocation(budget, ledger).invoke(call, str.encode))
    ledger.close()
    report = read_journal(cfg.journal, "operation")
    assert report.uncertain_model_calls == (0,)
    ack = report.rows[-1].model_ack
    assert ack.outcome == {"raised": "failed"}.get(failure, failure)
    assert ack.output_sha256 is ack.output_bytes is ack.output_scope is None
    assert report.rows[-1].model_call is None


def test_unknown_capacity_refuses_before_reserving_or_contact_and_input_bound_is_explicit(tmp_path):
    cfg = configured(tmp_path)
    budget, ledger = opened(cfg)
    for _ in range(cfg.model_work.max_unanswered_calls):
        invocation(budget, ledger)
    before = budget.judge_calls
    with pytest.raises(GhimeraRefused) as refused:
        invocation(budget, ledger)
    assert refused.value.code == RefusalCode.BUDGET_EXHAUSTED
    assert budget.judge_calls == before
    ledger.close()
    budget, ledger = opened(cfg, "oversized")
    with pytest.raises(GhimeraRefused):
        invocation(budget, ledger, request=b"x" * (cfg.model_work.max_input_bytes + 1))
    assert budget.judge_calls == 0 and ledger.snapshot() == ()
    ledger.close()


def test_storage_refuses_contact_and_lost_result_ack_leaves_original_intent(tmp_path):
    cfg = configured(tmp_path)
    budget, ledger = opened(cfg)
    sink = ledger._sink
    original = sink.append

    def fail(row):
        raise GhimeraRefused(RefusalCode.LEDGER_SINK_FAILED)

    sink.append = fail
    with pytest.raises(FatalModelWorkFailure, match="intent"):
        invocation(budget, ledger)
    assert read_journal(cfg.journal, "operation").rows == ()
    sink.append = original
    ledger.close()
    budget, ledger = opened(cfg, "lost-ack")
    attempt = invocation(budget, ledger)
    sink = ledger._sink
    sink.append = fail
    contacts = []

    async def call():
        contacts.append("contact")
        return "actual result"

    with pytest.raises(FatalModelWorkFailure, match="result"):
        asyncio.run(attempt.invoke(call, str.encode))
    ledger.close()
    assert contacts == ["contact"]
    assert read_journal(cfg.journal, "lost-ack").uncertain_model_calls == (0,)


@pytest.mark.parametrize("change", ["model", "duplicate_ack", "reservation", "orphan"])
def test_ack_chain_rejects_wrong_model_duplicate_ack_and_reservation(tmp_path, change):
    cfg = configured(tmp_path)
    budget, ledger = opened(cfg)

    async def call():
        return "observed"

    asyncio.run(invocation(budget, ledger).invoke(call, str.encode))
    rows = ledger.snapshot()
    ledger.close()
    intent, ack = rows
    if change == "model":
        rows = (intent, ack.model_copy(update={"model": FakeJudge().model}))
    elif change == "duplicate_ack":
        rows += (ack.model_copy(update={"sequence": 2}),)
    elif change == "reservation":
        rows += (intent.model_copy(update={"sequence": 2}),)
    else:
        rows = (ack,)
    with pytest.raises(ValueError):
        uncertain_model_sequences(rows)


def test_research_crash_retains_intent_in_fresh_native_reader(tmp_path):
    cfg = configured(tmp_path)
    recipe = tmp_path / "recipe.json"
    recipe.write_text(cfg.model_dump_json())
    marker = tmp_path / "contact.txt"
    script = """
import asyncio, os, sys, time
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.budget import RunBudget
from ghimera.journal import DirectoryLedgerSink
from ghimera.ledger import Ledger
from ghimera.loop import CollectionSession
from ghimera.models import Goal, ModelIdentity
from ghimera.research import ModelCalls
from ghimera.research_types import PlanningRequest
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
model = ModelIdentity(model_id="controlled-planner", revision="1", location="test_double")
goal = Goal(text="find ports")
ledger = Ledger(sink=DirectoryLedgerSink(cfg, "crash", goal, model))
session = CollectionSession(goal, RunBudget(cfg, time.monotonic), ledger, None)
async def crash(request):
    fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, b"port-invoked")
    os.fsync(fd)
    os.close(fd)
    os._exit(89)
request = PlanningRequest(intent=goal.text, questions=(), documents=(), assessment=None,
    max_questions=cfg.research.max_questions, max_queries=cfg.research.max_queries_per_round,
    max_query_chars=cfg.research.max_query_chars)
asyncio.run(ModelCalls(session, cfg.research).invoke("plan", model, request, crash))
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(recipe), str(marker)],
        env=os.environ.copy(),
        capture_output=True,
        timeout=30,
    )
    assert child.returncode == 89, child.stderr.decode()
    assert marker.read_bytes() == b"port-invoked"
    report = read_journal(cfg.journal, "crash")
    assert report.state == "unsealed" and not report.incomplete_tail
    assert [row.event for row in report.rows] == ["model_intent"]
    assert report.uncertain_model_calls == (0,)
    assert report.rows[0].model_intent.judge_reservation == 1


def test_completed_research_journal_counts_intents_once(tmp_path):
    cfg = configured(tmp_path)
    loop, _, _, _ = assemble(cfg)
    result = asyncio.run(loop.run(ResearchRequest(intent="find ports"), run_id="completed"))
    report = read_journal(cfg.journal, "completed")
    assert report.state == "complete" and report.uncertain_model_calls == ()
    assert len([row for row in report.rows if row.event == "model_intent"]) == (
        result.harvest.receipt.judge_calls
    )
    assert {row.model_intent.phase for row in report.rows if row.model_intent} >= {
        "plan",
        "assessment",
        "answer",
        "review",
        "verdict",
    }


def test_completed_round_restore_preserves_reservations_and_unknown_restore_is_held(tmp_path):
    cfg = configured(
        tmp_path,
        continuation=dict(
            schema="ghimera.continuation/1",
            max_checkpoint_bytes=4000000,
            clock_policy="include_downtime",
        ),
    )
    first, _, _, _ = assemble(cfg)
    receipt = suspend(first, "resumed")
    before = read_journal(cfg.journal, "resumed").rows
    second, route, search, planner = assemble(cfg)
    result = asyncio.run(second.resume("resumed", checkpoint_sha256=receipt.sha256))
    after = read_journal(cfg.journal, "resumed")
    assert after.state == "complete" and after.rows[: len(before)] == before
    assert not route.requests and not search.requests and not planner.requests
    assert (
        len([row for row in after.rows if row.model_intent]) == result.harvest.receipt.judge_calls
    )
    budget, ledger = opened(cfg, "uncertain")
    invocation(budget, ledger)
    ledger.close()
    unknown = read_journal(cfg.journal, "uncertain")
    restored = RunBudget(cfg, time.monotonic)
    with pytest.raises(ValueError, match="reconciliation"):
        restored.restore(result.harvest.receipt, unknown.rows, 0, 0)
    assert restored.judge_calls == 0


def test_concurrent_calls_keep_distinct_reservations_and_ack_original_intent(tmp_path):
    cfg = configured(tmp_path)
    budget, ledger = opened(cfg)

    async def run():
        ready = asyncio.Event()
        phases = ("plan", "assessment", "answer", "review")

        async def one(phase):
            attempt = invocation(budget, ledger, phase=phase, request=phase.encode())

            async def call():
                await ready.wait()
                return phase

            return await attempt.invoke(call, str.encode)

        tasks = [asyncio.create_task(one(phase)) for phase in phases]
        await asyncio.sleep(0)
        assert read_journal(cfg.journal, "operation").uncertain_model_calls == (0, 1, 2, 3)
        ready.set()
        assert tuple(await asyncio.gather(*tasks)) == phases

    asyncio.run(run())
    ledger.close()
    report = read_journal(cfg.journal, "operation")
    assert report.uncertain_model_calls == ()
    assert [row.model_ack.intent_sequence for row in report.rows if row.model_ack] == [0, 1, 2, 3]


def test_port_hash_is_logical_input_and_disabled_policy_does_not_serialize_documents(tmp_path):
    cfg = configured(tmp_path)
    budget = RunBudget(cfg, time.monotonic)
    goal = Goal(text="native question")
    assert port_input(budget, goal, second_look=False) != port_input(budget, goal, second_look=True)
    assert (
        port_input(RunBudget(config(), time.monotonic), goal, unsupported=Path("never encoded"))
        == b""
    )


def test_research_success_uses_existing_port_and_quota_boundary(tmp_path):
    cfg = configured(tmp_path)
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
    planner = PlannerFixture()
    result = asyncio.run(
        ModelCalls(session, cfg.research).invoke(
            "plan",
            planner.model,
            request,
            planner.plan,
        )
    )
    assert result.questions and budget.judge_calls == 1
    assert [row.event for row in ledger.snapshot()] == ["model_intent", "model_ack", "plan"]
    session.close()


@pytest.mark.parametrize("defect", [None, "truncated"])
def test_semantic_ports_retain_native_intents_and_distinguish_known_refused_completion(
    tmp_path, defect
):
    raw = reviewed_config(tmp_path).model_dump(by_alias=True)
    storage = configured(tmp_path)
    raw.update(journal=storage.journal, model_work=storage.model_work)
    cfg = GhimeraConfig.model_validate(raw)
    budget, ledger = opened(cfg)
    graph = ResearchGraph(cfg.graph, "semantic-calls", MemoryGraphSink())
    first, second = SemanticWire(cfg.models.analyst), ReviewWire(cfg.models.reviewer, defect=defect)
    extractor = SelfHostedModel(cfg, cfg.models.analyst, http=first)
    reviewer = SelfHostedModel(cfg, cfg.models.reviewer, http=second)
    stage = SemanticStage(cfg, extractor, reviewer=reviewer)
    doc = document()

    async def run():
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
        await stage.extract("map the organization", doc, identity, graph, budget, ledger)

    if defect:
        with pytest.raises(GhimeraRefused):
            asyncio.run(run())
    else:
        asyncio.run(run())
    ledger.close()
    report = read_journal(cfg.journal, "operation")
    assert [row.model_intent.phase for row in report.rows if row.model_intent] == [
        "semantic_extract",
        "semantic_review",
    ]
    assert budget.judge_calls == 2 and report.uncertain_model_calls == ()
    if defect:
        ack = [row.model_ack for row in report.rows if row.model_ack][-1]
        assert ack.outcome == "refused" and ack.refused_call.completion.finish_reason == "length"
        assert ack.output_scope == "wire_response"
    else:
        assert report.rows[-1].semantic_window is not None


def test_pdf_ports_record_exact_wire_inputs_without_changing_pixels_or_review(tmp_path):
    cfg = configured(tmp_path)
    pages = page_policy(tmp_path)
    page = asyncio.run(PdfPageRenderer(pages.renderer).render(native_pdf())).pages[0]
    first, second = ModelPort(pages.transcriber), ModelPort(pages.reviewer, review=True)
    budget, ledger = opened(cfg)
    result = asyncio.run(
        LocalPageTranscriber(
            pages,
            transcription_http=first,
            review_http=second,
        ).transcribe(
            page,
            language_hint="zh-Hant",
            source_url="https://example.org/x.pdf",
            budget=budget,
            ledger=ledger,
        )
    )
    ledger.close()
    report = read_journal(cfg.journal, "operation")
    intents = [row.model_intent for row in report.rows if row.model_intent]
    assert len(intents) == budget.judge_calls == 2
    assert all(
        item.phase == "transcription_model" and item.input_scope == "wire_request"
        for item in intents
    )
    assert [item.input_sha256 for item in intents] == [item.request_sha256 for item in result.calls]
    assert result.accepted and result.text == "臺灣港務公司"
    assert report.uncertain_model_calls == ()
