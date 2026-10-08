"""Durable scheduler intents in the source owner's existing SQLite transaction boundary."""

import hashlib
import time

from ghimera.private_database import PrivateDatabase
from ghimera.source_work_config import SourceFrontierConfig
from ghimera.source_work_types import SourceCoordinates, SourceFrontierEntry


class SourceFrontier:
    """The source-work owner supplies the private database and total free payload budget."""

    def __init__(
        self, private: PrivateDatabase, policy: SourceFrontierConfig, *, create: bool
    ) -> None:
        self._private, self._policy = private, policy
        if create:
            private.db.executescript(
                "CREATE TABLE frontier_binding(id INTEGER PRIMARY KEY CHECK(id=1),"
                " schema TEXT NOT NULL, entry_count INTEGER NOT NULL);"
                "CREATE TABLE frontier(id TEXT PRIMARY KEY, sequence INTEGER UNIQUE NOT NULL,"
                " payload BLOB NOT NULL, sha256 TEXT NOT NULL);"
                "INSERT INTO frontier_binding VALUES(1,'ghimera.source-frontier-store/1',0);"
            )
            private.db.commit()
        entries = self.read()
        self._entries = {item.entry_id: item for item in entries}
        self._coordinates = {
            (item.request.url, item.request.depth): item.entry_id for item in entries
        }
        self.payload_bytes = sum(len(item.model_dump_json().encode()) for item in entries)

    def read(self) -> tuple[SourceFrontierEntry, ...]:
        self._private.check()
        db, policy = self._private.db, self._policy
        count = db.execute("SELECT COUNT(*) FROM frontier").fetchone()[0]
        if db.execute("SELECT schema,entry_count FROM frontier_binding WHERE id=1").fetchone() != (
            "ghimera.source-frontier-store/1",
            count,
        ):
            raise ValueError("an acknowledged frontier entry is missing")
        if (
            count > policy.max_entries
            or db.execute(
                "SELECT 1 FROM frontier WHERE length(payload)>? LIMIT 1", (policy.max_entry_bytes,)
            ).fetchone()
            is not None
        ):
            raise ValueError("frontier exceeds its declared entry capacity")
        entries: list[SourceFrontierEntry] = []
        coordinates: set[tuple[str, int]] = set()
        total = 0
        for entry_id, sequence, payload, pin in db.execute(
            "SELECT id,sequence,payload,sha256 FROM frontier ORDER BY sequence"
        ):
            if not isinstance(payload, bytes) or hashlib.sha256(payload).hexdigest() != pin:
                raise ValueError("frontier entry changed after acknowledgement")
            item = SourceFrontierEntry.model_validate_json(payload)
            if item.entry_id != entry_id or item.sequence != sequence or sequence != len(entries):
                raise ValueError("frontier identity or order changed")
            key = (item.request.url, item.request.depth)
            if key in coordinates:
                raise ValueError("one queued source cannot have ambiguous scope or ancestry")
            coordinates.add(key)
            total += len(payload)
            if total > policy.max_frontier_bytes:
                raise ValueError("frontier exceeds its declared payload capacity")
            entries.append(item)
        return tuple(entries)

    def _write(self, item: SourceFrontierEntry, *, available_bytes: int) -> None:
        payload = item.model_dump_json().encode()
        previous = self._entries.get(item.entry_id)
        delta = len(payload) - (len(previous.model_dump_json().encode()) if previous else 0)
        if (
            (previous is None and len(self._entries) >= self._policy.max_entries)
            or len(payload) > self._policy.max_entry_bytes
            or self.payload_bytes + delta > self._policy.max_frontier_bytes
            or delta > available_bytes
        ):
            raise ValueError("frontier capacity exhausted before scheduling")
        with self._private.transaction():
            self._private.db.execute(
                "INSERT INTO frontier VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "payload=excluded.payload,sha256=excluded.sha256",
                (item.entry_id, item.sequence, payload, hashlib.sha256(payload).hexdigest()),
            )
            if previous is None:
                self._private.db.execute(
                    "UPDATE frontier_binding SET entry_count=entry_count+1 WHERE id=1"
                )
        self._entries[item.entry_id] = item
        self._coordinates[(item.request.url, item.request.depth)] = item.entry_id
        self.payload_bytes += delta

    def enqueue(
        self,
        request: SourceCoordinates,
        priority: float,
        ledger_start: int,
        *,
        available_bytes: int,
    ) -> None:
        request = SourceCoordinates.model_validate(request.model_dump())
        identity = self._coordinates.get((request.url, request.depth))
        if identity is not None and identity != request.identity:
            raise ValueError("queued scope or reference ancestry changed before dispatch")
        previous = self._entries.get(request.identity)
        if previous is not None:
            if previous.discard_reason is not None:
                raise ValueError("discarded frontier work cannot be silently requeued")
        item = SourceFrontierEntry(
            schema="ghimera.source-frontier-entry/1",
            entry_id=request.identity,
            sequence=previous.sequence if previous else len(self._entries),
            request=request,
            priority=priority,
            ledger_start=previous.ledger_start if previous else ledger_start,
            queued_at=previous.queued_at if previous else time.time(),
        )
        if previous is not None and previous.priority < item.priority:
            item = SourceFrontierEntry.model_validate(
                dict(item.model_dump(), priority=previous.priority)
            )
        if item != previous:
            self._write(item, available_bytes=available_bytes)

    def require_queued(self, request: SourceCoordinates) -> None:
        item = self._entries.get(request.identity)
        if item is None or item.discard_reason is not None:
            raise ValueError("acquisition requires its acknowledged live frontier intent")

    def discard(
        self, request: SourceCoordinates, reason: str, ledger_end: int, *, available_bytes: int
    ) -> None:
        item = self._entries.get(request.identity)
        if item is None:
            raise ValueError("discarding work requires its original queued intent")
        if item.discard_reason is not None:
            return
        self._write(
            SourceFrontierEntry.model_validate(
                dict(item.model_dump(), discard_reason=reason, ledger_end=ledger_end)
            ),
            available_bytes=available_bytes,
        )
