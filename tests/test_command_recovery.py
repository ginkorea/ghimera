"""Native command process death and original-output recovery, not model quality."""

import asyncio
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.command import CommandExecution, CommandOptions, execute
from ghimera.config import GhimeraConfig
from ghimera.journal import read_journal
from ghimera.result_archive import ResearchResultArchive
from tests.test_collector import encoder_endpoint, model_endpoint, search_endpoint, source_site
from tests.test_collector_command import options, toml_lines
from tests.test_command_continuation import recipe
from tests.test_http_fetch import ResolverFixture

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint):
    cfg = recipe(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    values = cfg.model_dump()
    values["model_work"] = dict(
        schema="ghimera.model-work/1",
        max_input_bytes=4000000,
        max_unanswered_calls=4,
        uncertain_policy="hold",
        results=dict(
            schema="ghimera.model-results/1",
            max_result_bytes=65536,
            max_total_result_bytes=524288,
        ),
    )
    values["research_recovery"] = dict(
        schema="ghimera.research-recovery/1",
        max_snapshot_bytes=4000000,
        clock_policy="include_downtime",
        tail_policy="acknowledged_model_return",
    )
    return GhimeraConfig.model_validate(values)


ENTRYPOINT = """
import os, sys
from ghimera import command
from ghimera.model_work import ModelInvocation
from tests.test_http_fetch import ResolverFixture
native_invoke = ModelInvocation.invoke
async def dying(self, call, observe):
    intent = self._ledger.snapshot()[-1].model_intent
    phase = intent.phase if intent is not None else None
    if phase == 'answer' and sys.argv[1] == 'unknown':
        async def lost():
            os._exit(74)
        return await native_invoke(self, lost, observe)
    result = await native_invoke(self, call, observe)
    if phase == 'answer' and sys.argv[1] == 'ack':
        os._exit(73)
    return result
ModelInvocation.invoke = dying
native_execute = command.execute
async def resolved(options):
    return await native_execute(options, source_resolver=ResolverFixture())
command.execute = resolved
raise SystemExit(command.main(sys.argv[2:]))
"""


def invoke(tmp_path, opts, boundary):
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


def interrupted(tmp_path, cfg, boundary):
    opts = options(
        tmp_path,
        cfg,
        schema="ghimera.collector-command/2",
        execution=dict(schema="ghimera.command-execution/1", operation="run"),
    )
    process = invoke(tmp_path, opts, boundary)
    assert process.returncode == (74 if boundary == "unknown" else 73), process.stderr
    control = cfg.journal.directory / opts.run_id / "research-control.json"
    return opts, hashlib.sha256(control.read_bytes()).hexdigest()


def recovering(opts, pin):
    return CommandOptions.model_validate(
        dict(
            opts.model_dump(),
            schema="ghimera.collector-command/4",
            request_path=None,
            execution=dict(
                schema="ghimera.command-execution/2", operation="recover", snapshot_sha256=pin
            ),
        )
    )


def counts(source_site, search_endpoint, model_endpoint, encoder_endpoint):
    return (
        dict(source_site[1]),
        len(search_endpoint[1]),
        len(model_endpoint[1]),
        len(encoder_endpoint[1]),
    )


def test_actual_command_recovers_an_acknowledged_answer_and_original_output(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts, pin = interrupted(tmp_path, cfg, "ack")
    original = read_journal(cfg.journal, opts.run_id).rows
    reservation = (opts.output_directory / "reservation.json").read_bytes()
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts.request_path.unlink()
    done = invoke(tmp_path, recovering(opts, pin), "recover")
    assert done.returncode == 0 and not done.stderr, done.stderr
    assert json.loads(done.stdout)["status"] == "answered"
    after = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert before[0] == after[0] and before[1] == after[1] and before[3] == after[3]
    assert after[2] == before[2] + 1  # Only the original independent review is new.
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert result.harvest.ledger[: len(original)] == original
    assert sum(row.model_replay is not None for row in result.harvest.ledger) == 1
    assert (opts.output_directory / "reservation.json").read_bytes() == reservation
    assert read_journal(cfg.journal, opts.run_id).state == "complete"
    repeated = invoke(tmp_path, recovering(opts, pin), "recover")
    assert repeated.returncode == 2 and repeated.stderr == "command_input_invalid\n"
    assert after == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


@pytest.mark.parametrize(
    "change",
    ["unknown", "wrong_digest", "missing_reservation", "changed_request", "uncertain_output"],
)
def test_recovery_refuses_uncertainty_or_changed_output_before_any_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, change
):
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts, pin = interrupted(tmp_path, cfg, "unknown" if change == "unknown" else "ack")
    if change == "wrong_digest":
        pin = "0" * 64
    elif change == "missing_reservation":
        (opts.output_directory / "reservation.json").unlink()
    elif change == "changed_request":
        path = opts.output_directory / "reservation.json"
        data = json.loads(path.read_bytes())
        data["request_sha256"] = "0" * 64
        path.write_text(json.dumps(data))
    elif change == "uncertain_output":
        (opts.output_directory / "result.json").write_bytes(b"unacknowledged bytes")
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    rows = read_journal(cfg.journal, opts.run_id).rows
    recovery = recovering(opts, pin)
    with pytest.raises((ValueError, OSError)):
        asyncio.run(execute(recovery, source_resolver=ResolverFixture()))
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert read_journal(cfg.journal, opts.run_id).rows == rows


@pytest.mark.parametrize(
    "update",
    [
        {"schema_version": "ghimera.command-execution/1"},
        {"snapshot_sha256": None},
        {"checkpoint_sha256": "a" * 64},
        {"suspend_after_rounds": 1},
        {"operation": "run"},
    ],
)
def test_recovery_execution_is_explicitly_versioned_and_exact(update):
    raw = dict(
        schema_version="ghimera.command-execution/2", operation="recover", snapshot_sha256="a" * 64
    )
    with pytest.raises(ValidationError):
        CommandExecution.model_validate(dict(raw, **update))


def test_recovery_example_uses_its_own_command_schema():
    example = Path(__file__).parents[1] / "examples" / "collector-recover.toml"
    opts = CommandOptions.model_validate(tomllib.loads(example.read_text()))
    assert opts.schema_version == "ghimera.collector-command/4"
    assert opts.request_path is None and opts.execution.operation == "recover"
    for update in (
        {"schema": "ghimera.collector-command/2"},
        {"request_path": Path("/replacement/request.json")},
    ):
        with pytest.raises(ValidationError):
            CommandOptions.model_validate(dict(opts.model_dump(), **update))


def test_explicit_human_assistance_remains_available_to_the_recovery_caller():
    from tests.test_terminal_assistance import policy

    example = Path(__file__).parents[1] / "examples" / "collector-recover.toml"
    opts = CommandOptions.model_validate(tomllib.loads(example.read_text()))
    interactive = CommandOptions.model_validate(dict(opts.model_dump(), human_assistance=policy()))
    assert interactive.execution == opts.execution
    assert interactive.human_assistance == policy()
    assert CommandOptions.model_validate_json(interactive.model_dump_json()) == interactive
