"""Owned background delivery against actual SQLite destinations and cancellation."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera import (
    DeliveryOutbox,
    DeliveryWorker,
    DeliveryWorkerConfig,
    DirectoryDeliverySink,
)
from ghimera.delivery_command import DeliveryCommandConfig, main
from tests.test_delivery_outbox import InterruptedSink, UnavailableSink, destination, policy
from tests.test_evidence_corpus import harvest


def worker_policy(**updates):
    raw = dict(
        schema="ghimera.delivery-worker/1",
        poll_seconds=0.02,
        shutdown_grace_seconds=1.0,
        retention="prune_acknowledged",
        max_prunes_per_cycle=1,
    )
    raw.update(updates)
    return DeliveryWorkerConfig.model_validate(raw)


async def observed(worker, predicate):
    async with asyncio.timeout(5):
        while not predicate(worker.status):
            await asyncio.sleep(0.01)


def test_background_worker_delivers_new_arrivals_and_prunes_after_readback(tmp_path):
    async def operation():
        box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
        sink = DirectoryDeliverySink(destination(tmp_path / "destination"), create=True)
        worker = DeliveryWorker(worker_policy(), outbox=box, sink=sink)
        assert worker.status.phase == "stopped"
        async with worker:
            material = await harvest(("zh", "港口研究"))
            pending = await box.enqueue(material)
            # No manual dispatch or wake-up is required for a later arrival.
            await observed(worker, lambda state: state.pruned_items == 1)
            assert await sink.result(pending.delivery_id) == material
            done = await box.state(pending.delivery_id)
            assert not done.payload_retained and done.acknowledged is not None
            assert worker.status.queue.acknowledged == 1
            assert worker.status.queue.retained_payload_bytes == 0
        assert worker.status.phase == "stopped"
        assert worker.status.acknowledged_items == 1
        assert await box.enqueue(material) == done

    asyncio.run(operation())


def test_retention_keep_never_prunes_and_wake_shortens_long_poll(tmp_path):
    async def operation():
        box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
        sink = DirectoryDeliverySink(destination(tmp_path / "destination"), create=True)
        worker = DeliveryWorker(
            worker_policy(retention="keep", poll_seconds=100.0), outbox=box, sink=sink
        )
        async with worker:
            await observed(worker, lambda state: state.cycles >= 1)
            queued = await box.enqueue(await harvest(("ja", "港口研究")))
            worker.wake()
            await observed(worker, lambda state: state.acknowledged_items == 1)
            assert worker.status.pruned_items == 0
            assert (await box.state(queued.delivery_id)).payload_retained

    asyncio.run(operation())


def test_missing_destination_does_not_starve_healthy_retention_neighbor(tmp_path):
    class MissingOne(DirectoryDeliverySink):
        missing_id = None

        async def lookup(self, identity):
            if identity == self.missing_id:
                return None
            return await super().lookup(identity)

    async def operation():
        box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
        first = await box.enqueue(await harvest(("zh", "港口研究")))
        sink = MissingOne(destination(tmp_path / "destination"), create=True)
        await box.dispatch(sink)
        sink.missing_id = first.delivery_id
        second = await box.enqueue(await harvest(("en", "port research")))
        worker = DeliveryWorker(worker_policy(), outbox=box, sink=sink)
        async with worker:
            await observed(worker, lambda state: state.pruned_items == 1)
            assert worker.status.prune_refusals >= 1
            assert (await box.state(first.delivery_id)).payload_retained
            assert not (await box.state(second.delivery_id)).payload_retained
            sink.missing_id = None
            worker.wake()
            await observed(worker, lambda state: state.pruned_items == 2)

    asyncio.run(operation())


def test_restart_recovers_cancelled_write_without_a_duplicate(tmp_path):
    async def operation():
        now = [1000.0]
        cfg = policy(tmp_path / "outbox")
        box = DeliveryOutbox(cfg, create=True, clock=lambda: now[0])
        queued = await box.enqueue(await harvest(("ko", "항만 연구")))
        sink = InterruptedSink(destination(tmp_path / "destination"), create=True)
        worker = DeliveryWorker(worker_policy(shutdown_grace_seconds=0.02), outbox=box, sink=sink)
        await worker.start()
        await asyncio.wait_for(sink.started.wait(), 3)
        await worker.stop()
        await worker.stop()  # Cancellation settlement is idempotent.
        assert worker.status.phase == "stopped"
        assert (await box.state(queued.delivery_id)).last_failure == "uncertain"
        now[0] += 11.0
        reopened = DeliveryOutbox(cfg, clock=lambda: now[0])
        recovered = DeliveryWorker(worker_policy(), outbox=reopened, sink=sink)
        async with recovered:
            await observed(recovered, lambda state: state.pruned_items == 1)
        assert sink.writes == 1
        assert (await reopened.state(queued.delivery_id)).attempts == 2

    asyncio.run(operation())


def test_exhausted_retries_stay_retained_and_visible_without_secret_diagnostics(tmp_path):
    async def operation():
        box = DeliveryOutbox(policy(tmp_path / "outbox", max_attempts=1), create=True)
        queued = await box.enqueue(await harvest(("tl", "port research")))
        worker = DeliveryWorker(worker_policy(), outbox=box, sink=UnavailableSink())
        async with worker:
            await observed(worker, lambda state: state.queue.exhausted == 1)
            assert worker.status.failed_attempts == 1
            assert worker.status.last_failure == "unavailable"
            assert "do not persist" not in worker.status.model_dump_json()
            assert (await box.state(queued.delivery_id)).payload_retained

    asyncio.run(operation())


def test_worker_rejects_wrong_destination_before_start_and_double_start(tmp_path):
    async def operation():
        box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
        sink = DirectoryDeliverySink(destination(tmp_path / "destination"), create=True)
        worker = DeliveryWorker(worker_policy(), outbox=box, sink=sink)
        await worker.start()
        with pytest.raises(ValueError, match="already"):
            await worker.start()
        await worker.stop()
        other = destination(tmp_path / "other").model_copy(
            update={"target": sink.target.model_copy(update={"revision": "different"})}
        )
        bad = DeliveryWorker(
            worker_policy(), outbox=box, sink=DirectoryDeliverySink(other, create=True)
        )
        with pytest.raises(ValueError, match="destination"):
            await bad.start()
        assert bad.status.phase == "failed"

    asyncio.run(operation())


@pytest.mark.parametrize(
    "updates",
    [
        {"poll_seconds": 0},
        {"shutdown_grace_seconds": float("inf")},
        {"retention": "delete_pending"},
        {"max_prunes_per_cycle": True},
    ],
)
def test_worker_configuration_refuses_invalid_operational_choices(updates):
    with pytest.raises(ValidationError):
        worker_policy(**updates)


def test_worker_example_is_inert_and_explicit():
    cfg = DeliveryWorkerConfig.model_validate(
        tomllib.loads(Path("examples/delivery-worker.toml").read_text())
    )
    assert cfg.retention == "keep"
    assert cfg.poll_seconds > 0 and cfg.max_prunes_per_cycle > 0


@pytest.mark.parametrize("interrupted", [False, True])
def test_actual_command_delivers_retained_result_then_sigterm_drains(tmp_path, interrupted):
    cfg, sink_cfg = policy(tmp_path / "outbox"), destination(tmp_path / "destination")

    async def seed():
        box = DeliveryOutbox(cfg, create=True)
        return await box.enqueue(await harvest(("zh", "港口研究")))

    pending = asyncio.run(seed())
    direct = DirectoryDeliverySink(sink_cfg, create=True)
    command = DeliveryCommandConfig(
        schema="ghimera.delivery-command/1",
        outbox=cfg,
        destination=sink_cfg,
        worker=worker_policy(),
        status_seconds=0.02,
    )
    config_path = tmp_path / "command.toml"
    # Native TOML example with replaced fixture values; the same parser is used.
    raw = Path("examples/delivery-command.toml").read_text()
    raw = raw.replace("/srv/ghimera/delivery/outbox-example", str(cfg.directory))
    raw = raw.replace("/srv/ghimera/delivery/destination-example", str(sink_cfg.directory))
    raw = raw.replace("replace-with-reviewed-destination-revision", "fixture-1")
    raw = raw.replace("owned_result_archive", "owned_archive")
    raw = raw.replace('retention = "keep"', 'retention = "prune_acknowledged"')
    raw = raw.replace("poll_seconds = 2.0", "poll_seconds = 0.02")
    raw = raw.replace("status_seconds = 10.0", "status_seconds = 0.02")
    raw = raw.replace("shutdown_grace_seconds = 30.0", "shutdown_grace_seconds = 0.02")
    config_path.write_text(raw)
    assert command.destination.target == command.outbox.target
    with (
        (tmp_path / "status.jsonl").open("w+") as report,
        (tmp_path / "errors.txt").open("w+") as errors,
    ):
        program = """
