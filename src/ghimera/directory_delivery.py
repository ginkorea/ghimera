"""A real local durable destination, not an implicit remote publication service."""

import hashlib
from pathlib import Path

from pydantic import TypeAdapter

from ghimera.delivery_config import DirectoryDeliveryConfig
from ghimera.delivery_sink import DeliverySink
from ghimera.delivery_types import DeliveryAck, DeliveryItem, DeliveryTarget, Result
from ghimera.owned_worker import off_loop
from ghimera.private_database import PrivateDatabase


class DirectoryDeliverySink(DeliverySink):
    """Immutable fsynced SQLite copies on a separately configured destination.

    Reuses the shared payload/capacity contract, not the outbox's retry mechanism.
    A distinct directory is mandatory; a local copy is not off-host durability.
    """

    def __init__(self, policy: DirectoryDeliveryConfig, *, create: bool = False) -> None:
        self.policy = DirectoryDeliveryConfig.model_validate(policy.model_dump())
        store = self._open(create=create)
        try:
            if create:
                store.db.executescript(
                    "CREATE TABLE metadata(id INTEGER PRIMARY KEY CHECK(id=1),"
                    "target BLOB NOT NULL);"
                    "CREATE TABLE results(id TEXT PRIMARY KEY,payload BLOB NOT NULL);"
                )
                with store.transaction():
                    store.db.execute(
                        "INSERT INTO metadata VALUES(1,?)",
                        (self.target.model_dump_json().encode(),),
                    )
                store.seal_directory()
            self._check(store)
        finally:
            store.close()

    @property
    def target(self) -> DeliveryTarget:
        return self.policy.target

    @property
    def directory(self) -> Path:
        return self.policy.directory

    def _open(self, *, create: bool = False) -> PrivateDatabase:
        DirectoryDeliveryConfig.model_validate(self.policy.model_dump())
        return PrivateDatabase(
            self.directory,
            "results.sqlite",
            timeout=self.policy.database_timeout_seconds,
            create=create,
        )

    def _check(self, store: PrivateDatabase) -> None:
        store.check()
        (target,) = TypeAdapter(tuple[bytes]).validate_python(
            store.db.execute("SELECT target FROM metadata WHERE id=1").fetchone(), strict=True
        )
        if target != self.target.model_dump_json().encode():
            raise ValueError("destination requires its exact recorded target")

    def _read(self, store: PrivateDatabase, identity: str) -> DeliveryItem | None:
        self._check(store)
        row = store.db.execute(
            "SELECT payload,length(payload) FROM results WHERE id=? AND length(payload)<=?",
            (identity, self.policy.max_item_bytes),
        ).fetchone()
        if row is None:
            if (
                store.db.execute("SELECT 1 FROM results WHERE id=?", (identity,)).fetchone()
                is not None
            ):
                raise ValueError("destination payload exceeds its allowance")
            return None
        data, size = TypeAdapter(tuple[bytes, int]).validate_python(row, strict=True)
        if size > self.policy.max_item_bytes or len(data) != size:
            raise ValueError("destination payload exceeds its allowance")
        item = DeliveryItem.model_validate_json(data)
        if (
            hashlib.sha256(data).hexdigest() != identity
            or item.identity != identity
            or item.target != self.target
        ):
            raise ValueError("destination bytes do not match their immutable delivery identity")
        return item

    @staticmethod
    def _ack(item: DeliveryItem) -> DeliveryAck:
        return DeliveryAck(
            schema="ghimera.delivery-ack/1",
            delivery_id=item.identity,
            payload_sha256=item.payload_sha256,
            target=item.target,
            payload_bytes=len(item.payload),
            reference=item.identity,
        )

    async def lookup(self, delivery_id: str) -> DeliveryAck | None:
        def read() -> DeliveryAck | None:
            store = self._open()
            try:
                item = self._read(store, delivery_id)
                return self._ack(item) if item is not None else None
            finally:
                store.close()

        return await off_loop(read)

    async def result(self, delivery_id: str) -> Result:
        def read() -> Result:
            store = self._open()
            try:
                item = self._read(store, delivery_id)
                if item is None:
                    raise ValueError("destination result is absent")
                return item.result
            finally:
                store.close()

        return await off_loop(read)

    async def write(self, item: DeliveryItem) -> DeliveryAck:
        item = DeliveryItem.model_validate(item.model_dump())
        data = item.model_dump_json().encode()
        if item.target != self.target or len(data) > self.policy.max_item_bytes:
            raise ValueError("delivery destination cannot widen target or payload limits")

        def persist() -> DeliveryAck:
            store = self._open()
            try:
                self._check(store)
                with store.transaction():
                    previous = self._read(store, item.identity)
                    if previous is not None:
                        if previous != item:
                            raise ValueError("destination cannot overwrite an existing result")
                    else:
                        count, size = TypeAdapter(tuple[int, int]).validate_python(
                            store.db.execute(
                                "SELECT COUNT(*),COALESCE(SUM(length(payload)),0) FROM results"
                            ).fetchone(),
                            strict=True,
                        )
                        if (
                            count >= self.policy.max_items
                            or size + len(data) > self.policy.max_total_item_bytes
                        ):
                            raise ValueError("delivery destination capacity exhausted")
                        store.db.execute("INSERT INTO results VALUES(?,?)", (item.identity, data))
                return self._ack(item)
            finally:
                store.close()

        return await off_loop(persist)
