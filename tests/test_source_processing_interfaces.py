"""Prepared exact-source command/service process cuts, not installed/model quality.

Do not execute while the parent gate is live. Contacts are local HTTP fixtures;
native execute, service, authenticated HTTP and Collector own all acceptance.
"""

import asyncio
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from ghimera.collection_service import CollectionServiceConfig, ServiceRecoveryPolicy
from ghimera.command import CommandExecution, CommandOptions
from ghimera.config import GhimeraConfig
from ghimera.graph import DirectoryGraphSink, ResearchGraph
from ghimera.journal import read_journal
from ghimera.result_archive import ResearchResultArchive
from ghimera.service_command import RecoveryBoundaryRequest
from ghimera.source_work import SourceWorkStore
from tests.test_collector_command import options
from tests.test_command_recovery import counts
from tests.test_source_processing_recovery import (
    configured,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def recipe_text(raw, path=(), *, array=False):
    """Test-only TOML rendering, including native graph arrays of tables."""
    result = []
    if path:
        result.append(("[[" if array else "[") + ".".join(path) + ("]]" if array else "]"))
    for key, value in raw.items():
        if (
            value is not None
            and not isinstance(value, dict)
            and not (isinstance(value, list) and value and isinstance(value[0], dict))
        ):
            result.append(key + " = " + json.dumps(value, ensure_ascii=False))
    for key, value in raw.items():
        if isinstance(value, dict):
            result.extend(recipe_text(value, path + (key,)))
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for item in value:
                result.extend(recipe_text(item, path + (key,), array=True))
    return result


def setup(tmp_path, fixtures):
    cfg = configured(tmp_path, *fixtures)
    cfg = GhimeraConfig.model_validate(
        dict(
            cfg.model_dump(),
            continuation=dict(
                schema="ghimera.continuation/1",
                max_checkpoint_bytes=4000000,
                clock_policy="include_downtime",
            ),
        )
    )
    # options' older scalar renderer cannot render graph arrays of tables.
    plain = cfg.model_copy(update={"graph": None})
    opts = options(
        tmp_path,
        plain,
        schema="ghimera.collector-command/2",
        execution=dict(schema="ghimera.command-execution/1", operation="run"),
    )
    opts.config_path.write_text("\n".join(recipe_text(cfg.model_dump(mode="json"))))
    return cfg, opts


HOOKS = r"""
from ghimera.source_work import SourceWorkStore
from ghimera.journal import DirectoryLedgerSink
from ghimera.graph import DirectoryGraphSink
save = SourceWorkStore.save_processing
def saved(self, cursor):
    save(self, cursor)
    if mode == cursor.stage and mode in {'parsed', 'parsing'}:
        os._exit(81)
SourceWorkStore.save_processing = saved
append = DirectoryLedgerSink.append
def appended(self, row):
    append(self, row)
    if mode == 'vector_ack' and row.run_encoding_ack is not None:
        original_sequence = row.run_encoding_ack.original_intent_sequence
        intent = self.committed_rows[original_sequence].run_encoding_intent
        if intent is not None and intent.purpose == 'source':
            os._exit(81)
    if mode == 'verdict_ack' and row.model_ack is not None:
        intent = self.committed_rows[row.model_ack.intent_sequence].model_intent
        if intent is not None and intent.phase == 'verdict':
            os._exit(81)
    if mode == 'unknown' and row.model_intent is not None and row.model_intent.phase == 'verdict':
        os._exit(81)
DirectoryLedgerSink.append = appended
write = DirectoryGraphSink._write
def written(self, batch):
    result = write(self, batch)
    if mode == 'graph_ack' and any(node.content_sha256 is not None for node in batch.nodes):
        os._exit(81)
    return result
DirectoryGraphSink._write = written
"""

COMMAND = (
    r"""
import asyncio, json, os, sys
from pathlib import Path
from ghimera import command
from ghimera.command import CommandOptions
from tests.test_http_fetch import ResolverFixture
mode = sys.argv[2]
"""
    + HOOKS
    + r"""
options = CommandOptions.model_validate_json(Path(sys.argv[1]).read_bytes())
receipt = asyncio.run(command.execute(options, source_resolver=ResolverFixture()))
print(receipt.model_dump_json(), flush=True)
"""
)

SERVICE = (
    r"""
import asyncio, json, os, sys
from pathlib import Path
from pydantic import SecretStr
from ghimera import collection_service as module
from ghimera.collection_service import CollectionService, CollectionServiceConfig
from ghimera.research_types import ResearchRequest
from ghimera.service_command import CollectionHttpServer, ServiceCommandConfig
from ghimera.source_processing import row_pin
from tests.test_http_fetch import ResolverFixture
mode = sys.argv[2]
"""
    + HOOKS
    + r"""
config = CollectionServiceConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
execute = module.execute
async def native(options, **kwargs):
    return await execute(options, source_resolver=ResolverFixture(), **kwargs)
module.execute = native
update = CollectionService._update
def updated(self, run_id, phase, **kwargs):
    if mode == 'output' and kwargs.get('archive') is not None:
        os._exit(81)
    return update(self, run_id, phase, **kwargs)
CollectionService._update = updated
launch = CollectionService._launch
restart_interrupted = False
def scheduled(self, run_id):
    global restart_interrupted
    if (mode == 'empty_auto' and not restart_interrupted
            and self.status(run_id).phase == 'recovering'):
        # Route-ownership witness only: after genuine native automatic admission
        # and durable attempt reservation, stop the first scheduled consumer.
        # The existing owner records the same interrupted held lifecycle as a
        # death before launch. No source/model acceptance or ACK is fabricated.
        restart_interrupted = True
        self._update(run_id, 'held', failure='interrupted')
        return
    return launch(self, run_id)
CollectionService._launch = scheduled
async def main():
    service = CollectionService(config)
    fresh = mode in {'parsed', 'graph_ack', 'vector_ack', 'verdict_ack',
                     'parsing', 'unknown', 'output'}
    await service.start(create=fresh)
    if fresh:
        run_id = service.submit(ResearchRequest(intent='find ports')).run_id
    else:
        run_id = next(iter(service._jobs))
        if service.status(run_id).phase == 'held':
            store = SourceWorkStore(service._recipe, run_id, create=False)
            try:
                pin = row_pin(store.current_processing())
            finally:
                store.close()
            if mode == 'attempts':
                service._update(run_id, 'held', failure='interrupted', snapshot_sha256=pin,
                                adoption_attempts=config.recovery.max_adoption_attempts)
            elif mode == 'cancelled':
                await service.cancel(run_id)
            api = ServiceCommandConfig(schema='ghimera.service-command/1', service=config,
                host='127.0.0.1', port=0, credential_environment_variable='FIXTURE_BEARER',
                max_request_bytes=1000000, max_header_bytes=16384,
                request_timeout_seconds=5, max_connections=4)
            server = CollectionHttpServer(api, service, credential=SecretStr('fixture-only'))
            await server.start()
            try:
                selector = dict(schema='ghimera.service-recovery-request/2',
                    boundary='source_processing', snapshot_sha256='0'*64 if mode=='drift' else pin)
                body = json.dumps(selector).encode()
                if mode in {'empty', 'empty_auto'}:
                    body = b''
                reader, writer = await asyncio.open_connection(*server.address)
                writer.write((f'POST /runs/{run_id}/recover HTTP/1.1\r\n'
                    'Authorization: Bearer fixture-only\r\n'
                    f'Content-Length: {len(body)}\r\n\r\n').encode()+body)
                await writer.drain()
                response = await reader.read()
                writer.close()
                await writer.wait_closed()
                print(response.split(b'\r\n',1)[0].decode(), flush=True)
            finally:
                await server.stop()
    async with asyncio.timeout(75):
        terminal = {'completed','held','failed','cancelled','paused'}
        while service.status(run_id).phase not in terminal:
            await asyncio.sleep(.01)
    print(service.status(run_id).model_dump_json(), flush=True)
    await service.stop()
asyncio.run(main())
"""
)


def child(tmp_path, config, mode, entry):
    path = tmp_path / ("child-" + mode + ".json")
    path.write_text(config.model_dump_json())
    return subprocess.run(
        [sys.executable, "-c", entry, str(path), mode],
        capture_output=True,
        text=True,
        timeout=100,
    )


def recovery(opts, pin):
    return CommandOptions.model_validate(
        dict(
            opts.model_dump(),
            schema="ghimera.collector-command/9",
            request_path=None,
            execution=dict(
                schema="ghimera.command-execution/7",
                operation="recover",
                recovery_boundary="source_processing",
                snapshot_sha256=pin,
            ),
        )
    )


@pytest.mark.parametrize("cut", ["parsed", "graph_ack", "vector_ack", "verdict_ack"])
def test_native_command_process_cut_retains_original_output_and_charges(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, cut
):
    fixtures = source_site, search_endpoint, model_endpoint, encoder_endpoint
    cfg, opts = setup(tmp_path, fixtures)
    crashed = child(tmp_path, opts, cut, COMMAND)
    assert crashed.returncode == 81, crashed.stderr
    saved = SourceWorkStore.processing_read(cfg, opts.run_id)
    original = saved.journal.rows
    reservation = (opts.output_directory / "reservation.json").read_bytes()
    opts.request_path.unlink()
    done = child(tmp_path, recovery(opts, saved.sha256), "resume", COMMAND)
    assert done.returncode == 0, done.stderr
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert result.status == "answered"
    assert result.harvest.ledger[: len(original)] == original
    assert (opts.output_directory / "reservation.json").read_bytes() == reservation
    assert source_site[1]["/plain"] == 1
    intents = tuple(
        row.run_encoding_intent for row in result.harvest.ledger if row.run_encoding_intent
    )
    assert result.harvest.receipt.encoding_calls == len(intents) == len(encoder_endpoint[1])
    assert result.harvest.receipt.encoding_chars == sum(intent.input_chars for intent in intents)


def service_config(tmp_path, opts, *, attempts=2, on_restart="hold"):
    return CollectionServiceConfig(
        schema="ghimera.collection-service/2",
        directory=tmp_path / "service",
        command=opts,
        max_jobs=8,
        max_active_jobs=1,
        max_manifest_bytes=1000000,
        rounds_per_checkpoint=1,
        handoff_retry_seconds=0.02,
        max_handoff_attempts=3,
        shutdown_grace_seconds=0.1,
        recovery=dict(
            schema="ghimera.service-recovery/7",
            boundary="source_processing",
            on_restart=on_restart,
            max_adoption_attempts=attempts,
        ),
    )


@pytest.mark.parametrize("cut", ["parsed", "graph_ack", "vector_ack", "verdict_ack"])
def test_fresh_native_service_authenticated_processing_recovery(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, cut
):
    fixtures = source_site, search_endpoint, model_endpoint, encoder_endpoint
    cfg, opts = setup(tmp_path, fixtures)
    service = service_config(tmp_path, opts)
    crashed = child(tmp_path, service, cut, SERVICE)
    assert crashed.returncode == 81, crashed.stderr
    job_path = next(service.directory.glob("*.json"))
    run_id = json.loads(job_path.read_bytes())["run_id"]
    original = read_journal(cfg.journal, run_id).rows
    output = service.directory / (run_id + ".output")
    reservation = (output / "reservation.json").read_bytes()
    done = child(tmp_path, service, "resume", SERVICE)
    assert done.returncode == 0, done.stderr
    assert "HTTP/1.1 202" in done.stdout
    job = json.loads(done.stdout.splitlines()[-1])
    assert job["phase"] == "completed" and job["schema"] == "ghimera.collection-job/8"
    assert job["adoption_attempts"] == 1 and job["snapshot_sha256"]
    result = ResearchResultArchive.read(output, max_bytes=opts.max_result_bytes)
    assert result.harvest.ledger[: len(original)] == original
    assert source_site[1]["/plain"] == 1
    assert (output / "reservation.json").read_bytes() == reservation


@pytest.mark.parametrize(
    "control",
    [
        "parsing",
        "unknown",
        "drift",
        "attempts",
        "cancelled",
        "torn",
        "active_writer",
        "graph_drift",
        "empty",
    ],
)
def test_service_processing_refusals_make_no_new_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, control
):
    fixtures = source_site, search_endpoint, model_endpoint, encoder_endpoint
    cfg, opts = setup(tmp_path, fixtures)
    service = service_config(tmp_path, opts)
    cut = control if control in {"parsing", "unknown"} else "parsed"
    crashed = child(tmp_path, service, cut, SERVICE)
    assert crashed.returncode == 81, crashed.stderr
    if control == "torn":
        run_id = json.loads(next(service.directory.glob("*.json")).read_bytes())["run_id"]
        ledger = cfg.journal.directory / run_id / "ledger.jsonl"
        with ledger.open("ab") as stream:
            stream.write(b'{"torn":')
    writer = None
    if control in {"active_writer", "graph_drift"}:
        run_id = json.loads(next(service.directory.glob("*.json")).read_bytes())["run_id"]
        saved = SourceWorkStore.processing_read(cfg, run_id)
        if control == "active_writer":
            writer = SourceWorkStore.resume(cfg, run_id, len(saved.journal.rows), processing=saved)
        else:

            async def foreign_graph():
                graph = ResearchGraph(cfg.graph, run_id, DirectoryGraphSink(cfg.graph, run_id))
                await graph.start(
                    "find ports", expected=saved.cursor.original.progress.harvest.graph
                )
                await graph.discovered("https://unrelated.example/foreign", graph.intent_id)

            asyncio.run(foreign_graph())
    before = counts(*fixtures)
    try:
        done = child(
            tmp_path,
            service,
            control if control not in {"parsing", "unknown"} else "resume",
            SERVICE,
        )
    finally:
        if writer is not None:
            writer.close()
    assert done.returncode == 0, done.stderr
    # Graph proof is admitted by core restoration after the service reservation,
    # but still before any contact; truthful native outcome is held on failure.
    assert ("HTTP/1.1 202" if control == "graph_drift" else "HTTP/1.1 409") in done.stdout
    assert json.loads(done.stdout.splitlines()[-1])["phase"] in {"held", "cancelled"}
    assert counts(*fixtures) == before


