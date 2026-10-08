"""Native private storage and model-tail admission, never service acceptance."""

import asyncio
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.journal import read_journal
from ghimera.model_work import ModelInvocation, port_input, record_output
from ghimera.models import Goal, LedgerRow, Scope
from ghimera.refusals import GhimeraRefused
from ghimera.research import ModelCalls
from ghimera.research_recovery_store import ResearchRecoveryStore
from ghimera.research_recovery_types import ResearchControlSnapshot, ResearchPendingModel
from ghimera.research_types import (
    AnswerRequest,
    EvidenceRequest,
    PlanningRequest,
    ResearchRequest,
    ResearchResult,
    ResearchRound,
    ReviewRequest,
)
from tests.test_intent_research import (
    AnalystFixture,
    PlannerFixture,
    ReviewerFixture,
    SearchFixture,
)
from tests.test_model_work import configured
from tests.test_run_journal import collector


@dataclass(frozen=True)
class StoragePolicy:
    max_snapshot_bytes: int = 4_000_000


@pytest.fixture
def opened(tmp_path):
    cfg = GhimeraConfig.model_validate(
        dict(
            configured(tmp_path).model_dump(),
            model_work=dict(
                schema="ghimera.model-work/1",
                max_input_bytes=4_000_000,
                max_unanswered_calls=4,
                uncertain_policy="hold",
                results=dict(
                    schema="ghimera.model-results/1",
                    max_result_bytes=1_000_000,
                    max_total_result_bytes=6_000_000,
                ),
            ),
        )
    )
    loop = collector(cfg)
    session = asyncio.run(loop.open(Goal(text="find ports"), run_id="control"))
    request = ResearchRequest(intent="find ports")
    planning = PlanningRequest(
        intent=request.intent,
        questions=(),
        documents=(),
        assessment=None,
        max_questions=cfg.research.max_questions,
        max_queries=cfg.research.max_queries_per_round,
        max_query_chars=cfg.research.max_query_chars,
    )
    raw = port_input(session.budget, planning)
    progress = ResearchResult(
        schema="chimera.research-result/1",
        status="partial",
        stop_reason="rounds_exhausted",
        harvest=loop.snapshot(session),
        questions=(),
        rounds=(),
        unresolved=(),
        answer=None,
        review=None,
        planner=PlannerFixture.model,
        analyst=AnalystFixture.model,
        reviewer=ReviewerFixture.model,
        search_provider=SearchFixture.name,
        search_revision=SearchFixture.revision,
        search_calls=0,
    )
    snapshot = ResearchControlSnapshot(
        schema="ghimera.research-control-snapshot/1",
        run_id="control",
        saved_at=1.0,
        max_snapshot_bytes=StoragePolicy().max_snapshot_bytes,
        request=request,
        progress=progress,
        session=session.checkpoint_state(),
        admitted_hosts=(),
        phase="plan",
        round_number=1,
        model_request=planning,
        pending_model=ResearchPendingModel(
            phase="plan",
            model=PlannerFixture.model,
            input_sha256=hashlib.sha256(raw).hexdigest(),
            input_bytes=len(raw),
        ),
    )
    store = ResearchRecoveryStore(cfg, "control", StoragePolicy())
    try:
        yield cfg, loop, session, snapshot, store
    finally:
        session.close()


def returned(session, snapshot, *, phase_event):
    async def call():
        if phase_event:
            return await ModelCalls(session, session.budget.config.research).invoke(
                "plan", PlannerFixture.model, snapshot.model_request, PlannerFixture().plan
            )
        invocation = ModelInvocation(
            session.budget,
            session.ledger,
            phase="plan",
            model=PlannerFixture.model,
            request=port_input(session.budget, snapshot.model_request),
        )
        return await invocation.invoke(
            lambda: PlannerFixture().plan(snapshot.model_request), record_output
        )

    return asyncio.run(call())


def test_initial_no_round_snapshot_roundtrips_without_sealing_or_calling(opened):
    cfg, _, _, snapshot, store = opened
    pin = store.write(snapshot)
    restored = store.read(
        pin.sha256, expected_request=snapshot.request, expected_models=snapshot.models
    )
    assert restored.snapshot == snapshot
    assert restored.intent_sequence is None
    assert restored.journal.rows == () and restored.journal.state == "unsealed"
    assert pin.ledger_rows == 0 and not snapshot.progress.rounds
    target = cfg.journal.directory / "control" / "research-control.json"
    assert target.stat().st_mode & 0o777 == 0o600
    before = target.read_bytes()
    assert store.read(pin.sha256) == restored
    assert target.read_bytes() == before


