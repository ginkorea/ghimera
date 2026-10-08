"""Owner-private immutable response versions; HTTP scheduling stays in FetchLadder."""

import hashlib
import math
import sqlite3
import time
from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from ghimera.models import Page
from ghimera.owned_worker import off_loop
from ghimera.private_database import PrivateDatabase
from ghimera.response import RETAINED_HEADERS, conditional_cache_permitted
from ghimera.source_refresh_config import SourceRefreshConfig, exact_http_url
from ghimera.source_refresh_types import Digest, SourceRefreshUse, Stamp


class SourceRefreshFailure(ValueError):
    """A configured durable source store failed; never downgrade to volatile reuse."""


class SourceRefreshKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.source-refresh-key/1"] = Field(alias="schema")
    url: str
    route: Annotated[str, Field(min_length=1)]
    representation_sha256: Digest

    @model_validator(mode="after")
    def exact(self) -> "SourceRefreshKey":
        exact_http_url(self.url)
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class SourceRefreshRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.source-refresh-record/1"] = Field(alias="schema")
    sequence: Annotated[int, Field(strict=True, ge=0)]
    previous_sha256: Digest | None
    key: SourceRefreshKey
    captured_at: Stamp
    page: Page | None

    @model_validator(mode="after")
    def original_response(self) -> "SourceRefreshRecord":
        page = self.page
        if page is None:
            return self  # Durable invalidation retains no source content.
        if (
            page.status != 200
            or page.url != self.key.url
            or page.final_url != self.key.url
            or page.revalidated
            or page.source_refresh is not None
            or page.rendered is not None
            or page.human_browser is not None
            or page.local_input is not None
            or page.challenge_use is not None
            or not conditional_cache_permitted(page.headers)
            or any(name not in RETAINED_HEADERS for name, _ in page.headers)
        ):
            raise ValueError("refresh versions require a native, unmodified single-hop HTTP 200")
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def reuse(self, policy: SourceRefreshConfig, checked_at: float) -> SourceRefreshUse:
        if self.page is None:
            raise SourceRefreshFailure("an invalidated version cannot be reused")
        return SourceRefreshUse(
            schema="ghimera.source-refresh-use/1",
            policy_sha256=policy.identity,
            record_sha256=self.identity,
            source_url=self.key.url,
            source_sha256=hashlib.sha256(self.page.body).hexdigest(),
            captured_at=self.captured_at,
            checked_at=checked_at,
            observation="conditional_304",
        )


