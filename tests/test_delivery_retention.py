"""Age and bounded audit rotation never drop pending payloads or dedup identities."""

import asyncio

import pytest

from ghimera.delivery_config import DeliveryWorkerConfig
from ghimera.delivery_outbox import DeliveryOutbox
from ghimera.delivery_storage import DeliveryStorage
from ghimera.delivery_worker import DeliveryWorker
from ghimera.directory_delivery import DirectoryDeliverySink
from tests.test_delivery_outbox import UnavailableSink, destination, policy
from tests.test_delivery_worker import observed
from tests.test_evidence_corpus import harvest


def test_age_starts_at_acknowledgement_not_original_enqueue_and_rotation_keeps_identity(tmp_path):
    async def operation():
        now = [1000.0]
        cfg = policy(tmp_path / "outbox")
        box = DeliveryOutbox(cfg, create=True, clock=lambda: now[0])
        item = await box.enqueue(await harvest(("ja", "港口研究")))
        await box.dispatch(UnavailableSink())
        now[0] += 100
        sink = DirectoryDeliverySink(destination(tmp_path / "destination"), create=True)
        await box.dispatch(sink)
        assert await box.acknowledged_candidates(limit=1, minimum_age_seconds=10) == ()
        now[0] += 9
        assert await box.acknowledged_candidates(limit=1, minimum_age_seconds=10) == ()
        now[0] += 1
        worker = DeliveryWorker(
            DeliveryWorkerConfig(
                schema="ghimera.delivery-worker/2",
                poll_seconds=0.01,
                shutdown_grace_seconds=1,
                retention="prune_acknowledged",
                max_prunes_per_cycle=1,
                acknowledged_age_seconds=10,
                rotate_acknowledged_attempts=True,
                compact_after_pruned_items=1,
                compaction_minimum_free_bytes=1,
            ),
            outbox=box,
            sink=sink,
        )
        async with worker:
            await observed(worker, lambda state: state.pruned_items == 1)
        state = await box.state(item.delivery_id)
        assert not state.payload_retained and state.attempts == 2
        store = DeliveryStorage(cfg)
        try:
            assert store.private.db.execute("SELECT COUNT(*) FROM attempts").fetchone() == (1,)
            assert store.private.db.execute("SELECT COUNT(*) FROM items").fetchone() == (1,)
        finally:
            store.close()
        assert (await box.enqueue(await sink.result(item.delivery_id))) == state
        before = (cfg.directory / "outbox.sqlite").stat().st_size
        await box.compact(minimum_free_bytes=1)
        assert (cfg.directory / "outbox.sqlite").stat().st_size <= before
        assert await box.state(item.delivery_id) == state
        with pytest.raises(OSError, match="allowance"):
            await box.compact(minimum_free_bytes=10**30)

    asyncio.run(operation())


def test_rotation_refuses_unacknowledged_or_still_retained_bytes(tmp_path):
    async def operation():
        box = DeliveryOutbox(policy(tmp_path / "outbox"), create=True)
        item = await box.enqueue(await harvest(("en", "port research")))
        with pytest.raises(ValueError):
            await box.rotate_acknowledged_attempts(item.delivery_id)
        sink = DirectoryDeliverySink(destination(tmp_path / "destination"), create=True)
        await box.dispatch(sink)
        with pytest.raises(ValueError):
            await box.rotate_acknowledged_attempts(item.delivery_id)

    asyncio.run(operation())