@pytest.mark.parametrize("phase_event", [False, True])
def test_exact_original_retained_model_return_is_admitted_without_fabricating_rows(
    opened, phase_event
):
    cfg, _, session, snapshot, store = opened
    pin = store.write(snapshot)
    returned(session, snapshot, phase_event=phase_event)
    before = read_journal(cfg.journal, "control")
    restored = store.read(pin.sha256)
    assert restored.intent_sequence == 0
    assert restored.snapshot == snapshot and restored.journal == before
    assert len(before.rows) == (3 if phase_event else 2)
    assert session.budget.judge_calls == 1
    assert read_journal(cfg.journal, "control") == before


def test_unknown_original_model_call_holds_without_automatic_retry(opened):
    cfg, _, session, snapshot, store = opened
    pin = store.write(snapshot)
    ModelInvocation(
        session.budget,
        session.ledger,
        phase="plan",
        model=PlannerFixture.model,
        request=port_input(session.budget, snapshot.model_request),
    )
    before = read_journal(cfg.journal, "control")
    with pytest.raises(ValueError, match="unknown model outcome"):
        store.read(pin.sha256)
    assert read_journal(cfg.journal, "control") == before
    assert before.uncertain_model_calls == (0,)


@pytest.mark.parametrize("change", ["request", "models", "recipe", "digest", "torn", "stop"])
def test_changed_binding_or_unreconciled_journal_refuses(opened, change):
    cfg, _, session, snapshot, store = opened
    pin = store.write(snapshot)
    options = {}
    if change == "request":
        options["expected_request"] = ResearchRequest(intent="another intent")
    elif change == "models":
        options["expected_models"] = snapshot.models.model_copy(
            update={"search_revision": "another-revision"}
        )
    elif change == "recipe":
        altered = GhimeraConfig.model_validate(dict(cfg.model_dump(), judge_budget=41))
        store = ResearchRecoveryStore(altered, "control", StoragePolicy())
    elif change == "digest":
        pin = pin.model_copy(update={"sha256": "0" * 64})
    elif change == "torn":
        with (cfg.journal.directory / "control" / "ledger.jsonl").open("ab") as stream:
            stream.write(b'{"unfinished":')
    else:
        session.ledger.append(LedgerRow(sequence=0, event="stop", reason="frontier_empty"))
    with pytest.raises((ValueError, GhimeraRefused)):
        store.read(pin.sha256, **options)


@pytest.mark.parametrize("change", ["phase", "input", "model", "event", "extra", "bytes"])
def test_adoption_requires_exact_input_model_phase_and_only_model_effects(opened, change):
    _, _, session, snapshot, store = opened
    pin = store.write(snapshot)
    if change in {"phase", "input", "model"}:
        model = AnalystFixture.model if change == "model" else PlannerFixture.model
        request = (
            b"changed" if change == "input" else port_input(session.budget, snapshot.model_request)
        )
        invocation = ModelInvocation(
            session.budget,
            session.ledger,
            phase="assessment" if change == "phase" else "plan",
            model=model,
            request=request,
        )
        asyncio.run(
            invocation.invoke(lambda: PlannerFixture().plan(snapshot.model_request), record_output)
        )
    else:
        returned(session, snapshot, phase_event=False)
        if change == "event":
            session.ledger.append(
                LedgerRow(sequence=2, event="plan", model=PlannerFixture.model, reason="changed")
            )
        elif change == "bytes":
            session.ledger.append(
                LedgerRow(sequence=2, event="fetch", bytes_read=1, reason="later source read")
            )
        else:
            session.ledger.append(LedgerRow(sequence=2, event="refusal", reason="later work"))
            session.ledger.append(LedgerRow(sequence=3, event="refusal", reason="more work"))
    before = session.ledger.snapshot()
    with pytest.raises(ValueError):
        store.read(pin.sha256)
    assert session.ledger.snapshot() == before