import sys
import ghimera.delivery_command as command
from tests.test_delivery_outbox import InterruptedSink
command.DirectoryDeliverySink = InterruptedSink
raise SystemExit(command.main(sys.argv[1:]))
"""
        launch = ["-c", program] if interrupted else ["-m", "ghimera.delivery_command"]
        child = subprocess.Popen(
            [
                sys.executable,
                *launch,
                "--config",
                str(config_path),
                "--max-config-bytes",
                "10000",
            ],
            stdout=report,
            stderr=errors,
            env=dict(os.environ, PYTHONPATH=str(Path.cwd() / "src"), PYTHONUNBUFFERED="1"),
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and child.poll() is None:
                report.seek(0)
                states = [json.loads(line) for line in report.readlines()]
                if (
                    interrupted and asyncio.run(direct.lookup(pending.delivery_id)) is not None
                ) or any(state["pruned_items"] == 1 for state in states):
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("real delivery command did not finish its queued copy")
            child.send_signal(signal.SIGTERM)
            assert child.wait(timeout=5) == 0
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        report.seek(0)
        assert json.loads(report.readlines()[-1])["phase"] == "stopped"
        errors.seek(0)
        assert errors.read() == ""
    reopened = DeliveryOutbox(cfg)
    state = asyncio.run(reopened.state(pending.delivery_id))
    if interrupted:
        assert state.status == "delivering" and state.payload_retained
        assert state.last_failure == "uncertain"
    else:
        assert not state.payload_retained


def test_delivery_command_has_an_inert_coherent_example_and_safe_errors(capsys, tmp_path):
    parsed = DeliveryCommandConfig.model_validate(
        tomllib.loads(Path("examples/delivery-command.toml").read_text())
    )
    assert parsed.worker.retention == "keep"
    assert parsed.outbox.target == parsed.destination.target
    assert main(["--config", str(tmp_path / "absent"), "--max-config-bytes", "10000"]) == 2
    assert capsys.readouterr().err == "delivery_io_failed\n"
    with pytest.raises(SystemExit):
        main(["--secret-input-should-not-echo"])
    assert "secret-input" not in capsys.readouterr().err


def test_unexpected_worker_failure_is_visible_to_owner_and_status(tmp_path):
    class Broken(DirectoryDeliverySink):
        async def lookup(self, identity):
            raise RuntimeError("secret fixture failure")

    async def operation():
        box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
        await box.enqueue(await harvest(("en", "port research")))
        worker = DeliveryWorker(
            worker_policy(),
            outbox=box,
            sink=Broken(destination(tmp_path / "destination"), create=True),
        )
        await worker.start()
        with pytest.raises(ExceptionGroup):
            await worker.wait()
        with pytest.raises(ExceptionGroup):
            await worker.stop()
        assert worker.status.phase == "failed"
        assert worker.status.last_failure == "worker_failed"
        assert "secret fixture" not in worker.status.model_dump_json()

    asyncio.run(operation())


def test_cancelling_context_owner_drains_its_owned_background_task(tmp_path):
    async def operation():
        box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
        queued = await box.enqueue(await harvest(("en", "port research")))
        sink = InterruptedSink(destination(tmp_path / "destination"), create=True)
        worker = DeliveryWorker(worker_policy(shutdown_grace_seconds=0.02), outbox=box, sink=sink)

        async def own():
            async with worker:
                await worker.wait()

        owner = asyncio.create_task(own())
        await asyncio.wait_for(sink.started.wait(), 3)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert worker.status.phase == "stopped"
        assert (await box.state(queued.delivery_id)).status == "delivering"
        await worker.stop()

    asyncio.run(operation())
