"""Actual process death after a native return, not a manual replay demonstration."""

import asyncio
import hashlib
import subprocess
import sys

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.journal import read_journal
from ghimera.research_recovery_types import ResearchControlSnapshot
from ghimera.research_types import ResearchRequest
from tests.test_research_continuation import assemble
from tests.test_research_continuation import configured as round_config


def configured(tmp_path):
    raw = round_config(tmp_path).model_dump()
    raw["model_work"] = dict(
        schema="ghimera.model-work/1",
        max_input_bytes=4000000,
        max_unanswered_calls=4,
        uncertain_policy="hold",
        results=dict(
            schema="ghimera.model-results/1",
            max_result_bytes=16384,
            max_total_result_bytes=131072,
        ),
    )
    raw["research_recovery"] = dict(
        schema="ghimera.research-recovery/1",
        max_snapshot_bytes=4000000,
        clock_policy="include_downtime",
        tail_policy="acknowledged_model_return",
    )
    return GhimeraConfig.model_validate(raw)


def killed_run(tmp_path, cfg, phase, *, unknown=False, boundary="phase"):
    configuration = tmp_path / "configuration.json"
    configuration.write_text(cfg.model_dump_json())
    code = """
import asyncio, os, sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.research import ModelCalls
from ghimera.model_work import ModelInvocation
from ghimera.research_recovery_store import ResearchRecoveryStore
from ghimera.research_types import ResearchRequest
from tests.test_research_continuation import assemble
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
write_snapshot = ResearchRecoveryStore.write
def snapshot_dying(self, snapshot):
    receipt = write_snapshot(self, snapshot)
    if sys.argv[4] == 'snapshot' and snapshot.phase == sys.argv[2]:
        os._exit(73)
    return receipt
ResearchRecoveryStore.write = snapshot_dying
original = ModelCalls.invoke
native = ModelInvocation.invoke
async def port_dying(self, call, observe):
    result = await native(self, call, observe)
    if sys.argv[4] == 'port':
        from ghimera.journal import read_journal
        rows = read_journal(cfg.journal, 'research').rows
        ack = rows[-1].model_ack
        if ack is not None and rows[ack.intent_sequence].model_intent.phase == sys.argv[2]:
            os._exit(73)
    return result
ModelInvocation.invoke = port_dying
async def dying(self, event, model, request, call, **kwargs):
    if event == sys.argv[2] and sys.argv[3] == 'unknown':
        async def lost(request):
            os._exit(74)
        return await original(self, event, model, request, lost, **kwargs)
    result = await original(self, event, model, request, call, **kwargs)
    if event == sys.argv[2] and sys.argv[4] == 'phase':
        os._exit(73)
    return result
ModelCalls.invoke = dying
loop, *_ = assemble(cfg)
asyncio.run(loop.run(ResearchRequest(intent='find ports'),run_id='research'))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(configuration),
            phase,
            "unknown" if unknown else "ack",
            boundary,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == (74 if unknown else 73), result.stderr
    snapshot = cfg.journal.directory / "research" / "research-control.json"
    return hashlib.sha256(snapshot.read_bytes()).hexdigest()


@pytest.mark.parametrize("phase", ["plan", "assessment", "answer", "review"])
@pytest.mark.parametrize("boundary", ["port", "phase"])
def test_fresh_process_recovers_original_phase_without_repeating_completed_io(
    tmp_path, phase, boundary
):
    baseline_loop, baseline_route, baseline_search, _ = assemble(configured(tmp_path / "baseline"))
    baseline = asyncio.run(
        baseline_loop.run(ResearchRequest(intent="find ports"), run_id="research")
    )
    cfg = configured(tmp_path)
    pin = killed_run(tmp_path, cfg, phase, boundary=boundary)
    before = read_journal(cfg.journal, "research")
    original = ResearchControlSnapshot.model_validate_json(
        (cfg.journal.directory / "research" / "research-control.json").read_bytes()
    )
    resumed, route, search, planner = assemble(cfg)
    result = asyncio.run(resumed.recover("research", snapshot_sha256=pin))
    assert result.status == "answered"
    assert result.harvest.ledger[: len(before.rows)] == before.rows
    assert result.harvest.receipt.judge_calls == baseline.harvest.receipt.judge_calls
    assert [row.model_intent.phase for row in result.harvest.ledger if row.model_intent] == [
        row.model_intent.phase for row in baseline.harvest.ledger if row.model_intent
    ]
    assert len([row for row in result.harvest.ledger if row.model_replay]) == 1
    assert len(route.requests) == (len(baseline_route.requests) if phase == "plan" else 0)
    assert len(search.requests) == (len(baseline_search.requests) if phase == "plan" else 0)
    assert not planner.requests
    assert result.harvest.receipt.fetches == baseline.harvest.receipt.fetches
    assert result.search_calls == baseline.search_calls
    if phase != "plan":
        assert result.harvest.graph == original.progress.harvest.graph
    assert read_journal(cfg.journal, "research").state == "complete"


def test_unknown_model_outcome_holds_before_any_new_contact(tmp_path):
    cfg = configured(tmp_path)
    pin = killed_run(tmp_path, cfg, "assessment", unknown=True)
    before = read_journal(cfg.journal, "research").rows
    resumed, route, search, planner = assemble(cfg)
    with pytest.raises(ValueError, match="unknown"):
        asyncio.run(resumed.recover("research", snapshot_sha256=pin))
    assert not route.requests and not search.requests and not planner.requests
    assert read_journal(cfg.journal, "research").rows == before


@pytest.mark.parametrize("phase", ["plan", "assessment", "answer", "review"])
def test_phase_not_started_before_crash_is_invoked_once_from_original_state(tmp_path, phase):
    cfg = configured(tmp_path)
    pin = killed_run(tmp_path, cfg, phase, boundary="snapshot")
    before = read_journal(cfg.journal, "research").rows
    resumed, route, search, planner = assemble(cfg)
    result = asyncio.run(resumed.recover("research", snapshot_sha256=pin))
    assert result.status == "answered"
    assert result.harvest.ledger[: len(before)] == before
    assert not any(row.model_replay for row in result.harvest.ledger)
    assert [row.model_intent.phase for row in result.harvest.ledger if row.model_intent] == [
        "plan",
        "verdict",
        "assessment",
        "answer",
        "review",
    ]
    assert len(route.requests) == len(search.requests) == (1 if phase == "plan" else 0)
    assert len(planner.requests) == (1 if phase == "plan" else 0)


def test_restart_downtime_never_grants_a_fresh_wall_budget(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    pin = killed_run(tmp_path, cfg, "plan", boundary="snapshot")
    saved = ResearchControlSnapshot.model_validate_json(
        (cfg.journal.directory / "research" / "research-control.json").read_bytes()
    )
    monkeypatch.setattr("ghimera.research.time.time", lambda: saved.saved_at + cfg.wall_seconds + 1)
    resumed, route, search, planner = assemble(cfg)
    result = asyncio.run(resumed.recover("research", snapshot_sha256=pin))
    assert result.stop_reason == "budget_exhausted"
    assert not route.requests and not search.requests and not planner.requests
    assert result.harvest.receipt.judge_calls == 0


def test_policy_requires_original_journal_and_retained_model_work(tmp_path):
    cfg = configured(tmp_path)
    for update in (dict(journal=None, continuation=None), dict(model_work=None)):
        with pytest.raises(ValidationError, match="recovery"):
            GhimeraConfig.model_validate(dict(cfg.model_dump(), **update))
    unconfigured, *_ = assemble(round_config(tmp_path))
    with pytest.raises(ValueError, match="explicit"):
        asyncio.run(unconfigured.recover("research", snapshot_sha256="0" * 64))