def test_write_refuses_omitted_native_work_and_wrong_input_binding(opened):
    _, _, session, snapshot, store = opened
    changed = snapshot.model_copy(
        update={"pending_model": snapshot.pending_model.model_copy(update={"input_bytes": 1})}
    )
    with pytest.raises(ValueError, match="input binding"):
        store.write(changed)
    session.ledger.append(LedgerRow(sequence=0, event="policy", reason="later work"))
    with pytest.raises(ValueError, match="cannot omit work"):
        store.write(snapshot)


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "public"])
def test_existing_unsafe_snapshot_is_not_replaced_or_read(opened, tmp_path, unsafe):
    cfg, _, _, snapshot, store = opened
    pin = store.write(snapshot)
    target = cfg.journal.directory / "control" / "research-control.json"
    original = target.read_bytes()
    if unsafe == "public":
        target.chmod(0o644)
    else:
        target.unlink()
        other = tmp_path / "other"
        other.write_bytes(original)
        other.chmod(0o600)
        if unsafe == "symlink":
            target.symlink_to(other)
        else:
            os.link(other, target)
    with pytest.raises((GhimeraRefused, OSError)):
        store.write(snapshot)
    with pytest.raises((GhimeraRefused, OSError)):
        store.read(pin.sha256)
    assert target.read_bytes() == original
    assert not tuple(target.parent.glob(".research-control-*"))


def test_atomic_failure_keeps_previous_snapshot_and_cleans_staging(opened, monkeypatch):
    cfg, _, _, snapshot, store = opened
    pin = store.write(snapshot)
    target = cfg.journal.directory / "control" / "research-control.json"
    before = target.read_bytes()

    def fail_replace(source, destination):
        raise OSError("deliberate replacement failure")

    monkeypatch.setattr("ghimera.research_recovery_store.os.replace", fail_replace)
    with pytest.raises(OSError, match="deliberate"):
        store.write(snapshot.model_copy(update={"saved_at": 2.0}))
    assert target.read_bytes() == before
    assert store.read(pin.sha256).snapshot == snapshot
    assert not tuple(target.parent.glob(".research-control-*"))


def test_snapshot_file_is_fsynced_before_atomic_replace_and_directory_after(opened, monkeypatch):
    _, _, _, snapshot, store = opened
    order = []
    native_fsync, native_replace = os.fsync, os.replace

    def observe_fsync(fd):
        order.append("fsync")
        native_fsync(fd)

    def observe_replace(source, destination):
        order.append("replace")
        native_replace(source, destination)

    monkeypatch.setattr("ghimera.journal.os.fsync", observe_fsync)
    monkeypatch.setattr("ghimera.research_recovery_store.os.replace", observe_replace)
    store.write(snapshot)
    assert order == ["fsync", "replace", "fsync"]


def test_operator_byte_bound_is_enforced_before_mutating_snapshot(opened):
    cfg, _, _, snapshot, store = opened
    pin = store.write(snapshot)
    small = ResearchRecoveryStore(cfg, "control", StoragePolicy(max_snapshot_bytes=1))
    with pytest.raises(ValueError, match="original journal prefix"):
        small.write(snapshot)
    with pytest.raises(ValueError, match="byte allowance"):
        small.write(snapshot.model_copy(update={"max_snapshot_bytes": 1}))
    assert store.read(pin.sha256).snapshot == snapshot
    with pytest.raises(GhimeraRefused):
        small.read(pin.sha256)
    larger = ResearchRecoveryStore(cfg, "control", StoragePolicy(max_snapshot_bytes=8_000_000))
    with pytest.raises(ValueError, match="original journal prefix"):
        larger.read(pin.sha256)


def test_zero_round_snapshot_is_invalid_if_claiming_a_finished_answer(opened):
    _, _, _, snapshot, _ = opened
    raw = snapshot.model_dump()
    raw["progress"]["status"] = "answered"
    with pytest.raises(ValidationError):
        ResearchControlSnapshot.model_validate(raw)


