"""Existing command/service owners adopt exact local returns, not model quality."""

import asyncio
import fcntl
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.collection_service import CollectionJob, CollectionServiceConfig, ServiceRecoveryPolicy
from ghimera.command import CommandExecution, CommandOptions, execute
from ghimera.config import GhimeraConfig
from ghimera.journal import read_journal
from ghimera.result_archive import ResearchResultArchive
from ghimera.source_work import SourceWorkFailure, SourceWorkStore
from tests.test_collector import encoder_endpoint, model_endpoint, search_endpoint, source_site
from tests.test_collector_command import toml_lines
from tests.test_command_recovery import ENTRYPOINT as COMMAND_ENTRYPOINT
from tests.test_command_recovery import counts
from tests.test_document_extraction import docx
from tests.test_http_fetch import ResolverFixture
from tests.test_local_inputs import seed
from tests.test_local_source_work import configured as local_configured
from tests.test_service_recovery import ENTRYPOINT as SERVICE_ENTRYPOINT
from tests.test_source_completion_recovery import settings as completed_settings
from tests.test_source_completion_recovery import starting as native_starting

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]

ACQUIRED_DEATH = """
from ghimera.source_work import SourceWorkStore
original_acquired=SourceWorkStore.acquired
def acquired(self, token, page, **kwargs):
    original_acquired(self, token, page, **kwargs)
    if kwargs.get('control') is not None and boundary == 'acquired':
        os._exit(79)
SourceWorkStore.acquired=acquired
"""


def configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, **options):
    service, cfg = completed_settings(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, graph=True
    )
    raw = cfg.model_dump()
    raw["research_recovery"].pop("source_completion", None)
    raw["research_recovery"].pop("model_reconciliation", None)
    raw["research_recovery"].pop("query_control", None)
    raw["research_recovery"].update(
        schema="ghimera.research-recovery/5",
        source_acquisition=dict(
            schema="ghimera.source-acquisition-recovery/1",
            execution="serial",
            max_capsule_bytes=8000000,
        ),
    )
    cfg = GhimeraConfig.model_validate(raw)
    write_recipe(service.command.config_path, cfg)
    policy = dict(
        schema="ghimera.service-recovery/5",
        boundary="source_acquisition",
        on_restart="adopt_acknowledged",
        max_adoption_attempts=2,
    )
    policy.update(options)
    service = CollectionServiceConfig.model_validate(dict(service.model_dump(), recovery=policy))
    return service, cfg


def write_recipe(path, cfg):
    text = "\n".join(
        toml_lines({k: v for k, v in cfg.model_dump(mode="json").items() if k != "graph"})
    )
    native = (
        Path("examples/research-graph.toml")
        .read_text()
        .replace(
            'sink_path = "/tmp/chimera-research-graphs"',
            "sink_path = " + json.dumps(str(cfg.graph.sink_path)),
        )
    )
    text += "\n[graph]\n" + native.replace("[[roles]]", "[[graph.roles]]").replace(
        "[[relations]]", "[[graph.relations]]"
    )
    path.write_text(text)


def starting(tmp_path, cfg):
    # The existing scalar TOML fixture cannot emit graph arrays-of-tables.
    opts = native_starting(
        tmp_path, GhimeraConfig.model_validate(dict(cfg.model_dump(), graph=None))
    )
    write_recipe(opts.config_path, cfg)
    return opts


def recovering(opts, pin):
    return CommandOptions.model_validate(
        dict(
            opts.model_dump(),
            schema="ghimera.collector-command/8",
            request_path=None,
            execution=dict(
                schema="ghimera.command-execution/6",
                operation="recover",
                recovery_boundary="source_acquisition",
                snapshot_sha256=pin,
            ),
        )
    )


