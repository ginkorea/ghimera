"""Native local HTTP/process-death lifecycle witnesses, not model accuracy."""

import asyncio
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from ghimera.collection_service import CollectionService, CollectionServiceConfig
from ghimera.collector import Collector
from ghimera.config import GhimeraConfig
from ghimera.delivery_outbox import DeliveryOutbox
from ghimera.graph import ResearchGraph
from ghimera.journal import read_journal
from ghimera.result_archive import ResearchResultArchive
from ghimera.service_command import CollectionHttpServer, ServiceCommandConfig
from tests.test_collection_service import service_config
from tests.test_collector import encoder_endpoint, model_endpoint, search_endpoint, source_site
from tests.test_collector_command import toml_lines
from tests.test_command_recovery import configured, counts
from tests.test_delivery_outbox import policy
from tests.test_evidence_corpus import config as corpus_config
from tests.test_evidence_corpus import corpus as native_corpus

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def configuration(
    tmp_path,
    source_site,
    search_endpoint,
    model_endpoint,
    encoder_endpoint,
    *,
    mode="adopt_acknowledged",
    attempts=1,
    handoff=False,
    graph=False,
):
    service = service_config(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    cfg = configured(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    values = cfg.model_dump()
    values["source_work"] = dict(
        schema="ghimera.source-work/1",
        max_operations=100,
        max_page_bytes=2000000,
        max_result_bytes=4000000,
        max_operation_bytes=8000000,
        max_store_bytes=32000000,
        database_timeout_seconds=2.0,
    )
    cfg = GhimeraConfig.model_validate(values)
    text = "\n".join(toml_lines(cfg.model_dump(mode="json")))
    if graph:
        native = Path("examples/research-graph.toml").read_text()
        native = native.replace(
            'sink_path = "/tmp/chimera-research-graphs"',
            "sink_path = " + json.dumps(str(tmp_path / "graph")),
        )
        text += "\n[graph]\n" + native.replace("[[roles]]", "[[graph.roles]]").replace(
            "[[relations]]", "[[graph.relations]]"
        )
        cfg = GhimeraConfig.model_validate(tomllib.loads(text))
    service.command.config_path.write_text(text)
    raw = dict(
        service.model_dump(),
        schema="ghimera.collection-service/2",
        max_manifest_bytes=1000000,
        recovery=dict(
            schema="ghimera.service-recovery/1", on_restart=mode, max_adoption_attempts=attempts
        ),
    )
    if handoff:
        raw["corpus"] = corpus_config(tmp_path, encoder_endpoint[0]).model_dump()
        raw["outbox"] = policy(tmp_path / "outbox").model_dump()
    return CollectionServiceConfig.model_validate(raw), cfg


ENTRYPOINT = """
import asyncio, json, os, sys
from pathlib import Path
import ghimera.collection_service as module
from ghimera.collection_service import CollectionService, CollectionServiceConfig
from ghimera.model_work import ModelInvocation
from ghimera.research_types import ResearchRequest
from tests.test_http_fetch import ResolverFixture
config = CollectionServiceConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
boundary = sys.argv[2]
native_invoke = ModelInvocation.invoke
async def dying(self, call, observe):
    intent = self._ledger.snapshot()[-1].model_intent
    phase = intent.phase if intent is not None else None
    if phase == 'answer' and boundary == 'unknown':
        async def lost():
            os._exit(74)
        return await native_invoke(self, lost, observe)
    if phase == 'review' and boundary == 'review_unknown':
        async def lost():
            os._exit(77)
        return await native_invoke(self, lost, observe)
    result = await native_invoke(self, call, observe)
    if phase == 'answer' and boundary == 'ack':
        os._exit(73)
    if phase == 'review' and boundary == 'review_ack':
        os._exit(76)
    return result
ModelInvocation.invoke = dying
native_execute = module.execute
async def resolved(options, **kwargs):
    if boundary == 'failed' and options.execution.operation == 'recover':
        raise RuntimeError('PRIVATE-EXCEPTION-NEVER-EXPOSED')
    return await native_execute(options, source_resolver=ResolverFixture(), **kwargs)
module.execute = resolved
original_launch = CollectionService._launch
def launch(self, run_id):
    if boundary == 'reserved' and self.status(run_id).phase == 'recovering':
        os._exit(75)
    return original_launch(self, run_id)
CollectionService._launch = launch
original_update = CollectionService._update
def update(self, run_id, phase, **kwargs):
    if boundary == 'output' and kwargs.get('archive') is not None:
        os._exit(78)
    return original_update(self, run_id, phase, **kwargs)
CollectionService._update = update
async def main():
    service = CollectionService(config)
    await service.start(create=boundary in {'ack', 'unknown'})
    if boundary in {'ack', 'unknown'}:
        request = ResearchRequest.model_validate_json(config.command.request_path.read_bytes())
        job = service.submit(request)
        run_id = job.run_id
    else:
        run_id = next(iter(service._jobs))
    async with asyncio.timeout(75):
        while service.status(run_id).phase not in {'completed', 'failed', 'held', 'cancelled'}:
            await asyncio.sleep(0.01)
    print(service.status(run_id).model_dump_json(), flush=True)
    await service.stop()
asyncio.run(main())
"""


def invoke(tmp_path, config, boundary):
    path = tmp_path / "service-config.json"
    path.write_text(config.model_dump_json())
    return subprocess.run(
        [sys.executable, "-c", ENTRYPOINT, str(path), boundary],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def interrupted(tmp_path, config, boundary="ack"):
    process = invoke(tmp_path, config, boundary)
    assert process.returncode == (74 if boundary == "unknown" else 73), process.stderr
    manifests = tuple(config.directory.glob("*.json"))
    assert len(manifests) == 1
    return json.loads(manifests[0].read_bytes())["run_id"]


def test_native_fresh_process_adopts_ack_and_hands_original_output_to_corpus_outbox(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config, cfg = configuration(
        tmp_path,
        source_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        handoff=True,
        graph=True,
    )
    native_corpus(config.corpus, create=True).close()
    asyncio.run(DeliveryOutbox(config.outbox, create=True).check_ready())
    run_id = interrupted(tmp_path, config)
    original = read_journal(cfg.journal, run_id).rows
    output = config.directory / (run_id + ".output")
    reservation = (output / "reservation.json").read_bytes()
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    done = invoke(tmp_path, config, "recover")
    assert done.returncode == 0 and not done.stderr, done.stderr
    job = json.loads(done.stdout)
    assert job["phase"] == "completed" and job["adoption_attempts"] == 1
    assert job["snapshot_sha256"] and job["corpus"] and job["delivery_id"]
    after = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert before[:2] == after[:2] and after[2] == before[2] + 1
    # New corpus encoding is a handoff, not repeated source-scoring work.
    assert after[3] > before[3]
    result = ResearchResultArchive.read(output, max_bytes=config.command.max_result_bytes)
    assert result.harvest.ledger[: len(original)] == original
    assert sum(row.model_replay is not None for row in result.harvest.ledger) == 1
    assert (output / "reservation.json").read_bytes() == reservation
    assert read_journal(cfg.journal, run_id).state == "complete"
    box = DeliveryOutbox(config.outbox)
    assert asyncio.run(box.state(job["delivery_id"])).status == "pending"
    repeated = invoke(tmp_path, config, "recover")
    assert repeated.returncode == 0 and json.loads(repeated.stdout) == job
    assert after == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "torn_journal",
        "snapshot",
        "request",
        "missing_reservation",
        "changed_reservation",
        "uncertain_output",
        "output_writer",
        "journal_writer",
        "source_binding",
        "graph",
        "recipe",
        "policy",
    ],
)
def test_restart_holds_uncertain_or_changed_native_evidence_without_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, change
):
    config, cfg = configuration(
        tmp_path,
        source_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        graph=change == "graph",
    )
    run_id = interrupted(tmp_path, config, "unknown" if change == "unknown" else "ack")
    journal = cfg.journal.directory / run_id
    output = config.directory / (run_id + ".output")
    lease = None
    if change == "torn_journal":
        with (journal / "ledger.jsonl").open("ab") as stream:
            stream.write(b'{"torn":')
    elif change == "snapshot":
        (journal / "research-control.json").write_bytes(b"{}")
    elif change == "request":
        (config.directory / (run_id + ".request")).write_text('{"intent":"changed"}')
    elif change == "missing_reservation":
        (output / "reservation.json").unlink()
    elif change == "changed_reservation":
        reservation = json.loads((output / "reservation.json").read_bytes())
        reservation["request_sha256"] = "0" * 64
        (output / "reservation.json").write_text(json.dumps(reservation))
    elif change == "uncertain_output":
        (output / "result.json").write_bytes(b"uncertain")
    elif change in {"output_writer", "journal_writer"}:
        lease = os.open(
            output if change == "output_writer" else journal / "ledger.jsonl", os.O_RDONLY
        )
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif change == "source_binding":
        database = next(journal.rglob("operations.sqlite"))
        with sqlite3.connect(database) as db:
            db.execute("UPDATE binding SET header='changed'")
    elif change == "graph":
        next((cfg.graph.sink_path / run_id).glob("*.json")).unlink()
    elif change == "recipe":
        config.command.config_path.write_text(
            config.command.config_path.read_text().replace(
                "max_operations = 100", "max_operations = 101"
            )
        )
    elif change == "policy":
        config = CollectionServiceConfig.model_validate(dict(config.model_dump(), max_jobs=9))
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    try:
        done = invoke(tmp_path, config, "recover")
        assert done.returncode == 0 and not done.stderr, done.stderr
        job = json.loads(done.stdout)
        assert job["phase"] == "held" and job.get("adoption_attempts", 0) == 0
        assert job["recovery_hold"] in {"not_admissible", "recipe_changed", "policy_changed"}
        assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
        repeated = invoke(tmp_path, config, "recover")
        assert repeated.returncode == 0
        assert json.loads(repeated.stdout).get("adoption_attempts", 0) == 0
        assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    finally:
        if lease is not None:
            os.close(lease)


def test_attempt_is_durable_before_launch_and_remains_exhausted_across_restart(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config, _ = configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    run_id = interrupted(tmp_path, config)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    reserved = invoke(tmp_path, config, "reserved")
    assert reserved.returncode == 75, reserved.stderr
    job = json.loads((config.directory / (run_id + ".json")).read_bytes())
    assert job["phase"] == "recovering" and job["adoption_attempts"] == 1
    assert len(job["snapshot_sha256"]) == 64
    for _ in range(2):
        reopened = invoke(tmp_path, config, "recover")
        assert reopened.returncode == 0 and not reopened.stderr
        held = json.loads(reopened.stdout)
        assert held["phase"] == "held" and held["adoption_attempts"] == 1
        assert held["snapshot_sha256"] == job["snapshot_sha256"]
        assert held["recovery_hold"] == "attempts_exhausted"
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


def test_failed_adoption_never_becomes_a_new_run_and_never_exposes_exception(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config, _ = configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, attempts=3
    )
    run_id = interrupted(tmp_path, config)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    failed = invoke(tmp_path, config, "failed")
    assert failed.returncode == 0 and not failed.stderr
    job = json.loads(failed.stdout)
    assert job["failure"] == job["recovery_hold"] == "recovery_failed"
    assert job["phase"] == "held" and job["adoption_attempts"] == 1
    assert "PRIVATE-EXCEPTION" not in failed.stdout
    assert invoke(tmp_path, config, "recover").returncode == 0

    async def operation():
        service = CollectionService(config)
        await service.start()
        assert service.status(run_id).adoption_attempts == 1
        with pytest.raises(ValueError):
            await service.recover(run_id)
        with pytest.raises(ValueError):
            service.resume(run_id)
        await service.stop()

    asyncio.run(operation())
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


def test_manual_policy_route_bounds_pause_and_cancellation_with_native_recovery_evidence(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config, _ = configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, mode="hold"
    )
    run_id = interrupted(tmp_path, config)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)

    async def operation():
        entered = asyncio.Event()

        async def blocked(options):
            assert options.schema_version == "ghimera.collector-command/4"
            assert options.execution.operation == "recover"
            assert options.execution.suspend_after_rounds is None
            entered.set()
            await asyncio.Event().wait()

        service = CollectionService(config, executor=blocked)
        await service.start()
        assert service.status(run_id).recovery_hold == "manual_required"
        http = CollectionHttpServer(
            ServiceCommandConfig(
                schema="ghimera.service-command/1",
                service=config,
                host="127.0.0.1",
                port=0,
                credential_environment_variable="FIXTURE_SERVICE_BEARER",
                max_request_bytes=1000000,
                max_header_bytes=16384,
                request_timeout_seconds=10.0,
                max_connections=4,
            ),
            service=service,
            credential=SecretStr("fixture-owner"),
        )
        code, body = await http._dispatch("POST", "/runs/" + run_id + "/recover", b"")
        assert code == 202 and json.loads(body)["adoption_attempts"] == 1
        await entered.wait()
        with pytest.raises(ValueError):
            service.pause(run_id)
        with pytest.raises(ValueError):
            await service.recover(run_id)
        assert service.health()["adoption_attempts"] == 1
        assert service.health()["states"]["recovering"] == 1
        assert (await service.cancel(run_id)).phase == "cancelled"
        await service.stop()
        await service.start()
        assert service.status(run_id).phase == "cancelled"
        await service.stop()

    asyncio.run(operation())
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


def test_service_recovery_example_and_legacy_wire_are_explicitly_versioned():
    cfg = ServiceCommandConfig.model_validate(
        tomllib.loads(Path("examples/collection-service-recovery.toml").read_text())
    ).service
    assert cfg.recovery.on_restart == "hold"
    for update in (
        {"schema": "ghimera.collection-service/1"},
        {"recovery": None},
        {"recovery": dict(cfg.recovery.model_dump(), max_adoption_attempts=0)},
    ):
        with pytest.raises(ValidationError):
            CollectionServiceConfig.model_validate(dict(cfg.model_dump(), **update))
    legacy = ServiceCommandConfig.model_validate(
        tomllib.loads(Path("examples/collection-service.toml").read_text())
    ).service
    assert "recovery" not in legacy.model_dump() and legacy.recovery is None


def test_later_native_ack_rebinds_snapshot_without_resetting_original_attempts(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config, _ = configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, attempts=2
    )
    run_id = interrupted(tmp_path, config)
    first = invoke(tmp_path, config, "review_ack")
    assert first.returncode == 76, first.stderr
    original = json.loads((config.directory / (run_id + ".json")).read_bytes())
    assert original["adoption_attempts"] == 1
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    second = invoke(tmp_path, config, "recover")
    assert second.returncode == 0 and not second.stderr, second.stderr
    done = json.loads(second.stdout)
    assert done["phase"] == "completed" and done["adoption_attempts"] == 2
    assert done["snapshot_sha256"] != original["snapshot_sha256"]
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    result = ResearchResultArchive.read(
        config.directory / (run_id + ".output"), max_bytes=config.command.max_result_bytes
    )
    assert sum(row.model_replay is not None for row in result.harvest.ledger) == 2


def test_completed_archive_without_service_ack_adopts_even_with_exhausted_model_budget(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config, _ = configuration(
        tmp_path,
        source_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        handoff=True,
        graph=True,
    )
    native_corpus(config.corpus, create=True).close()
    asyncio.run(DeliveryOutbox(config.outbox, create=True).check_ready())
    run_id = interrupted(tmp_path, config)
    lost = invoke(tmp_path, config, "output")
    assert lost.returncode == 78, lost.stderr
    original = json.loads((config.directory / (run_id + ".json")).read_bytes())
    assert original["phase"] == "recovering" and original["adoption_attempts"] == 1
    assert original["archive"] is None
    output = config.directory / (run_id + ".output")
    bytes_before = {path.name: path.read_bytes() for path in output.iterdir()}
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    adopted = invoke(tmp_path, config, "recover")
    assert adopted.returncode == 0 and not adopted.stderr, adopted.stderr
    done = json.loads(adopted.stdout)
    assert done["phase"] == "completed" and done["adoption_attempts"] == 1
    assert done["archive"] and done["corpus"] and done["delivery_id"]
    assert {path.name: path.read_bytes() for path in output.iterdir()} == bytes_before
    assert before[:3] == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)[:3]
    box = DeliveryOutbox(config.outbox)
    assert asyncio.run(box.state(done["delivery_id"])).status == "pending"


@pytest.mark.parametrize("refuse", [False, True])
def test_cancellation_during_native_preflight_cannot_resurrect_or_launch_job(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, monkeypatch, refuse
):
    config, _ = configuration(
        tmp_path,
        source_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        mode="hold",
        graph=True,
    )
    run_id = interrupted(tmp_path, config)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)

    async def operation():
        entered, release = asyncio.Event(), asyncio.Event()
        native_start = ResearchGraph.start

        async def blocked(self, intent, *, expected=None):
            entered.set()
            await release.wait()
            if refuse:
                raise ValueError("native graph refused")
            return await native_start(self, intent, expected=expected)

        monkeypatch.setattr(ResearchGraph, "start", blocked)
        service = CollectionService(config)
        await service.start()
        attempt = asyncio.create_task(service.recover(run_id))
        await entered.wait()
        assert (await service.cancel(run_id)).phase == "cancelled"
        release.set()
        with pytest.raises(ValueError, match="changed"):
            await attempt
        assert service.status(run_id).phase == "cancelled"
        assert service.status(run_id).adoption_attempts == 0
        assert service.health()["tasks"] == 0
        await service.stop()

    asyncio.run(operation())
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


