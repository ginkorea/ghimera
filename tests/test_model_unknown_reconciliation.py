"""Native private journal/process-death witnesses; local model fixture, not accuracy."""

import asyncio
import hashlib
import json
import subprocess
import sys

import pytest

from ghimera.command import CommandOptions, execute
from ghimera.config import GhimeraConfig
from ghimera.journal import DirectoryLedgerSink, read_journal
from ghimera.model_reconciliation import unreconciled_model_sequences, validate_decisions
from ghimera.model_reconciliation_types import (
    ModelAttemptAuthorization,
    ModelReconciliationDecision,
    ModelUnknownObservation,
)
from ghimera.refusals import GhimeraRefused
from ghimera.result_archive import ResearchResultArchive
from tests.test_collector import encoder_endpoint, model_endpoint, search_endpoint, source_site
from tests.test_collector_command import options, toml_lines
from tests.test_command_recovery import configured as original_configuration
from tests.test_command_recovery import counts
from tests.test_http_fetch import ResolverFixture

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, **updates):
    cfg = original_configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    raw = cfg.model_dump()
    raw["research_recovery"].update(
        schema="ghimera.research-recovery/3",
        model_reconciliation=dict(
            schema="ghimera.model-reconciliation-policy/1",
            max_decisions_per_run=2,
            max_decision_bytes=16384,
        ),
    )
    raw.update(updates)
    return GhimeraConfig.model_validate(raw)


def caller_options(opts, pin, operation, *, decision=None, attempt=None):
    return CommandOptions.model_validate(
        dict(
            opts.model_dump(),
            schema="ghimera.collector-command/5",
            request_path=None,
            execution=dict(
                schema="ghimera.command-execution/4",
                operation=operation,
                snapshot_sha256=pin,
                model_decision=decision,
                model_attempt=attempt,
            ),
        )
    )


ENTRYPOINT = """
import os, sys
from ghimera import command
from ghimera.model_work import ModelInvocation
from tests.test_http_fetch import ResolverFixture
native = ModelInvocation.invoke
async def dying(self, call, observe):
    rows = self._ledger.snapshot()
    intent = rows[self._sequence].model_intent if self._sequence is not None else None
    selected = intent is not None and intent.phase == 'answer'
    if selected and (sys.argv[1] == 'original_unknown' and self._authorization is None
                     or sys.argv[1] == 'attempt_unknown' and self._authorization is not None):
        async def lost():
            await call()  # Fixture accepted actual POST; local native ACK never exists.
            os._exit(74)
        return await native(self, lost, observe)
    if selected and sys.argv[1] == 'local_unknown':
        async def locally_ended():
            await call()
            raise RuntimeError('fixture lost remote outcome')
        return await native(self, locally_ended, observe)
    result = await native(self, call, observe)
    if selected and sys.argv[1] == 'attempt_ack' and self._authorization is not None:
        os._exit(75)
    return result
ModelInvocation.invoke = dying
if sys.argv[1] == 'local_unknown':
    from ghimera.research import ModelCalls
    native_phase = ModelCalls.invoke
    async def ended(self,event,model,request,call,**kwargs):
        try:
            return await native_phase(self,event,model,request,call,**kwargs)
        except RuntimeError:
            os._exit(74)  # Native UNKNOWN ACK and phase end committed, no stop/seal.
    ModelCalls.invoke = ended
execute = command.execute
async def resolved(opts):
    return await execute(opts, source_resolver=ResolverFixture())
command.execute = resolved
raise SystemExit(command.main(sys.argv[2:]))
"""