class SourceRefreshStore:
    """Short native SQLite transactions; no shared connection or background owner.

    Each operation opens its own thread-local connection and drains before return
    or cancellation. Prefix headers and selected originals are verified; versions()
    also audits every historical payload. This is integrity checking, not a signed log.
    Capacity exhaustion refuses instead of deleting historical source versions.
    """

    def __init__(self, policy: SourceRefreshConfig, *, clock: Callable[[], float] = time.time):
        self.policy = SourceRefreshConfig.model_validate(policy.model_dump())
        self._clock = clock
        create = policy.create_if_missing and not policy.directory.exists()
        store = self._open(create=create)
        try:
            if create:
                store.db.executescript(
                    "CREATE TABLE metadata(id INTEGER PRIMARY KEY CHECK(id=1),recipe TEXT NOT NULL,"
                    "records INTEGER NOT NULL,bytes INTEGER NOT NULL,head TEXT);"
                    "CREATE TABLE versions(sequence INTEGER PRIMARY KEY,id TEXT UNIQUE NOT NULL,"
                    "partition TEXT NOT NULL,previous TEXT,captured REAL NOT NULL,"
                    "payload BLOB NOT NULL);"
                    "CREATE INDEX source_partition ON versions(partition,sequence);"
                )
                with store.transaction():
                    store.db.execute(
                        "INSERT INTO metadata VALUES(1,?,0,0,NULL)", (self.policy.identity,)
                    )
                store.seal_directory()
            store.db.execute("BEGIN")
            self._check(store)
        except SourceRefreshFailure:
            raise
        except (ValueError, sqlite3.Error, OSError):
            raise SourceRefreshFailure("source refresh store cannot be verified") from None
        finally:
            store.close()

    def now(self) -> float:
        value = self._clock()
        if not math.isfinite(value) or value < 0:
            raise SourceRefreshFailure("source refresh clock is invalid")
        return value

    def _open(self, *, create: bool = False) -> PrivateDatabase:
        try:
            return PrivateDatabase(
                self.policy.directory,
                "versions.sqlite",
                timeout=self.policy.database_timeout_seconds,
                create=create,
            )
        except (ValueError, sqlite3.Error, OSError):
            raise SourceRefreshFailure("source refresh storage is unavailable") from None

    def _check(self, store: PrivateDatabase) -> tuple[int, int, str | None, float | None]:
        store.check()
        row = store.db.execute(
            "SELECT recipe,records,bytes,head FROM metadata WHERE id=1"
        ).fetchone()
        recipe, expected_count, expected_bytes, expected_head = TypeAdapter(
            tuple[str, int, int, str | None]
        ).validate_python(row, strict=True)
        if recipe != self.policy.identity:
            raise SourceRefreshFailure("source refresh requires its original policy")
        count, total, maximum = TypeAdapter(tuple[int, int, int]).validate_python(
            store.db.execute(
                "SELECT COUNT(*),COALESCE(SUM(length(payload)),0),"
                "COALESCE(MAX(length(payload)),0) FROM versions"
            ).fetchone(),
            strict=True,
        )
        if (
            count != expected_count
            or total != expected_bytes
            or count > self.policy.max_versions
            or total > self.policy.max_store_bytes
            or maximum > self.policy.max_record_bytes
        ):
            raise SourceRefreshFailure("source refresh exceeds its declared capacity")
        rows = store.db.execute(
            "SELECT sequence,id,partition,previous,captured FROM versions ORDER BY sequence"
        )
        previous: str | None = None
        captured: float | None = None
        for expected_sequence, raw in enumerate(rows):
            sequence, identity, partition, parent, stamp = TypeAdapter(
                tuple[int, str, str, str | None, float]
            ).validate_python(raw, strict=True)
            if (
                sequence != expected_sequence
                or parent != previous
                or any(c not in "0123456789abcdef" for c in identity + partition)
                or len(identity) != 64
                or len(partition) != 64
                or not math.isfinite(stamp)
                or stamp < 0
                or (captured is not None and stamp < captured)
            ):
                raise SourceRefreshFailure("source refresh prefix changed")
            previous, captured = identity, stamp
        if previous != expected_head:
            raise SourceRefreshFailure("source refresh tail changed")
        return count, total, previous, captured

    def _decode(self, row: object) -> SourceRefreshRecord:
        sequence, identity, partition, parent, captured, data = TypeAdapter(
            tuple[int, str, str, str | None, float, bytes]
        ).validate_python(row, strict=True)
        if len(data) > self.policy.max_record_bytes:
            raise SourceRefreshFailure("source refresh original exceeds its size allowance")
        record = SourceRefreshRecord.model_validate_json(data)
        if (
            len(data) > self.policy.max_record_bytes
            or record.sequence != sequence
            or record.previous_sha256 != parent
            or record.captured_at != captured
            or record.identity != identity
            or record.key.identity != partition
            or record.key.url not in self.policy.urls
        ):
            raise SourceRefreshFailure("source refresh original changed")
        return record

    def _operation(
        self, action: Callable[[PrivateDatabase], tuple[SourceRefreshRecord, ...]]
    ) -> tuple[SourceRefreshRecord, ...]:
        store = self._open()
        try:
            return action(store)
        except SourceRefreshFailure:
            raise
        except (ValueError, sqlite3.Error, OSError):
            raise SourceRefreshFailure("source refresh operation could not be verified") from None
        finally:
            store.close()

    async def versions(self) -> tuple[SourceRefreshRecord, ...]:
        def read(store: PrivateDatabase) -> tuple[SourceRefreshRecord, ...]:
            store.db.execute("BEGIN")
            self._check(store)
            return tuple(
                self._decode(row)
                for row in store.db.execute(
                    "SELECT sequence,id,partition,previous,captured,payload "
                    "FROM versions ORDER BY sequence"
                )
            )

        return await off_loop(lambda: self._operation(read))

    async def latest(self, key: SourceRefreshKey) -> SourceRefreshRecord | None:
        key = SourceRefreshKey.model_validate(key.model_dump())
        if key.url not in self.policy.urls:
            raise SourceRefreshFailure("URL is not a configured refresh target")

        def read(store: PrivateDatabase) -> tuple[SourceRefreshRecord, ...]:
            store.db.execute("BEGIN")
            _, _, _, captured = self._check(store)
            now = self.now()
            if captured is not None and now < captured:
                raise SourceRefreshFailure("source refresh clock moved backwards")
            row = store.db.execute(
                "SELECT sequence,id,partition,previous,captured,payload FROM versions "
                "WHERE partition=? ORDER BY sequence DESC LIMIT 1",
                (key.identity,),
            ).fetchone()
            if row is None:
                return ()
            record = self._decode(row)
            if record.key != key:
                raise SourceRefreshFailure("source refresh partition changed")
            return (
                (record,)
                if record.page is not None
                and now - record.captured_at <= self.policy.max_validator_age_seconds
                else ()
            )

        records = await off_loop(lambda: self._operation(read))
        return records[0] if records else None

    async def capture(self, key: SourceRefreshKey, page: Page) -> SourceRefreshRecord:
        return await self._append(key, page)

    async def invalidate(self, key: SourceRefreshKey) -> SourceRefreshRecord:
        """Persist a content-free tombstone; historical originals remain reachable."""
        return await self._append(key, None)

    async def _append(self, key: SourceRefreshKey, page: Page | None) -> SourceRefreshRecord:
        key, page = (
            SourceRefreshKey.model_validate(key.model_dump()),
            Page.model_validate(page.model_dump()) if page is not None else None,
        )
        if key.url not in self.policy.urls:
            raise SourceRefreshFailure("URL is not a configured refresh target")

        def persist(store: PrivateDatabase) -> tuple[SourceRefreshRecord, ...]:
            with store.transaction():
                count, total, previous, captured = self._check(store)
                now = self.now()
                if captured is not None and now < captured:
                    raise SourceRefreshFailure("source refresh clock moved backwards")
                record = SourceRefreshRecord(
                    schema="ghimera.source-refresh-record/1",
                    sequence=count,
                    previous_sha256=previous,
                    key=key,
                    captured_at=now,
                    page=page,
                )
                data = record.model_dump_json().encode()
                if (
                    count >= self.policy.max_versions
                    or len(data) > self.policy.max_record_bytes
                    or len(data) + total > self.policy.max_store_bytes
                ):
                    raise SourceRefreshFailure("source refresh capacity exhausted")
                store.db.execute(
                    "INSERT INTO versions VALUES(?,?,?,?,?,?)",
                    (record.sequence, record.identity, key.identity, previous, now, data),
                )
                store.db.execute(
                    "UPDATE metadata SET records=?,bytes=?,head=? WHERE id=1",
                    (count + 1, total + len(data), record.identity),
                )
            return (record,)

        (record,) = await off_loop(lambda: self._operation(persist))
        return record
