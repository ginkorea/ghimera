"""Real private SQLite delivery/restart behavior; not remote/model quality."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera import (
    Collector,
    DeliveryCollector,
    DeliveryHandoffFailure,
    DeliveryOutbox,
    DeliveryOutboxConfig,
    DirectoryDeliveryConfig,
    DirectoryDeliverySink,
    QueuedCollection,
)
from ghimera.delivery_sink import DeliverySink
from ghimera.delivery_types import DeliveryItem, DeliveryTarget
from tests.test_collector import (
    assembled,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)
from tests.test_evidence_corpus import harvest
from tests.test_http_fetch import ResolverFixture

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def target():
    return DeliveryTarget(
        schema="ghimera.delivery-target/1", id="owned_archive", revision="fixture-1"
    )


def policy(path, **updates):
    raw = dict(
        schema="ghimera.delivery-outbox/1",
        directory=path,
        target=target(),
        max_items=100,
        max_item_bytes=1000000,
        max_total_item_bytes=10000000,
        max_ack_bytes=4096,
        max_attempt_records=100,
        max_attempts=3,
        max_claims_per_dispatch=1,
        dispatch_concurrency=1,
        database_timeout_seconds=2.0,
        delivery_timeout_seconds=5.0,
        claim_seconds=10.0,
        retry_delay_seconds=1.0,
    )
    raw.update(updates)
    return DeliveryOutboxConfig.model_validate(raw)


def destination(path, **updates):
    raw = dict(
        schema="ghimera.directory-delivery/1",
        directory=path,
        target=target(),
        max_items=100,
        max_item_bytes=1000000,
        max_total_item_bytes=10000000,
        database_timeout_seconds=2.0,
    )
    raw.update(updates)
    return DirectoryDeliveryConfig.model_validate(raw)


def test_pending_restart_delivery_and_explicit_acknowledged_pruning(tmp_path):
    async def operation():
        result = await harvest(("zh", "港口研究"))
        cfg = policy(tmp_path / "outbox")
        outbox = DeliveryOutbox(cfg, create=True)
        state = await outbox.enqueue(result)
        assert state.status == "pending" and state.payload_retained and not state.acknowledged
        assert await outbox.enqueue(result) == state
        reopened = DeliveryOutbox(cfg)
        assert await reopened.result(state.delivery_id) == result
        sink = DirectoryDeliverySink(destination(tmp_path / "destination"), create=True)
        with pytest.raises(ValueError, match="unacknowledged"):
            await reopened.prune_acknowledged(state.delivery_id, sink)
        (done,) = await reopened.dispatch(sink)
        assert done.status == "acknowledged" and done.attempts == 1
        assert await sink.result(done.delivery_id) == result
        assert not await reopened.dispatch(sink)
        pruned = await reopened.prune_acknowledged(done.delivery_id, sink)
        assert not pruned.payload_retained and pruned.acknowledged == done.acknowledged
        assert await reopened.enqueue(result) == pruned  # Tombstone prevents redelivery.
        with pytest.raises(ValueError, match="pruned"):
            await reopened.result(done.delivery_id)
        assert await sink.result(done.delivery_id) == result

    asyncio.run(operation())
    assert (tmp_path / "outbox").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "outbox/outbox.sqlite").stat().st_mode & 0o777 == 0o600


class InterruptedSink(DirectoryDeliverySink):
    """Actual durable write then lose the returning call, not a volatile fake ack."""

    def __init__(self, config, **kwargs):
        super().__init__(config, **kwargs)
        self.started = asyncio.Event()
        self.writes = 0

    async def write(self, item):
        self.writes += 1
        await super().write(item)
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable fixture")


def test_uncertain_cancel_reconciles_durable_write_without_duplicate(tmp_path):
    async def operation():
        now = [1000.0]
        cfg = policy(tmp_path / "outbox")
        box = DeliveryOutbox(cfg, create=True, clock=lambda: now[0])
        pending = await box.enqueue(await harvest(("ja", "港口研究")))
        sink = InterruptedSink(destination(tmp_path / "destination"), create=True)
        task = asyncio.create_task(box.dispatch(sink))
        await asyncio.wait_for(sink.started.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        uncertain = await box.state(pending.delivery_id)
        assert uncertain.status == "delivering" and uncertain.last_failure == "uncertain"
        restarted = DeliveryOutbox(cfg, clock=lambda: now[0])
        assert not await restarted.dispatch(sink)
        now[0] += 11.0
        (done,) = await restarted.dispatch(sink)
        assert done.status == "acknowledged" and done.attempts == 2
        assert sink.writes == 1

    asyncio.run(operation())


class UnavailableSink(DeliverySink):
    @property
    def target(self):
        return target()

    async def lookup(self, delivery_id):
        raise OSError("fixture unavailable: do not persist this message")

    async def write(self, item):
        raise AssertionError("unavailable lookup forbids write")


def test_retry_budget_is_durable_and_failure_messages_are_not_recorded(tmp_path):
    async def operation():
        now = [1000.0]
        cfg = policy(tmp_path / "outbox", max_attempts=2)
        box = DeliveryOutbox(cfg, create=True, clock=lambda: now[0])
        pending = await box.enqueue(await harvest(("tl", "port research")))
        (first,) = await box.dispatch(UnavailableSink())
        assert first.status == "pending" and first.last_failure == "unavailable"
        assert not await box.dispatch(UnavailableSink())
        now[0] += 2.0
        reopened = DeliveryOutbox(cfg, clock=lambda: now[0])
        (last,) = await reopened.dispatch(UnavailableSink())
        assert last.exhausted and last.attempts == 2 and last.payload_retained
        now[0] += 2.0
        assert not await reopened.dispatch(UnavailableSink())
        assert (await reopened.state(pending.delivery_id)).exhausted

    asyncio.run(operation())
    db = sqlite3.connect(tmp_path / "outbox/outbox.sqlite")
    assert db.execute("SELECT outcome FROM attempts").fetchall() == [
        ("unavailable",),
        ("unavailable",),
    ]
    assert "do not persist" not in str(db.execute("SELECT * FROM attempts").fetchall())
    db.close()


class LyingSink(DirectoryDeliverySink):
    async def lookup(self, delivery_id):
        return None


def test_destination_ack_without_readback_never_acknowledges_or_prunes(tmp_path):
    async def operation():
        box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
        await box.enqueue(await harvest(("zh", "港口研究")))
        sink = LyingSink(destination(tmp_path / "destination"), create=True)
        (state,) = await box.dispatch(sink)
        assert state.status == "pending" and state.last_failure == "refused"
        assert state.payload_retained and state.acknowledged is None
        with pytest.raises(ValueError, match="unacknowledged"):
            await box.prune_acknowledged(state.delivery_id, sink)

    asyncio.run(operation())


def test_wrong_target_and_capacity_refuse_before_delivery(tmp_path):
    async def operation():
        result = await harvest(("ko", "항만 연구"))
        tiny = DeliveryOutbox(policy(tmp_path / "tiny", max_item_bytes=1), create=True)
        with pytest.raises(ValueError, match="allowance"):
            await tiny.enqueue(result)
        box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
        pending = await box.enqueue(result)
        other = target().model_copy(update={"revision": "fixture-2"})
        sink = DirectoryDeliverySink(
            destination(tmp_path / "destination", target=other), create=True
        )
        with pytest.raises(ValueError, match="destination"):
            await box.dispatch(sink)
        assert (await box.state(pending.delivery_id)).attempts == 0
        with pytest.raises(ValueError, match="recorded"):
            DeliveryOutbox(policy(tmp_path / "outbox", target=other))

    asyncio.run(operation())


def test_two_dispatchers_do_not_claim_the_same_inflight_item(tmp_path):
    async def operation():
        cfg = policy(tmp_path / "outbox")
        first, second = DeliveryOutbox(cfg, create=True), DeliveryOutbox(cfg)
        await first.enqueue(await harvest(("zh", "港口研究")))
        sink = InterruptedSink(destination(tmp_path / "destination"), create=True)
        task = asyncio.create_task(first.dispatch(sink))
        await asyncio.wait_for(sink.started.wait(), 3)
        assert not await second.dispatch(sink)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sink.writes == 1

    asyncio.run(operation())


def test_a_slow_delivery_does_not_block_its_healthy_neighbor(tmp_path):
    class SlowOne(DirectoryDeliverySink):
        def __init__(self, cfg, **kwargs):
            super().__init__(cfg, **kwargs)
            self.slow_id = None
            self.started, self.release = asyncio.Event(), asyncio.Event()
            self.healthy = asyncio.Event()

        async def write(self, item):
            if item.identity == self.slow_id:
                self.started.set()
                await self.release.wait()
            result = await super().write(item)
            if item.identity != self.slow_id:
                self.healthy.set()
            return result

    async def operation():
        box = DeliveryOutbox(
            policy(tmp_path / "outbox", max_claims_per_dispatch=2, dispatch_concurrency=2),
            create=True,
        )
        slow = await box.enqueue(await harvest(("zh", "港口研究")))
        healthy = await box.enqueue(await harvest(("en", "ports research")))
        sink = SlowOne(destination(tmp_path / "destination"), create=True)
        sink.slow_id = slow.delivery_id
        task = asyncio.create_task(box.dispatch(sink))
        await asyncio.wait_for(sink.started.wait(), 3)
        await asyncio.wait_for(sink.healthy.wait(), 3)
        assert (await sink.lookup(healthy.delivery_id)) is not None
        assert not task.done()
        sink.release.set()
        states = await task
        assert len(states) == 2 and all(state.status == "acknowledged" for state in states)

    asyncio.run(operation())


def test_configured_research_queues_before_any_destination_contact(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)

    async def operation():
        box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
        collector = DeliveryCollector(Collector(cfg, source_resolver=ResolverFixture()), box)
        queued = await collector.run("find ports")
        assert queued.delivery.status == "pending" and queued.result.status == "answered"
        assert QueuedCollection.model_validate_json(queued.model_dump_json()) == queued
        assert await box.result(queued.delivery.delivery_id) == queued.result
        source_calls = source_site[1]["/plain"]
        assert await collector.persist(queued.result) == queued
        assert source_site[1]["/plain"] == source_calls == 1
        changed = queued.model_dump()
        changed["delivery"]["delivery_id"] = "0" * 64
        with pytest.raises(ValidationError):
            QueuedCollection.model_validate(changed)
        tiny = DeliveryOutbox(policy(tmp_path / "tiny", max_item_bytes=1), create=True)
        with pytest.raises(DeliveryHandoffFailure) as failed:
            await DeliveryCollector(
                Collector(cfg, source_resolver=ResolverFixture()), tiny
            ).persist(queued.result)
        assert failed.value.result == queued.result

    asyncio.run(operation())


def test_actual_fresh_process_dispatches_retained_native_result(tmp_path):
    cfg = policy(tmp_path / "outbox")
    sink_cfg = destination(tmp_path / "destination")

    async def seed():
        box = DeliveryOutbox(cfg, create=True)
        return await box.enqueue(await harvest(("zh", "港口研究")))

    pending = asyncio.run(seed())
    DirectoryDeliverySink(sink_cfg, create=True)
    program = """
