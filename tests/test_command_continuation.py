"""Command-level pause/restart with concrete local adapters, not research accuracy."""

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.command import CommandExecution, CommandOptions, execute
from ghimera.config import GhimeraConfig
from ghimera.continuation import CheckpointReceipt, CheckpointStore
from ghimera.journal import read_journal
from ghimera.result_archive import ArchiveReservation, ResearchResultArchive
from tests.test_collector import (
    assembled,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)
from tests.test_collector_command import options, toml_lines
from tests.test_http_fetch import ResolverFixture

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def recipe(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    values = cfg.model_dump()
    values.update(
        journal=dict(
            schema="chimera.run-journal-config/1",
            directory=tmp_path / "journal",
            max_record_bytes=2000000,
            max_journal_bytes=10000000,
            max_summary_bytes=2000000,
            max_records=2000,
        ),
        continuation=dict(
            schema="ghimera.continuation/1",
            max_checkpoint_bytes=4000000,
            clock_policy="include_downtime",
        ),
    )
    return GhimeraConfig.model_validate(values)


def starting(tmp_path, cfg):
    return options(
        tmp_path,
        cfg,
        schema="ghimera.collector-command/2",
        execution=dict(
            schema="ghimera.command-execution/1", operation="run", suspend_after_rounds=1
        ),
    )


def resumed(opts, receipt, **updates):
    values = opts.model_dump()
    values.update(
        request_path=None,
        execution=dict(
            schema="ghimera.command-execution/1",
            operation="resume",
            checkpoint_sha256=receipt.sha256,
        ),
        **updates,
    )
    return CommandOptions.model_validate(values)


def test_command_pause_resume_keeps_output_identity_and_does_not_repeat_work(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg = recipe(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = starting(tmp_path, cfg)
    paused = asyncio.run(execute(opts, source_resolver=ResolverFixture()))
    assert isinstance(paused, CheckpointReceipt)
    checkpoint = CheckpointStore(cfg, opts.run_id).read(paused.sha256)
    assert set(p.name for p in opts.output_directory.iterdir()) == {"reservation.json"}
    reservation = (opts.output_directory / "reservation.json").read_bytes()
    assert (opts.output_directory / "reservation.json").stat().st_mode & 0o777 == 0o600
    assert not (opts.output_directory / "receipt.json").exists()
    binding = ArchiveReservation.model_validate_json(reservation)
    assert binding.run_id == opts.run_id
    assert binding.request_sha256 == checkpoint.request.content_digest()
    assert binding.config_sha256 == hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()
    assert read_journal(cfg.journal, opts.run_id).state == "unsealed"
    before = dict(source_site[1]), len(search_endpoint[1]), len(encoder_endpoint[1])
    before_models = len(model_endpoint[1])
    opts.request_path.unlink()  # Resume consumes the pinned original request, not this file.
    finished = asyncio.run(execute(resumed(opts, paused), source_resolver=ResolverFixture()))
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert finished.status == result.status == "answered"
    assert before == (dict(source_site[1]), len(search_endpoint[1]), len(encoder_endpoint[1]))
    assert len(model_endpoint[1]) == before_models + 2
    assert result.harvest.ledger[: len(checkpoint.progress.harvest.ledger)] == (
        checkpoint.progress.harvest.ledger
    )
    assert read_journal(cfg.journal, opts.run_id).state == "complete"
    assert (opts.output_directory / "reservation.json").read_bytes() == reservation
    before_models = len(model_endpoint[1])
    with pytest.raises((ValueError, OSError)):
        asyncio.run(execute(resumed(opts, paused), source_resolver=ResolverFixture()))
    assert len(model_endpoint[1]) == before_models


def test_real_command_resumes_in_a_fresh_process_and_emits_receipts(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg = recipe(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = starting(tmp_path, cfg)
    # Keep exact fixture hostnames and inject only their declared loopback DNS
    # port in the child. The actual parser/command and HTTP/model adapters run.
    entrypoint = """
import sys
from ghimera import command
from tests.test_http_fetch import ResolverFixture
native_execute = command.execute
async def fixture_execute(options):
    return await native_execute(options, source_resolver=ResolverFixture())
command.execute = fixture_execute
raise SystemExit(command.main(sys.argv[1:]))
"""

    def invoke(command, filename):
        job = tmp_path / filename
        job.write_text("\n".join(toml_lines(command.model_dump(mode="json"))))
        return subprocess.run(
            [sys.executable, "-c", entrypoint, "--job", str(job), "--max-job-bytes", "100000"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )

    stopped = invoke(opts, "pause.toml")
    assert stopped.returncode == 3 and stopped.stderr == "", stopped.stderr
    paused = CheckpointReceipt.model_validate_json(stopped.stdout)
    before = dict(source_site[1]), len(search_endpoint[1]), len(encoder_endpoint[1])
    completed = invoke(resumed(opts, paused), "resume.toml")
    assert completed.returncode == 0 and completed.stderr == "", completed.stderr
    assert json.loads(completed.stdout)["status"] == "answered"
    assert before == (dict(source_site[1]), len(search_endpoint[1]), len(encoder_endpoint[1]))
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert result.status == "answered" and result.harvest.documents[0].raw


@pytest.mark.parametrize("change", ["wrong_pin", "missing", "foreign", "unsafe", "later_file"])
def test_resume_output_refusals_precede_new_source_or_model_io(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, change
):
    cfg = recipe(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = starting(tmp_path, cfg)
    paused = asyncio.run(execute(opts, source_resolver=ResolverFixture()))
    more = resumed(opts, paused)
    if change == "wrong_pin":
        execution = more.execution.model_dump()
        execution["checkpoint_sha256"] = "0" * 64
        more = CommandOptions.model_validate(dict(more.model_dump(), execution=execution))
    elif change == "missing":
        (opts.output_directory / "reservation.json").unlink()
    elif change == "foreign":
        path = opts.output_directory / "reservation.json"
        data = json.loads(path.read_bytes())
        data["run_id"] = "another-run"
        path.write_text(json.dumps(data))
    elif change == "unsafe":
        (opts.output_directory / "reservation.json").chmod(0o644)
    else:
        (opts.output_directory / "result.json").write_bytes(b"uncertain output")
    before = (
        dict(source_site[1]),
        len(search_endpoint[1]),
        len(model_endpoint[1]),
        len(encoder_endpoint[1]),
    )
    with pytest.raises((ValueError, OSError)):
        asyncio.run(execute(more, source_resolver=ResolverFixture()))
    assert before == (
        dict(source_site[1]),
        len(search_endpoint[1]),
        len(model_endpoint[1]),
        len(encoder_endpoint[1]),
    )
    assert read_journal(cfg.journal, opts.run_id).state == "unsealed"


def test_resumable_archive_has_an_exclusive_writer_and_no_empty_directory_adoption(tmp_path):
    reservation = ArchiveReservation(
        schema="ghimera.command-output/1",
        run_id="ours",
        config_sha256="a" * 64,
        request_sha256="b" * 64,
    )
    path = tmp_path / "reserved"
    first = ResearchResultArchive.reserve(path, reservation=reservation, max_bytes=1000)
    try:
        with pytest.raises(OSError):
            ResearchResultArchive.resume(path, reservation=reservation, max_bytes=1000)
    finally:
        first.close()
    second = ResearchResultArchive.resume(path, reservation=reservation, max_bytes=1000)
    second.close()
    empty = tmp_path / "empty"
    empty.mkdir(mode=0o700)
    with pytest.raises((ValueError, OSError)):
        ResearchResultArchive.resume(empty, reservation=reservation, max_bytes=1000)
    assert not tuple(empty.iterdir())
    assert ArchiveReservation.model_validate_json((path / "reservation.json").read_bytes()) == (
        reservation
    )


def test_execution_contract_is_explicit_and_legacy_serialization_is_unchanged(tmp_path):
    legacy = CommandOptions(
        schema="chimera.collector-command/1",
        config_path=tmp_path / "config.toml",
        request_path=tmp_path / "request.json",
        output_directory=tmp_path / "output",
        run_id="ours",
        max_input_bytes=1000,
        max_result_bytes=10000,
    )
    assert "execution" not in legacy.model_dump()
    assert set(legacy.model_dump()) == {
        "schema",
        "config_path",
        "request_path",
        "output_directory",
        "run_id",
        "max_input_bytes",
        "max_result_bytes",
        "bindings_path",
        "references_path",
    }
    for update in (
        dict(schema="ghimera.collector-command/2"),
        dict(request_path=None),
        dict(execution=dict(schema="ghimera.command-execution/1", operation="run")),
    ):
        with pytest.raises(ValidationError):
            CommandOptions.model_validate(dict(legacy.model_dump(), **update))
    for invalid in (
        dict(operation="resume"),
        dict(operation="run", checkpoint_sha256="a" * 64),
        dict(operation="run", suspend_after_rounds=0),
        dict(operation="run", suspend_after_rounds=True),
    ):
        with pytest.raises(ValidationError):
            CommandExecution(schema="ghimera.command-execution/1", **invalid)
    valid_resume = dict(
        schema="ghimera.collector-command/2",
        execution=dict(
            schema="ghimera.command-execution/1", operation="resume", checkpoint_sha256="a" * 64
        ),
    )
    with pytest.raises(ValidationError):
        CommandOptions.model_validate(dict(legacy.model_dump(), **valid_resume))


def test_resumable_command_can_finish_without_pause_and_reader_checks_its_binding(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg = recipe(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = starting(tmp_path, cfg)
    values = opts.model_dump()
    values["execution"].pop("suspend_after_rounds")
    opts = CommandOptions.model_validate(values)
    receipt = asyncio.run(execute(opts, source_resolver=ResolverFixture()))
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert receipt.status == result.status == "answered"
    original = (opts.output_directory / "result.json").read_bytes()
    marker = opts.output_directory / "reservation.json"
    data = json.loads(marker.read_bytes())
    data["config_sha256"] = "0" * 64
    marker.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert (opts.output_directory / "result.json").read_bytes() == original


@pytest.mark.parametrize("filename", ["collector-resumable.toml", "collector-resume.toml"])
def test_resumable_templates_are_valid_explicit_command_contracts(filename):
    import tomllib

    opts = CommandOptions.model_validate(tomllib.loads((Path("examples") / filename).read_text()))
    assert opts.schema_version == "ghimera.collector-command/2"
    assert opts.execution is not None
    assert (opts.execution.operation == "resume") == (opts.request_path is None)