def test_independent_process_reads_the_original_control_state(opened, tmp_path):
    cfg, _, session, snapshot, store = opened
    pin = store.write(snapshot)
    session.close()
    configuration = tmp_path / "configuration.json"
    configuration.write_text(cfg.model_dump_json())
    script = """
import sys
from pathlib import Path
from dataclasses import dataclass
from ghimera.config import GhimeraConfig
from ghimera.research_recovery_store import ResearchRecoveryStore
@dataclass(frozen=True)
class Policy:
    max_snapshot_bytes: int = 4000000
config = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
result = ResearchRecoveryStore(config, 'control', Policy()).read(sys.argv[2])
assert result.snapshot.phase == 'plan' and not result.snapshot.progress.rounds
assert result.journal.state == 'unsealed' and result.intent_sequence is None
print('original-control-state')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(configuration), pin.sha256],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert completed.stdout == "original-control-state\n" and completed.stderr == ""


@pytest.mark.parametrize("phase", ["assessment", "answer", "review"])
def test_phase_specific_control_and_collected_frontier_roundtrip(opened, phase):
    _, loop, session, initial, store = opened
    scope = Scope(allowed_hosts=("example.org",), max_depth=1, content_types=("text/html",))
    stop = asyncio.run(
        loop.collect(session, scope, ("https://example.org/one",), fetch_limit=1, allow_grade=False)
    )
    # The native session owns the pending frontier, visits and content revisions.
    session.queue_source(scope, "https://example.org/pending", 1, -0.5)
    harvest = loop.snapshot(session)
    plan = asyncio.run(PlannerFixture().plan(initial.model_request))
    evidence = EvidenceRequest(
        intent=initial.request.intent,
        questions=plan.questions,
        documents=harvest.source_documents,
    )
    assessment = asyncio.run(AnalystFixture().assess(evidence))
    answer_request = AnswerRequest(**evidence.model_dump(), assessment=assessment)
    answer = asyncio.run(AnalystFixture().answer(answer_request))
    review_request = ReviewRequest(**evidence.model_dump(), answer=answer)
    phase_request = (
        evidence
        if phase == "assessment"
        else answer_request
        if phase == "answer"
        else review_request
    )
    rounds = (
        ()
        if phase == "assessment"
        else (
            ResearchRound(
                number=1,
                queries=plan.queries,
                discovered_urls=("https://example.org/one",),
                assessment=assessment,
                collection_stop=stop,
            ),
        )
    )
    progress = initial.progress.model_copy(
        update={
            "harvest": harvest,
            "questions": plan.questions,
            "rounds": rounds,
        }
    )
    raw = port_input(session.budget, phase_request)
    snapshot = ResearchControlSnapshot.model_validate(
        dict(
            initial.model_dump(),
            progress=progress,
            session=session.checkpoint_state(),
            admitted_hosts=("example.org",),
            phase=phase,
            model_request=phase_request,
            pending_model=dict(
                phase=phase,
                model=ReviewerFixture.model if phase == "review" else AnalystFixture.model,
                input_sha256=hashlib.sha256(raw).hexdigest(),
                input_bytes=len(raw),
            ),
            current_plan=plan,
            assessment=assessment if phase != "assessment" else None,
            current_answer=answer if phase == "review" else None,
            discovered_urls=("https://example.org/one",),
            collection_stop=stop,
            before_documents=(harvest.documents[0].sha256,),
            before_answers=("q1",),
            allow_retained_completion=False,
        )
    )
    pin = store.write(snapshot)
    restored = store.read(pin.sha256).snapshot
    assert restored == snapshot
    assert restored.session.frontier and restored.session.visited
    assert restored.progress.harvest.documents == harvest.documents
    assert restored.current_plan == plan
    assert restored.before_answers == ("q1",) and not restored.allow_retained_completion


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_locally_ended_call_with_unknown_remote_outcome_holds(opened, outcome):
    cfg, _, session, snapshot, store = opened
    pin = store.write(snapshot)
    invocation = ModelInvocation(
        session.budget,
        session.ledger,
        phase="plan",
        model=PlannerFixture.model,
        request=port_input(session.budget, snapshot.model_request),
    )

    async def fail():
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        raise RuntimeError("unknown remote effects")

    with pytest.raises((RuntimeError, asyncio.CancelledError)):
        asyncio.run(invocation.invoke(fail, record_output))
    before = read_journal(cfg.journal, "control")
    assert before.rows[-1].model_ack.outcome == outcome
    with pytest.raises(ValueError, match="unknown model outcome"):
        store.read(pin.sha256)
    assert read_journal(cfg.journal, "control") == before