import asyncio,json,sys
from ghimera import DeliveryOutbox,DeliveryOutboxConfig
from ghimera import DirectoryDeliverySink,DirectoryDeliveryConfig
raw=json.loads(sys.stdin.read())
box=DeliveryOutbox(DeliveryOutboxConfig.model_validate(raw['outbox']))
sink=DirectoryDeliverySink(DirectoryDeliveryConfig.model_validate(raw['sink']))
states=asyncio.run(box.dispatch(sink))
assert len(states)==1
print(states[0].model_dump_json())
"""
    child = subprocess.run(
        [sys.executable, "-c", program],
        input=json.dumps(
            {"outbox": cfg.model_dump(mode="json"), "sink": sink_cfg.model_dump(mode="json")}
        ),
        text=True,
        capture_output=True,
        timeout=20,
        env=dict(os.environ, PYTHONPATH=str(Path.cwd() / "src")),
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)["status"] == "acknowledged"
    assert json.loads(child.stdout)["delivery_id"] == pending.delivery_id


def test_final_delivery_template_cannot_be_replaced():
    with pytest.raises(TypeError, match="final"):

        class Broken(DeliverySink):
            async def deliver(self, item):
                return None


@pytest.mark.parametrize(
    "changes", [{"delivery_id": "0" * 64}, {"payload_sha256": "0" * 64}, {"payload_bytes": 1}]
)
def test_sink_template_rejects_forged_acknowledgements(tmp_path, changes):
    class Forged(DirectoryDeliverySink):
        @staticmethod
        def _ack(item):
            return DirectoryDeliverySink._ack(item).model_copy(update=changes)

    async def operation():
        result = await harvest(("zh", "港口研究"))
        item = DeliveryItem(schema="ghimera.delivery-item/1", target=target(), result=result)
        sink = Forged(destination(tmp_path / "destination"), create=True)
        with pytest.raises(ValueError, match="bind"):
            await sink.deliver(item)

    asyncio.run(operation())


@pytest.mark.parametrize(
    "changes", [{"directory": Path("/")}, {"claim_seconds": 5.0}, {"max_claims_per_dispatch": 101}]
)
def test_invalid_outbox_policy_is_not_a_runtime_choice(tmp_path, changes):
    with pytest.raises(ValidationError):
        policy(tmp_path / "outbox", **changes)


def test_non_active_examples_validate_without_storage_or_contact():
    for name, model in (
        ("delivery-outbox.toml", DeliveryOutboxConfig),
        ("directory-delivery.toml", DirectoryDeliveryConfig),
    ):
        parsed = model.model_validate(
            tomllib.loads(Path("examples", name).read_text(encoding="utf-8"))
        )
        assert parsed.target.id == "owned_result_archive"
        assert parsed.directory.is_absolute()


def test_missing_destination_copy_blocks_pruning_and_preserves_outbox_result(tmp_path):
    async def operation():
        result = await harvest(("zh", "港口研究"))
        box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
        state = await box.enqueue(result)
        sink = DirectoryDeliverySink(destination(tmp_path / "destination"), create=True)
        await box.dispatch(sink)
        database = sqlite3.connect(tmp_path / "destination/results.sqlite")
        database.execute("DELETE FROM results WHERE id=?", (state.delivery_id,))
        database.commit()
        database.close()
        with pytest.raises(ValueError, match="readback"):
            await box.prune_acknowledged(state.delivery_id, sink)
        assert (await box.state(state.delivery_id)).payload_retained
        assert await box.result(state.delivery_id) == result

    asyncio.run(operation())