def command_invoke(tmp_path, opts, boundary):
    path = tmp_path / (boundary + ".toml")
    path.write_text("\n".join(toml_lines(opts.model_dump(mode="json"))))
    entry = COMMAND_ENTRYPOINT.replace(
        "native_invoke = ModelInvocation.invoke",
        "boundary=sys.argv[1]\n" + ACQUIRED_DEATH + "\nnative_invoke = ModelInvocation.invoke",
    )
    return subprocess.run(
        [sys.executable, "-c", entry, boundary, "--job", str(path), "--max-job-bytes", "1000000"],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def service_invoke(tmp_path, service, boundary):
    path = tmp_path / "service.json"
    path.write_text(service.model_dump_json())
    entry = SERVICE_ENTRYPOINT.replace(
        "native_invoke = ModelInvocation.invoke",
        ACQUIRED_DEATH + "\nnative_invoke = ModelInvocation.invoke",
    ).replace("{'ack', 'unknown'}", "{'ack', 'unknown', 'acquired'}")
    return subprocess.run(
        [sys.executable, "-c", entry, str(path), boundary],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def test_new_boundaries_cannot_borrow_legacy_versions():
    raw = dict(
        schema="ghimera.command-execution/6",
        operation="recover",
        recovery_boundary="source_acquisition",
        snapshot_sha256="a" * 64,
    )
    selected = CommandExecution.model_validate(raw)
    assert selected.model_dump() == raw
    for version in (1, 2, 3, 4):
        with pytest.raises(ValidationError):
            CommandExecution.model_validate(
                dict(raw, schema=f"ghimera.command-execution/{version}")
            )
    with pytest.raises(ValidationError):
        CommandExecution.model_validate(dict(raw, recovery_boundary="source_completion"))
    policy = dict(
        schema="ghimera.service-recovery/5",
        on_restart="hold",
        max_adoption_attempts=1,
        boundary="source_acquisition",
    )
    assert ServiceRecoveryPolicy.model_validate(policy).model_dump() == policy
    for version in (1, 2, 3):
        with pytest.raises(ValidationError):
            ServiceRecoveryPolicy.model_validate(
                dict(policy, schema=f"ghimera.service-recovery/{version}")
            )
    for raw in (
        dict(schema="ghimera.command-execution/1", operation="run"),
        dict(schema="ghimera.command-execution/2", operation="recover", snapshot_sha256="a" * 64),
        dict(
            schema="ghimera.command-execution/3",
            operation="recover",
            snapshot_sha256="a" * 64,
            recovery_boundary="source_completion",
        ),
    ):
        assert CommandExecution.model_validate(raw).model_dump() == raw
    for version, boundary in ((1, None), (2, "source_completion")):
        raw = dict(
            schema=f"ghimera.service-recovery/{version}", on_restart="hold", max_adoption_attempts=1
        )
        if boundary is not None:
            raw["boundary"] = boundary
        assert ServiceRecoveryPolicy.model_validate(raw).model_dump() == raw
    example = CommandOptions.model_validate(
        tomllib.loads(Path("examples/collector-acquisition-recover.toml").read_text())
    )
    assert example.execution.recovery_boundary == "source_acquisition"
    fragment = tomllib.loads(
        Path("examples/collection-service-acquisition-recovery.toml").read_text()
    )
    assert (
        ServiceRecoveryPolicy.model_validate(fragment["service"]["recovery"]).on_restart == "hold"
    )


@pytest.mark.parametrize("kind", ["fetched", "owned_file"])
def test_fresh_command_uses_original_output_page_and_known_spend(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, kind
):
    service, cfg = configured(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    opts = starting(tmp_path, cfg)
    owned = None
    if kind == "owned_file":
        local = local_configured(tmp_path)
        cfg = GhimeraConfig.model_validate(
            dict(
                cfg.model_dump(),
                local_inputs=local.local_inputs,
                document_extraction=local.document_extraction,
            )
        )
        write_recipe(service.command.config_path, cfg)
        raw = docx()
        owned = tmp_path / "owned.docx"
        owned.write_bytes(raw)
        opts.request_path.write_text(
            json.dumps(
                dict(
                    intent="find ports", local_documents=[seed(owned, raw).model_dump(mode="json")]
                )
            )
        )
    dead = command_invoke(tmp_path, opts, "acquired")
    assert dead.returncode == 79, dead.stderr
    cut = SourceWorkStore.acquisition(cfg, opts.run_id)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    original_reservation = (opts.output_directory / "reservation.json").read_bytes()
    opts.request_path.unlink()
    if owned is not None:
        owned.unlink()
    done = command_invoke(tmp_path, recovering(opts, cut.sha256), "recover")
    assert done.returncode == 0 and not done.stderr, done.stderr
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert result.harvest.documents[0].raw == cut.operation.page.body
    assert result.harvest.ledger[: len(cut.journal.rows)] == cut.journal.rows
    assert result.harvest.graph is not None
    assert (
        result.harvest.receipt.elapsed_seconds
        >= cut.snapshot.progress.harvest.receipt.elapsed_seconds
    )
    assert result.harvest.receipt.fetches >= cut.snapshot.progress.harvest.receipt.fetches
    assert (opts.output_directory / "reservation.json").read_bytes() == original_reservation
    assert read_journal(cfg.journal, opts.run_id).state == "complete"
    if kind == "fetched":
        after = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
        assert after[:2] == before[:2]
        assert result.harvest.receipt.fetches == cut.snapshot.progress.harvest.receipt.fetches
    again = command_invoke(tmp_path, recovering(opts, cut.sha256), "recover")
    assert again.returncode == 2


def test_fresh_service_keeps_original_acquisition_and_restart_accounting(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    service, cfg = configured(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    dead = service_invoke(tmp_path, service, "acquired")
    assert dead.returncode == 79, dead.stderr
    manifest = json.loads(next(service.directory.glob("*.json")).read_bytes())
    assert manifest["schema"] == "ghimera.collection-job/6"
    run_id = manifest["run_id"]
    cut = SourceWorkStore.acquisition(cfg, run_id)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    reserved = service_invoke(tmp_path, service, "reserved")
    assert reserved.returncode == 75, reserved.stderr
    manifest = json.loads(next(service.directory.glob("*.json")).read_bytes())
    assert manifest["adoption_attempts"] == 1 and manifest["phase"] == "recovering"
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    done = service_invoke(tmp_path, service, "recover")
    assert done.returncode == 0 and not done.stderr, done.stderr
    job = CollectionJob.model_validate_json(done.stdout)
    assert job.phase == "completed" and job.adoption_attempts == 2
    assert job.snapshot_sha256 == cut.sha256 and job.recovery_boundary == "source_acquisition"
    after = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert after[:2] == before[:2]
    result = ResearchResultArchive.read(
        service.directory / (run_id + ".output"), max_bytes=service.command.max_result_bytes
    )
    assert result.harvest.receipt.fetches == cut.snapshot.progress.harvest.receipt.fetches
    assert result.harvest.documents[0].raw == cut.operation.page.body
    assert result.harvest.ledger[: len(cut.journal.rows)] == cut.journal.rows
    repeat = service_invoke(tmp_path, service, "recover")
    assert repeat.returncode == 0 and CollectionJob.model_validate_json(repeat.stdout) == job
    assert after == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


def test_command_unknown_tail_is_held_without_any_repeat(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    _, cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    opts = starting(tmp_path, cfg)
    stopped = command_invoke(tmp_path, opts, "unknown")
    assert stopped.returncode == 74, stopped.stderr
    journal = read_journal(cfg.journal, opts.run_id)
    assert journal.uncertain_model_calls
    import sqlite3

    with sqlite3.connect(
        cfg.journal.directory / opts.run_id / "source-work" / "operations.sqlite"
    ) as db:
        pin = db.execute("SELECT sha256 FROM source_acquisition WHERE id=1").fetchone()[0]
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    with pytest.raises((ValueError, OSError, SourceWorkFailure)):
        asyncio.run(execute(recovering(opts, pin), source_resolver=ResolverFixture()))
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


@pytest.mark.parametrize("change", ["request", "recipe", "graph", "journal_writer"])
def test_service_acquisition_refuses_changed_owners_before_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, change
):
    service, cfg = configured(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    dead = service_invoke(tmp_path, service, "acquired")
    assert dead.returncode == 79, dead.stderr
    manifest = json.loads(next(service.directory.glob("*.json")).read_bytes())
    run_id = manifest["run_id"]
    lease = None
    if change == "request":
        (service.directory / (run_id + ".request")).write_text('{"intent":"different"}')
    elif change == "recipe":
        service.command.config_path.write_text(
            service.command.config_path.read_text().replace(
                "max_operations = 100", "max_operations = 101"
            )
        )
    elif change == "graph":
        next((cfg.graph.sink_path / run_id).glob("*.json")).unlink()
    else:
        lease = os.open(cfg.journal.directory / run_id / "ledger.jsonl", os.O_RDONLY)
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    try:
        done = service_invoke(tmp_path, service, "recover")
        assert done.returncode == 0 and not done.stderr, done.stderr
        job = CollectionJob.model_validate_json(done.stdout)
        assert job.phase == "held" and job.adoption_attempts == 0
        assert job.recovery_hold in {"not_admissible", "recipe_changed"}
        assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    finally:
        if lease is not None:
            os.close(lease)


def test_service_acquisition_attempt_remains_exhausted_after_restart(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    service, _ = configured(
        tmp_path,
        source_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        max_adoption_attempts=1,
    )
    dead = service_invoke(tmp_path, service, "acquired")
    assert dead.returncode == 79, dead.stderr
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    reserved = service_invoke(tmp_path, service, "reserved")
    assert reserved.returncode == 75, reserved.stderr
    for _ in range(2):
        done = service_invoke(tmp_path, service, "recover")
        assert done.returncode == 0 and not done.stderr, done.stderr
        job = CollectionJob.model_validate_json(done.stdout)
        assert job.phase == "held" and job.adoption_attempts == 1
        assert job.recovery_hold == "attempts_exhausted" and job.snapshot_sha256
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
