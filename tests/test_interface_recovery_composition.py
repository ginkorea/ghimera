"""Same-run native facade cuts; local replies establish lifecycle, not quality."""

import asyncio
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from ghimera.collection_service import (
    CollectionJob,
    CollectionService,
    CollectionServiceConfig,
    ServiceRecoveryPolicy,
)
from ghimera.journal import read_journal
from ghimera.result_archive import ResearchResultArchive
from ghimera.service_command import (
    CollectionHttpServer,
    RecoveryBoundaryRequest,
    ServiceCommandConfig,
)
from ghimera.source_work import SourceWorkStore
from tests.test_acquisition_interfaces import (
    ACQUIRED_DEATH,
    configured,
    encoder_endpoint,
    model_endpoint,
    recovering,
    search_endpoint,
    source_site,
    starting,
    write_recipe,
)
from tests.test_command_recovery import ENTRYPOINT as COMMAND_ENTRYPOINT
from tests.test_command_recovery import counts
from tests.test_query_control_recovery import recovering as recover_query
from tests.test_recovery_composition import combined
from tests.test_service_recovery import ENTRYPOINT as SERVICE_ENTRYPOINT

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]

QUERY_DEATH = """
from ghimera.journal import DirectoryLedgerSink
original_append=DirectoryLedgerSink.append
def appended(self,row):
    original_append(self,row)
    if boundary=='query' and row.query_ack is not None: os._exit(73)
DirectoryLedgerSink.append=appended
"""

MANUAL_HTTP = """
    if boundary in {'acquired','finish'}:
        from ghimera.service_command import CollectionHttpServer, ServiceCommandConfig
        from pydantic import SecretStr
        http=CollectionHttpServer(ServiceCommandConfig(
            schema='ghimera.service-command/1',service=config,host='127.0.0.1',port=0,
            credential_environment_variable='FIXTURE_BEARER',max_request_bytes=1000000,
            max_header_bytes=16384,request_timeout_seconds=10,max_connections=1),
            service,credential=SecretStr('fixture-only'))
        chosen='query_return' if boundary=='acquired' else 'source_acquisition'
        code,payload=await http._dispatch('POST','/runs/'+run_id+'/recover',json.dumps(
            {'schema':'ghimera.service-recovery-request/1','boundary':chosen}).encode())
        assert code==202, payload
"""