def test_later_snapshot_with_unknown_intent_stays_held_without_another_attempt_or_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    config, _ = configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, attempts=2
    )
    run_id = interrupted(tmp_path, config)
    dying = invoke(tmp_path, config, "review_unknown")
    assert dying.returncode == 77, dying.stderr
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    restart = invoke(tmp_path, config, "recover")
    assert restart.returncode == 0 and not restart.stderr
    job = json.loads(restart.stdout)
    assert job["phase"] == "held" and job["adoption_attempts"] == 1
    assert job["recovery_hold"] == "not_admissible"
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert json.loads((config.directory / (run_id + ".json")).read_bytes())["phase"] == "held"


def test_changed_owning_model_identity_is_held_before_launch_or_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, monkeypatch
):
    config, _ = configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, mode="hold"
    )
    run_id = interrupted(tmp_path, config)
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    native_models = Collector.recovery_models

    def changed(self):
        models = native_models(self)
        return models.model_copy(
            update={"reviewer": models.reviewer.model_copy(update={"revision": "changed-runtime"})}
        )

    monkeypatch.setattr(Collector, "recovery_models", changed)

    async def operation():
        service = CollectionService(config)
        await service.start()
        held = await service.recover(run_id)
        assert held.phase == "held" and held.recovery_hold == "not_admissible"
        assert held.adoption_attempts == 0 and service.health()["tasks"] == 0
        await service.stop()

    asyncio.run(operation())
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