def test_completed_original_archive_adopts_without_cursor_or_attempt_allowance(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    fixtures = source_site, search_endpoint, model_endpoint, encoder_endpoint
    cfg, opts = setup(tmp_path, fixtures)
    service = service_config(tmp_path, opts, attempts=1)
    crashed = child(tmp_path, service, "output", SERVICE)
    assert crashed.returncode == 81, crashed.stderr
    job_path = next(service.directory.glob("*.json"))
    job = json.loads(job_path.read_bytes())
    # Original service reservation survives death after complete archive ACK.
    job.update(adoption_attempts=1, snapshot_sha256="0" * 64)
    job_path.write_text(json.dumps(job))
    before = counts(*fixtures)
    done = child(tmp_path, service, "resume", SERVICE)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.splitlines()[-1])["phase"] == "completed"
    assert counts(*fixtures) == before


def test_empty_http_auto_restart_profile_cannot_admit_unfinished_processing(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    """Isolate real HTTP from the first scheduled automatic restart consumer.

    The fixture lets native restart admission reserve its original attempt,
    then interrupts only that launch through the existing held-state owner.
    HTTP still invokes native recovery; removing archive_only would admit a
    second unfinished-source attempt (202), so this is not vacuous under hold.
    It is a controlled route witness, not an additional real process-death cut.
    """
    fixtures = source_site, search_endpoint, model_endpoint, encoder_endpoint
    _, opts = setup(tmp_path, fixtures)
    service = service_config(tmp_path, opts, on_restart="adopt_acknowledged")
    crashed = child(tmp_path, service, "parsed", SERVICE)
    assert crashed.returncode == 81, crashed.stderr
    before = counts(*fixtures)
    done = child(tmp_path, service, "empty_auto", SERVICE)
    assert done.returncode == 0, done.stderr
    assert "HTTP/1.1 409" in done.stdout
    job = json.loads(done.stdout.splitlines()[-1])
    assert job["phase"] == "held" and job["failure"] == "interrupted"
    assert job["adoption_attempts"] == 1 and job["snapshot_sha256"]
    assert counts(*fixtures) == before


@pytest.mark.parametrize("version", range(1, 7))
def test_old_execution_never_accepts_processing(version):
    with pytest.raises(ValidationError):
        CommandExecution.model_validate(
            dict(
                schema=f"ghimera.command-execution/{version}",
                operation="recover",
                recovery_boundary="source_processing",
                snapshot_sha256="0" * 64,
            )
        )


def test_exact_legacy_selector_and_new_profile_refusals():
    old = dict(schema="ghimera.service-recovery-request/1", boundary="source_acquisition")
    assert RecoveryBoundaryRequest.model_validate(old).model_dump(mode="json") == old
    for bad in [
        dict(old, snapshot_sha256=None),
        dict(old, boundary="source_processing"),
        dict(schema="ghimera.service-recovery-request/2", boundary="source_processing"),
        dict(
            schema="ghimera.service-recovery-request/2",
            boundary="query_return",
            snapshot_sha256="0" * 64,
        ),
    ]:
        with pytest.raises(ValidationError):
            RecoveryBoundaryRequest.model_validate(bad)
    policy = dict(
        schema="ghimera.service-recovery/7",
        boundary="source_processing",
        on_restart="hold",
        max_adoption_attempts=2,
    )
    assert ServiceRecoveryPolicy.model_validate(policy).model_dump(mode="json") == policy
    for bad in [
        dict(policy, permitted_boundaries=None),
        dict(policy, model_reconciliation=None),
        dict(policy, schema="ghimera.service-recovery/6"),
        dict(policy, boundary="source_acquisition"),
    ]:
        with pytest.raises(ValidationError):
            ServiceRecoveryPolicy.model_validate(bad)
    with pytest.raises(ValueError, match="duplicate"):
        RecoveryBoundaryRequest.read(
            b'{"schema":"ghimera.service-recovery-request/2","boundary":"source_processing","boundary":"query_return"}'
        )