def invoke(tmp_path, opts, boundary="none"):
    path = tmp_path / (boundary + ".toml")
    path.write_text("\n".join(toml_lines(opts.model_dump(mode="json"))))
    return subprocess.run(
        [
            sys.executable,
            "-c",
            ENTRYPOINT,
            boundary,
            "--job",
            str(path),
            "--max-job-bytes",
            "1000000",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def interrupted(tmp_path, cfg, boundary="original_unknown"):
    opts = options(
        tmp_path,
        cfg,
        schema="ghimera.collector-command/2",
        execution=dict(schema="ghimera.command-execution/1", operation="run"),
    )
    died = invoke(tmp_path, opts, boundary)
    assert died.returncode == 74, died.stderr
    snapshot = cfg.journal.directory / opts.run_id / "research-control.json"
    return opts, hashlib.sha256(snapshot.read_bytes()).hexdigest()


def decision(opts, pin):
    observed = asyncio.run(
        execute(
            caller_options(opts, pin, "observe_model_unknown"), source_resolver=ResolverFixture()
        )
    )
    assert isinstance(observed, ModelUnknownObservation)
    return ModelReconciliationDecision(
        schema="ghimera.model-reconciliation/1",
        action="abandon_and_authorize_new_attempt",
        operation_id="owned-attempt",
        caller="native fixture caller",
        reason="caller abandons original uncertain fixture outcome",
        observed=observed,
        observed_sha256=observed.sha256,
    )


def reserve(opts, pin, chosen):
    receipt = asyncio.run(
        execute(
            caller_options(opts, pin, "reconcile_model", decision=chosen),
            source_resolver=ResolverFixture(),
        )
    )
    assert isinstance(receipt, ModelAttemptAuthorization)
    return receipt


def test_actual_command_unknown_decision_is_charged_consumed_and_ack_replayed(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts, pin = interrupted(tmp_path, cfg)
    original = read_journal(cfg.journal, opts.run_id)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    reservation = (opts.output_directory / "reservation.json").read_bytes()
    chosen = decision(opts, pin)
    authorized = reserve(opts, pin, chosen)
    reserved = read_journal(cfg.journal, opts.run_id)
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert reserved.rows[:-1] == original.rows
    assert reserved.rows[-1].model_decision == chosen
    assert reserved.rows[-1].model_intent.judge_reservation == chosen.observed.judge_calls + 1
    assert reserved.uncertain_model_calls == (
        chosen.observed.original_intent_sequence,
        authorized.attempt_intent_sequence,
    )
    recovering = caller_options(opts, pin, "recover", attempt=authorized)
    died = invoke(tmp_path, recovering, "attempt_ack")
    assert died.returncode == 75, died.stderr
    assert (
        counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)[2] == before[2] + 1
    )
    acknowledged = read_journal(cfg.journal, opts.run_id)
    assert acknowledged.uncertain_model_calls == (chosen.observed.original_intent_sequence,)
    assert not unreconciled_model_sequences(acknowledged.rows)
    done = invoke(tmp_path, recovering)
    assert done.returncode == 0, done.stderr
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert result.status == "answered"
    assert result.harvest.ledger[: len(original.rows)] == original.rows
    assert (
        result.harvest.receipt.judge_calls == chosen.observed.judge_calls + 2
    )  # new answer + original review
    assert read_journal(cfg.journal, opts.run_id).uncertain_model_calls == (
        chosen.observed.original_intent_sequence,
    )
    assert len([row for row in result.harvest.ledger if row.model_attempt is not None]) == 1
    assert len([row for row in result.harvest.ledger if row.model_replay is not None]) == 1
    after = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert before[0] == after[0] and before[1] == after[1] and before[3] == after[3]
    assert after[2] == before[2] + 2
    assert (opts.output_directory / "reservation.json").read_bytes() == reservation


@pytest.mark.parametrize(
    "change",
    ["double_decision", "changed_request", "active_writer", "stale_decision", "changed_recipe"],
)
def test_native_decision_refuses_changed_or_stale_state_before_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, change
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts, pin = interrupted(tmp_path, cfg)
    chosen = decision(opts, pin)
    lease = None
    if change == "double_decision":
        reserve(opts, pin, chosen)
    elif change == "changed_request":
        path = opts.output_directory / "reservation.json"
        raw = json.loads(path.read_bytes())
        raw["request_sha256"] = "0" * 64
        path.write_text(json.dumps(raw))
    elif change == "active_writer":
        report = read_journal(cfg.journal, opts.run_id)
        lease = DirectoryLedgerSink(
            cfg, opts.run_id, report.header.goal, report.header.judge, resume_rows=report.rows
        )
    elif change == "stale_decision":
        raw = chosen.model_dump()
        raw["observed"]["last_entry_sha256"] = "0" * 64
        observed = ModelUnknownObservation.model_validate(raw["observed"])
        raw["observed_sha256"] = observed.sha256
        chosen = ModelReconciliationDecision.model_validate(raw)
    else:
        changed = GhimeraConfig.model_validate(
            dict(cfg.model_dump(), judge_budget=cfg.judge_budget + 1)
        )
        opts.config_path.write_text("\n".join(toml_lines(changed.model_dump(mode="json"))))
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    try:
        with pytest.raises((ValueError, OSError, RuntimeError, GhimeraRefused)):
            reserve(opts, pin, chosen)
    finally:
        if lease is not None:
            lease.close()
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


def test_consumed_attempt_unknown_survives_reopen_and_never_contacts_again(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts, pin = interrupted(tmp_path, cfg)
    chosen = decision(opts, pin)
    authorized = reserve(opts, pin, chosen)
    recovery = caller_options(opts, pin, "recover", attempt=authorized)
    died = invoke(tmp_path, recovery, "attempt_unknown")
    assert died.returncode == 74, died.stderr
    report = read_journal(cfg.journal, opts.run_id)
    assert len(report.uncertain_model_calls) == 2
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    refused = invoke(tmp_path, recovery)
    assert refused.returncode == 2 and refused.stderr == "command_input_invalid\n"
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert read_journal(cfg.journal, opts.run_id).rows == report.rows


def test_abandoned_original_late_ack_is_rejected_before_recovery_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    from ghimera.models import LedgerRow

    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts, pin = interrupted(tmp_path, cfg)
    chosen = decision(opts, pin)
    authorized = reserve(opts, pin, chosen)
    report = read_journal(cfg.journal, opts.run_id)
    original = report.rows[chosen.observed.original_intent_sequence]
    earlier = next(
        row.model_ack
        for row in report.rows
        if row.model_ack is not None and row.model == original.model
    )
    # Deliberately forge a late ACK from an earlier fixture response. This is
    # journal tampering, not a caller decision or proof of the original outcome.
    tampered = LedgerRow(
        sequence=len(report.rows),
        event="model_ack",
        model=original.model,
        reason="model_port_ended",
        model_ack=earlier.model_copy(
            update={"intent_sequence": chosen.observed.original_intent_sequence}
        ),
    )
    with pytest.raises(ValueError, match="caller-abandoned original"):
        validate_decisions((*report.rows, tampered))
    lease = DirectoryLedgerSink(
        cfg, opts.run_id, report.header.goal, report.header.judge, resume_rows=report.rows
    )
    try:
        lease.append(tampered)
    finally:
        lease.close()
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    with pytest.raises(GhimeraRefused):
        read_journal(cfg.journal, opts.run_id)
    refused = invoke(tmp_path, caller_options(opts, pin, "recover", attempt=authorized))
    assert refused.returncode == 2 and refused.stderr == "command_refused:ledger_sink_failed\n"
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


@pytest.mark.parametrize("limit", ["judge", "unanswered", "downtime", "decision_bytes"])
def test_original_remaining_limits_never_become_a_new_attempt_allowance(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, limit, monkeypatch
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    raw = cfg.model_dump()
    if limit == "judge":
        raw["judge_budget"] = 4
    elif limit == "unanswered":
        raw["model_work"]["max_unanswered_calls"] = 1
    elif limit == "decision_bytes":
        raw["research_recovery"]["model_reconciliation"]["max_decision_bytes"] = 1
    cfg = GhimeraConfig.model_validate(raw)
    opts, pin = interrupted(tmp_path, cfg)
    chosen = decision(opts, pin)
    if limit == "downtime":
        from ghimera.research_recovery_types import ResearchControlSnapshot

        saved = ResearchControlSnapshot.model_validate_json(
            (cfg.journal.directory / opts.run_id / "research-control.json").read_bytes()
        )
        monkeypatch.setattr(
            "ghimera.research.time.time", lambda: saved.saved_at + cfg.wall_seconds + 1
        )
    original = read_journal(cfg.journal, opts.run_id).rows
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    with pytest.raises((ValueError, GhimeraRefused, RuntimeError)):
        reserve(opts, pin, chosen)
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert read_journal(cfg.journal, opts.run_id).rows == original


def test_native_locally_ended_unknown_is_observed_not_rewritten(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts, pin = interrupted(tmp_path, cfg, "local_unknown")
    original = read_journal(cfg.journal, opts.run_id)
    assert original.rows[-2].model_ack.uncertain and original.rows[-1].event == "answer"
    chosen = decision(opts, pin)
    assert chosen.observed.acknowledgement_row_sha256 is not None
    authorized = reserve(opts, pin, chosen)
    done = invoke(tmp_path, caller_options(opts, pin, "recover", attempt=authorized))
    assert done.returncode == 0, done.stderr
    sealed = read_journal(cfg.journal, opts.run_id)
    assert sealed.rows[: len(original.rows)] == original.rows
    assert sealed.uncertain_model_calls == (chosen.observed.original_intent_sequence,)


def test_new_fields_are_inert_and_legacy_model_client_schemas_stay_exact():
    import tomllib
    from pathlib import Path

    from ghimera.models import LedgerRow
    from ghimera.research_recovery_config import ResearchRecoveryConfig
    from ghimera.research_types import AnswerDraft, AnswerReview, Assessment, ResearchPlan

    fragment = tomllib.loads(Path("examples/model-unknown-reconciliation.toml").read_text())
    policy = ResearchRecoveryConfig.model_validate(fragment["research_recovery"])
    assert policy.model_reconciliation.max_decisions_per_run == 2
    inert = CommandOptions.model_validate(
        tomllib.loads(Path("examples/collector-model-unknown-observe.toml").read_text())
    )
    assert inert.execution.operation == "observe_model_unknown"
    assert inert.execution.model_decision is None and inert.execution.model_attempt is None
    # Measured against exact ebeadc legacy source using the same pinned interpreter.
    expected = {
        ResearchPlan: "ca42524bdf8fae6a83d445f67c112c8e733385a7167ef051717505df249f3d49",
        Assessment: "6abab2df336a98b44f7ded6e198aebc0d2acff90ea2787b9ddfaacedec98bfa2",
        AnswerDraft: "6402f45c64aedcb2d3bdc92703070c1577a0a724fa5bd38766c6de044533cdd8",
        AnswerReview: "28170e1e8467ddc57d07d4e1b27a2192448ea9a56ed485899b849fc000ea5644",
    }
    projected = LedgerRow.model_json_schema()["properties"]
    assert "model_decision" not in projected and "model_attempt" not in projected
    for record, fingerprint in expected.items():
        assert (
            hashlib.sha256(
                json.dumps(record.model_json_schema(), sort_keys=True).encode()
            ).hexdigest()
            == fingerprint
        )


@pytest.mark.parametrize("phase", ["plan", "assessment", "answer", "review"])
def test_exact_native_phase_reconciliation_keeps_round_and_source_bounds(tmp_path, phase):
    from tests.test_research_continuation import assemble
    from tests.test_research_phase_recovery import configured as phase_configuration
    from tests.test_research_phase_recovery import killed_run

    cfg = phase_configuration(tmp_path)
    raw = cfg.model_dump()
    raw["research_recovery"].update(
        schema="ghimera.research-recovery/3",
        model_reconciliation=dict(
            schema="ghimera.model-reconciliation-policy/1",
            max_decisions_per_run=1,
            max_decision_bytes=16384,
        ),
    )
    cfg = GhimeraConfig.model_validate(raw)
    pin = killed_run(tmp_path, cfg, phase, unknown=True)
    loop, *_ = assemble(cfg)
    observed = loop.observe_model_unknown("research", snapshot_sha256=pin)
    chosen = ModelReconciliationDecision(
        schema="ghimera.model-reconciliation/1",
        action="abandon_and_authorize_new_attempt",
        operation_id="phase-attempt",
        caller="native control fixture",
        reason="explicit phase abandonment",
        observed=observed,
        observed_sha256=observed.sha256,
    )
    authorized = asyncio.run(loop.reconcile_model(chosen))
    fresh, route, search, planner = assemble(cfg)
    result = asyncio.run(fresh.recover("research", snapshot_sha256=pin, attempt=authorized))
    assert result.status == "answered" and len(result.rounds) <= cfg.research.max_rounds
    assert result.harvest.receipt.effective_config == cfg
    assert (
        result.harvest.receipt.judge_calls == 6
    )  # Ordinary five, plus original UNKNOWN reservation.
    assert read_journal(cfg.journal, "research").uncertain_model_calls == (
        observed.original_intent_sequence,
    )
    if phase != "plan":
        assert not route.requests and not search.requests and not planner.requests