def settings(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint):
    service, cfg = configured(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    cfg = combined(cfg)
    write_recipe(service.command.config_path, cfg)
    service = CollectionServiceConfig.model_validate(
        dict(
            service.model_dump(),
            recovery=dict(
                schema="ghimera.service-recovery/6",
                on_restart="hold",
                max_adoption_attempts=2,
                permitted_boundaries=["query_return", "source_acquisition"],
            ),
        )
    )
    return service, cfg


def invoke(tmp_path, options, mode, *, service=False):
    path = tmp_path / ("paired-service.json" if service else mode + ".toml")
    if service:
        path.write_text(options.model_dump_json())
        entry = SERVICE_ENTRYPOINT.replace(
            "native_invoke = ModelInvocation.invoke",
            QUERY_DEATH + ACQUIRED_DEATH + "\nnative_invoke = ModelInvocation.invoke",
        )
        entry = entry.replace("{'ack', 'unknown'}", "{'query'}")
        entry = entry.replace(
            "        run_id = next(iter(service._jobs))",
            "        run_id = next(iter(service._jobs))\n" + MANUAL_HTTP,
        )
        args = [str(path), mode]
    else:
        from tests.test_collector_command import toml_lines

        path.write_text("\n".join(toml_lines(options.model_dump(mode="json"))))
        entry = COMMAND_ENTRYPOINT.replace(
            "native_invoke = ModelInvocation.invoke",
            "boundary=sys.argv[1]\n"
            + QUERY_DEATH
            + ACQUIRED_DEATH
            + "\nnative_invoke = ModelInvocation.invoke",
        )
        args = [mode, "--job", str(path), "--max-job-bytes", "1000000"]
    return subprocess.run(
        [sys.executable, "-c", entry, *args],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def test_manual_combined_profile_is_exact_and_never_automatic():
    raw = dict(
        schema="ghimera.service-recovery/6",
        on_restart="hold",
        max_adoption_attempts=2,
        permitted_boundaries=["query_return", "source_acquisition"],
    )
    assert json.loads(ServiceRecoveryPolicy.model_validate(raw).model_dump_json()) == raw
    fragment = tomllib.loads(
        Path("examples/collection-service-query-acquisition-recovery.toml").read_text()
    )
    assert (
        ServiceRecoveryPolicy.model_validate(fragment["service"]["recovery"]).model_dump()
        == ServiceRecoveryPolicy.model_validate(raw).model_dump()
    )
    for change in (
        dict(on_restart="adopt_acknowledged"),
        dict(boundary=None),
        dict(model_reconciliation=None),
        dict(permitted_boundaries=["query_return"]),
        dict(permitted_boundaries=["query_return", "query_return"]),
        dict(permitted_boundaries=["query_return", "source_completion"]),
    ):
        with pytest.raises(ValidationError):
            ServiceRecoveryPolicy.model_validate(dict(raw, **change))
    for version, boundary in ((4, "query_return"), (5, "source_acquisition")):
        fixed = dict(
            schema=f"ghimera.service-recovery/{version}",
            on_restart="hold",
            max_adoption_attempts=2,
            boundary=boundary,
        )
        assert ServiceRecoveryPolicy.model_validate(fixed).model_dump() == fixed
        for extra in (dict(permitted_boundaries=None), dict(model_reconciliation=None)):
            with pytest.raises(ValidationError):
                ServiceRecoveryPolicy.model_validate(dict(fixed, **extra))


@pytest.mark.parametrize("facade", ["command", "service"])
def test_original_run_crosses_query_then_acquired_cut_without_repeat(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, facade
):
    service, cfg = settings(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    command = starting(tmp_path, cfg)
    recipe = service.command.config_path.read_bytes()
    dead = invoke(
        tmp_path, service if facade == "service" else command, "query", service=facade == "service"
    )
    assert dead.returncode == 73, dead.stderr
    run_id = (
        json.loads(next(service.directory.glob("*.json")).read_bytes())["run_id"]
        if facade == "service"
        else command.run_id
    )
    output = (
        service.directory / (run_id + ".output")
        if facade == "service"
        else command.output_directory
    )
    reservation = (output / "reservation.json").read_bytes()
    original = read_journal(cfg.journal, run_id).rows
    pin = hashlib.sha256(
        (cfg.journal.directory / run_id / "research-control.json").read_bytes()
    ).hexdigest()
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    acquired = invoke(
        tmp_path,
        service if facade == "service" else recover_query(command, pin),
        "acquired",
        service=facade == "service",
    )
    assert acquired.returncode == 79, acquired.stderr
    cut = SourceWorkStore.acquisition(cfg, run_id)
    assert cut.journal.rows[: len(original)] == original
    after_query = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert after_query[1] == before[1]
    if facade == "service":
        job = CollectionJob.model_validate_json(next(service.directory.glob("*.json")).read_bytes())
        assert job.schema_version == "ghimera.collection-job/7" and job.adoption_attempts == 1
        assert job.recovery_boundary == "query_return"
    finished = invoke(
        tmp_path,
        service if facade == "service" else recovering(command, cut.sha256),
        "finish",
        service=facade == "service",
    )
    assert finished.returncode == 0 and not finished.stderr, finished.stderr
    result = ResearchResultArchive.read(output, max_bytes=command.max_result_bytes)
    assert result.harvest.ledger[: len(cut.journal.rows)] == cut.journal.rows
    assert result.harvest.documents[0].raw == cut.operation.page.body
    assert (
        counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)[:2]
        == after_query[:2]
    )
    assert result.search_calls == 1
    assert result.harvest.receipt.fetches == cut.snapshot.progress.harvest.receipt.fetches
    assert result.harvest.receipt.bytes_read == cut.snapshot.progress.harvest.receipt.bytes_read
    assert (output / "reservation.json").read_bytes() == reservation
    assert service.command.config_path.read_bytes() == recipe
    if facade == "service":
        job = CollectionJob.model_validate_json(finished.stdout)
        assert job.phase == "completed" and job.adoption_attempts == 2
        assert job.recovery_boundary == "source_acquisition"


@pytest.mark.parametrize("profile", [4, 5, 6])
def test_http_selector_never_widens_original_fixed_or_combined_permissions(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, profile
):
    service, cfg = settings(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )

    if profile != 6:
        raw = service.model_dump()
        raw["recovery"] = dict(
            schema=f"ghimera.service-recovery/{profile}",
            on_restart="hold",
            max_adoption_attempts=2,
            boundary="query_return" if profile == 4 else "source_acquisition",
        )
        service = CollectionServiceConfig.model_validate(raw)
    dead = invoke(tmp_path, service, "query", service=True)
    assert dead.returncode == 73, dead.stderr
    run_id = json.loads(next(service.directory.glob("*.json")).read_bytes())["run_id"]

    async def check():
        owner = CollectionService(service)
        await owner.start()
        http = CollectionHttpServer(
            ServiceCommandConfig(
                schema="ghimera.service-command/1",
                service=service,
                host="127.0.0.1",
                port=0,
                credential_environment_variable="FIXTURE_BEARER",
                max_request_bytes=1000000,
                max_header_bytes=16384,
                request_timeout_seconds=10,
                max_connections=1,
            ),
            owner,
            credential=SecretStr("fixture-only"),
        )
        original_job = owner.status(run_id)
        bodies = (
            b'{"schema":"ghimera.service-recovery-request/1","boundary":"query_return","boundary":"source_acquisition"}',
            b'{"schema":"ghimera.service-recovery-request/1","boundary":"source_completion"}',
            b'{"schema":"ghimera.service-recovery-request/1","boundary":"query_return","attempt":null}',
        )
        try:
            for body in bodies:
                with pytest.raises(ValueError):
                    RecoveryBoundaryRequest.read(body)
                with pytest.raises(ValueError):
                    await http._dispatch("POST", "/runs/" + run_id + "/recover", body)
            if profile != 6:
                foreign = "source_acquisition" if profile == 4 else "query_return"
                with pytest.raises(ValueError):
                    await owner.recover(run_id, boundary=foreign)
                with pytest.raises(ValueError):
                    await http._dispatch(
                        "POST",
                        "/runs/" + run_id + "/recover",
                        json.dumps(
                            dict(schema="ghimera.service-recovery-request/1", boundary=foreign)
                        ).encode(),
                    )
            else:
                with pytest.raises(ValueError):
                    await http._dispatch("POST", "/runs/" + run_id + "/recover", b"")
            assert owner.status(run_id) == original_job
            assert original_job.adoption_attempts == 0
        finally:
            await owner.stop()

    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    asyncio.run(check())
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


@pytest.mark.parametrize("profile", [4, 5])
def test_fixed_policy_rejects_coherently_retyped_manual_job_before_adoption(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, monkeypatch, profile
):
    service, cfg = settings(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    raw = service.model_dump()
    raw["recovery"] = dict(
        schema=f"ghimera.service-recovery/{profile}",
        on_restart="hold",
        max_adoption_attempts=2,
        boundary="query_return" if profile == 4 else "source_acquisition",
    )
    service = CollectionServiceConfig.model_validate(raw)
    dead = invoke(tmp_path, service, "query", service=True)
    assert dead.returncode == 73, dead.stderr
    manifest = next(service.directory.glob("*.json"))
    original = CollectionJob.model_validate_json(manifest.read_bytes())
    assert original.schema_version == f"ghimera.collection-job/{profile + 1}"
    retyped = CollectionJob.model_validate(
        dict(original.model_dump(), schema="ghimera.collection-job/7")
    )
    # Mutate only the negative manifest witness, not retained source/config bytes.
    assert retyped.policy_sha256 == original.policy_sha256 == service.identity
    assert retyped.recipe_sha256 == original.recipe_sha256
    assert retyped.request_sha256 == original.request_sha256
    assert retyped.recovery_boundary == original.recovery_boundary
    manifest.write_text(retyped.model_dump_json())
    recipe = service.command.config_path.read_bytes()
    journal = (cfg.journal.directory / original.run_id / "ledger.jsonl").read_bytes()
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)

    async def check():
        owner = CollectionService(service)
        await owner.start()
        held = owner.status(original.run_id)
        retained = manifest.read_bytes()
        launches = []
        monkeypatch.setattr(owner, "_launch", launches.append)
        try:
            assert held.phase == "held" and held.adoption_attempts == 0
            with pytest.raises(ValueError, match="fixed recovery"):
                await owner.recover(original.run_id)
            assert owner.status(original.run_id) == held
            assert manifest.read_bytes() == retained
            assert not launches
        finally:
            await owner.stop()

    asyncio.run(check())
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert service.command.config_path.read_bytes() == recipe
    assert (cfg.journal.directory / original.run_id / "ledger.jsonl").read_bytes() == journal