@pytest.mark.parametrize("change", ["policy", "intent", "identity", "documents"])
def test_completed_archive_admission_holds_changed_original_bindings_without_handoff(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, monkeypatch, change
):
    config, cfg = configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    run_id = interrupted(tmp_path, config)
    lost = invoke(tmp_path, config, "output")
    assert lost.returncode == 78, lost.stderr
    manifest = config.directory / (run_id + ".json")
    job = json.loads(manifest.read_bytes())
    # An inactive interrupted manifest is a supported admission state, including
    # an older service that held this proven-output window conservatively.
    job.update(phase="held", failure="interrupted")
    if change == "policy":
        manifest.write_text(json.dumps(job))
        config = CollectionServiceConfig.model_validate(dict(config.model_dump(), max_jobs=9))
    elif change == "intent":
        from ghimera.research_types import ResearchRequest

        request = ResearchRequest(intent="a different research intent")
        job["request_sha256"] = request.content_digest()
        manifest.write_text(json.dumps(job))
        (config.directory / (run_id + ".request")).write_text(request.model_dump_json())
        reservation = config.directory / (run_id + ".output") / "reservation.json"
        binding = json.loads(reservation.read_bytes())
        binding["request_sha256"] = request.content_digest()
        reservation.write_text(json.dumps(binding))
    elif change == "identity":
        native_models = Collector.recovery_models

        def changed(self):
            models = native_models(self)
            return models.model_copy(update={"search_revision": "changed-runtime"})

        monkeypatch.setattr(Collector, "recovery_models", changed)
    elif change == "documents":
        summary = cfg.journal.directory / run_id / "summary.json"
        data = json.loads(summary.read_bytes())
        data["documents"][0]["native_text_sha256"] = "0" * 64
        summary.write_text(json.dumps(data))
        assert read_journal(cfg.journal, run_id).state == "complete"
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)

    async def operation():
        service = CollectionService(config)
        await service.start()
        held = service.status(run_id)
        assert held.phase == "held" and held.archive is None
        assert held.adoption_attempts == 1 and service.health()["tasks"] == 0
        if change == "policy":
            assert held.recovery_hold == "policy_changed"
            assert not await service._adopt_completed_output(held)
        await service.stop()

    asyncio.run(operation())
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)


@pytest.mark.parametrize("phase", ["completed", "cancelled", "failed"])
def test_policy_drift_preserves_terminal_job_outcomes(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, phase
):
    config, _ = configuration(
        tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
    )
    run_id = interrupted(tmp_path, config)
    manifest = config.directory / (run_id + ".json")
    job = json.loads(manifest.read_bytes())
    failure = "execution_failed" if phase == "failed" else None
    job.update(phase=phase, failure=failure)
    manifest.write_text(json.dumps(job))
    changed = CollectionServiceConfig.model_validate(dict(config.model_dump(), max_jobs=9))
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)

    async def operation():
        service = CollectionService(changed)
        await service.start()
        held = service.status(run_id)
        assert held.phase == phase and held.failure == failure
        assert held.recovery_hold == "policy_changed" and service.health()["tasks"] == 0
        await service.stop()

    asyncio.run(operation())
    assert before == counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
