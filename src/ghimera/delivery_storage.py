"""Transactional outbox claims, bounded result bytes and non-secret retry audit."""

import math
import uuid
from typing import Literal

from pydantic import TypeAdapter

from ghimera.delivery_config import DeliveryOutboxConfig
from ghimera.delivery_types import DeliveryAck, DeliveryItem, DeliveryQueueSummary, DeliveryState
from ghimera.private_database import PrivateDatabase

StateName = Literal["pending", "delivering", "acknowledged"]
Failure = Literal["unavailable", "refused", "uncertain"]
_STATE = TypeAdapter(tuple[StateName, int, bytes | None, Failure | None, int, str, int])
_CLAIM = TypeAdapter(tuple[str, int])


class DeliveryStorage:
    def __init__(self, config: DeliveryOutboxConfig, *, create: bool = False) -> None:
        self.config = DeliveryOutboxConfig.model_validate(config.model_dump())
        self.private = PrivateDatabase(
            config.directory,
            "outbox.sqlite",
            timeout=config.database_timeout_seconds,
            create=create,
        )
        try:
            if create:
                self.private.db.executescript(
                    "CREATE TABLE metadata(id INTEGER PRIMARY KEY CHECK(id=1),"
                    " schema TEXT NOT NULL,target BLOB NOT NULL,identity TEXT NOT NULL);"
                    "CREATE TABLE items(id TEXT PRIMARY KEY,payload BLOB,"
                    " payload_sha TEXT NOT NULL,payload_bytes INTEGER NOT NULL,"
                    " state TEXT NOT NULL,attempts INTEGER NOT NULL,next_attempt REAL NOT NULL,"
                    " claim TEXT,lease_until REAL NOT NULL,ack BLOB,failure TEXT);"
                    "CREATE TABLE attempts(claim TEXT PRIMARY KEY,item TEXT NOT NULL"
                    " REFERENCES items(id),started REAL NOT NULL,policy TEXT NOT NULL,"
                    " outcome TEXT NOT NULL);"
                    "CREATE INDEX eligible_items ON items(state,next_attempt,lease_until);"
                )
                with self.private.transaction():
                    self.private.db.execute(
                        "INSERT INTO metadata VALUES(1,?,?,?)",
                        (
                            "ghimera.delivery-store/1",
                            config.target.model_dump_json().encode(),
                            uuid.uuid4().hex,
                        ),
                    )
                self.private.seal_directory()
            self.check()
            count, size = self.private.db.execute(
                "SELECT COUNT(*),COALESCE(SUM(length(payload)),0) FROM items"
            ).fetchone()
            attempts = self.private.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            maximum = self.private.db.execute(
                "SELECT COALESCE(MAX(length(payload)),0) FROM items"
            ).fetchone()[0]
            ack_maximum = self.private.db.execute(
                "SELECT COALESCE(MAX(length(ack)),0) FROM items"
            ).fetchone()[0]
            if (
                int(count) > config.max_items
                or int(size) + int(count) * config.max_ack_bytes > config.max_total_item_bytes
                or int(attempts) > config.max_attempt_records
                or int(maximum) > config.max_item_bytes
                or int(ack_maximum) > config.max_ack_bytes
            ):
                raise ValueError("retained outbox exceeds its explicit capacity")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.private.close()

    def check(self) -> str:
        self.private.check()
        schema, target, identity = TypeAdapter(tuple[str, bytes, str]).validate_python(
            self.private.db.execute(
                "SELECT schema,target,identity FROM metadata WHERE id=1"
            ).fetchone(),
            strict=True,
        )
        if (
            schema != "ghimera.delivery-store/1"
            or target != self.config.target.model_dump_json().encode()
            or len(identity) != 32
            or uuid.UUID(identity).hex != identity
        ):
            raise ValueError("outbox requires its exact recorded schema and destination")
        return identity

    @staticmethod
    def time_check(now: float) -> None:
        if not math.isfinite(now) or now < 0:
            raise ValueError("outbox clock must provide finite nonnegative epoch seconds")

    def enqueue(self, item: DeliveryItem, now: float) -> DeliveryState:
        self.check()
        self.time_check(now)
        item = DeliveryItem.model_validate(item.model_dump())
        data = item.model_dump_json().encode()
        if item.target != self.config.target or len(data) > self.config.max_item_bytes:
            raise ValueError("outbox result must fit its explicit target and payload allowance")
        with self.private.transaction():
            previous = self.private.db.execute(
                "SELECT payload_sha,payload_bytes FROM items WHERE id=?", (item.identity,)
            ).fetchone()
            if previous is not None:
                if TypeAdapter(tuple[str, int]).validate_python(previous, strict=True) != (
                    item.payload_sha256,
                    len(item.payload),
                ):
                    raise ValueError("existing delivery identity has different result bytes")
            else:
                count, size = self.private.db.execute(
                    "SELECT COUNT(*),COALESCE(SUM(length(payload)),0) FROM items"
                ).fetchone()
                if (
                    int(count) >= self.config.max_items
                    or int(size) + len(data) + (int(count) + 1) * self.config.max_ack_bytes
                    > self.config.max_total_item_bytes
                ):
                    raise ValueError("outbox capacity exhausted; acknowledged rotation is explicit")
                self.private.db.execute(
                    "INSERT INTO items VALUES(?,?,?,?,'pending',0,?,NULL,0,NULL,NULL)",
                    (item.identity, data, item.payload_sha256, len(item.payload), now),
                )
        return self.state(item.identity)

    def item(self, identity: str) -> DeliveryItem:
        self.check()
        row = self.private.db.execute(
            "SELECT payload FROM items WHERE id=? AND length(payload)<=?",
            (identity, self.config.max_item_bytes),
        ).fetchone()
        if row is None:
            raise ValueError("outbox result is absent, pruned or exceeds its allowance")
        (data,) = TypeAdapter(tuple[bytes]).validate_python(row, strict=True)
        item = DeliveryItem.model_validate_json(data)
        if item.identity != identity or item.target != self.config.target:
            raise ValueError("stored outbox result or destination changed")
        return item

    def state(self, identity: str) -> DeliveryState:
        self.check()
        row = self.private.db.execute(
            "SELECT state,attempts,ack,failure,payload IS NOT NULL,payload_sha,payload_bytes"
            " FROM items WHERE id=? AND (ack IS NULL OR length(ack)<=?)",
            (identity, self.config.max_ack_bytes),
        ).fetchone()
        if row is None:
            raise ValueError("outbox state is absent or its acknowledgement exceeds its allowance")
        status, attempts, raw, failure, retained, sha, size = _STATE.validate_python(
            row, strict=True
        )
        ack = DeliveryAck.model_validate_json(raw) if raw is not None else None
        if ack is not None and (
            ack.target != self.config.target
            or ack.payload_sha256 != sha
            or ack.payload_bytes != size
        ):
            raise ValueError("stored acknowledgement does not bind the original result")
        return DeliveryState(
            schema="ghimera.delivery-state/1",
            delivery_id=identity,
            status=status,
            attempts=attempts,
            exhausted=status != "acknowledged" and attempts >= self.config.max_attempts,
            payload_retained=bool(retained),
            last_failure=failure,
            acknowledged=ack,
        )

    def claim(self, now: float) -> tuple[DeliveryItem, str] | None:
        self.check()
        self.time_check(now)
        with self.private.transaction():
            row = self.private.db.execute(
                "SELECT id,attempts FROM items WHERE payload IS NOT NULL AND attempts<?"
                " AND ((state='pending' AND next_attempt<=?)"
                " OR (state='delivering' AND lease_until<=?)) ORDER BY rowid LIMIT 1",
                (self.config.max_attempts, now, now),
            ).fetchone()
            if row is None:
                return None
            identity, attempts = _CLAIM.validate_python(row, strict=True)
            count = int(self.private.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])
            if count >= self.config.max_attempt_records:
                raise ValueError("outbox attempt audit capacity exhausted")
            item = self.item(identity)
            token = uuid.uuid4().hex
            self.private.db.execute(
                "UPDATE items SET state='delivering',attempts=?,claim=?,lease_until=?,"
                "failure='uncertain' WHERE id=?",
                (attempts + 1, token, now + self.config.claim_seconds, identity),
            )
            self.private.db.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,'uncertain')",
                (token, identity, now, self.config.identity),
            )
            return item, token

    def finish(
        self,
        identity: str,
        token: str,
        now: float,
        *,
        ack: DeliveryAck | None,
        failure: Failure | None,
    ) -> DeliveryState:
        self.check()
        self.time_check(now)
        if (ack is None) == (failure is None):
            raise ValueError("one acknowledgement or one failure is required")
        data = None
        if ack is not None:
            ack = DeliveryAck.model_validate(ack.model_dump())
            ack.validate_item(self.item(identity))
            data = ack.model_dump_json().encode()
            if len(data) > self.config.max_ack_bytes:
                raise ValueError("delivery acknowledgement exceeds its reserved allowance")
        with self.private.transaction():
            row = self.private.db.execute(
                "SELECT claim,state FROM items WHERE id=?", (identity,)
            ).fetchone()
            if row != (token, "delivering"):
                raise ValueError("delivery claim is no longer owned by this attempt")
            self.private.db.execute(
                "UPDATE items SET state=?,next_attempt=?,claim=NULL,lease_until=0,"
                "ack=?,failure=? WHERE id=?",
                (
                    "acknowledged" if ack is not None else "pending",
                    now + self.config.retry_delay_seconds,
                    data,
                    failure,
                    identity,
                ),
            )
            self.private.db.execute(
                "UPDATE attempts SET outcome=? WHERE claim=?",
                ("acknowledged" if ack is not None else failure, token),
            )
        return self.state(identity)

    def prune(self, identity: str, ack: DeliveryAck) -> DeliveryState:
        """Remove only acknowledged payload bytes; retain the deduplication tombstone."""
        self.check()
        ack = DeliveryAck.model_validate(ack.model_dump())
        with self.private.transaction():
            state = self.state(identity)
            if state.status != "acknowledged" or state.acknowledged != ack:
                raise ValueError("only the exact acknowledged result may be pruned")
            self.private.db.execute("UPDATE items SET payload=NULL WHERE id=?", (identity,))
        return self.state(identity)

    def summary(self) -> DeliveryQueueSummary:
        self.check()
        counts = TypeAdapter(tuple[int, int, int, int, int, int]).validate_python(
            self.private.db.execute(
                "SELECT COUNT(*),COALESCE(SUM(state='pending'),0),"
                "COALESCE(SUM(state='delivering'),0),COALESCE(SUM(state='acknowledged'),0),"
                "COALESCE(SUM(state!='acknowledged' AND attempts>=?),0),"
                "COALESCE(SUM(length(payload)),0) FROM items",
                (self.config.max_attempts,),
            ).fetchone(),
            strict=True,
        )
        return DeliveryQueueSummary(
            schema="ghimera.delivery-queue/1",
            items=counts[0],
            pending=counts[1],
            delivering=counts[2],
            acknowledged=counts[3],
            exhausted=counts[4],
            retained_payload_bytes=counts[5],
        )

    def acknowledged_candidates(
        self, *, limit: int, after_delivery_id: str | None
    ) -> tuple[str, ...]:
        """Bounded stable cursor over retained payloads; this does not authorize pruning."""
        self.check()
        if type(limit) is not int or not 0 < limit <= self.config.max_items:
            raise ValueError("retention batch must fit the explicit outbox item allowance")
        after = 0
        if after_delivery_id is not None:
            row = self.private.db.execute(
                "SELECT rowid FROM items WHERE id=?", (after_delivery_id,)
            ).fetchone()
            if row is None:
                raise ValueError("retention cursor must name a retained outbox identity")
            (after,) = TypeAdapter(tuple[int]).validate_python(row, strict=True)
        rows = self.private.db.execute(
            "SELECT id FROM items WHERE state='acknowledged' AND payload IS NOT NULL"
            " AND rowid>? ORDER BY rowid LIMIT ?",
            (after, limit),
        ).fetchall()
        return tuple(TypeAdapter(tuple[str]).validate_python(row, strict=True)[0] for row in rows)
