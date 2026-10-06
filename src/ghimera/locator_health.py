"""Durable completion-ordered health, never a global locator registry."""

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ghimera.extraction_config import ExtractionConfig, LocatorProfile
from ghimera.extraction_types import LocatorEvent
from ghimera.locator_types import LocatorDriftPolicy, LocatorHealth
from ghimera.passive_worker import private_directory
from ghimera.refusals import GhimeraRefused, RefusalCode


class LocatorHealthStore:
    """Short transactions serialize observations, not HTTP or parsing work.

    A drifted profile stays in generic mode until explicitly reset or changed.
    In-flight attempts may complete after the latch; they never silently reset
    it. Worker errors without any selector observations are not CSS misses.
    """

    def __init__(self, directory: Path, policy: LocatorDriftPolicy) -> None:
        self._directory = directory
        self._path = directory / "locator-health-v1.sqlite"
        self._policy = policy
        self._policy_digest = hashlib.sha256(policy.model_dump_json().encode()).hexdigest()

    def _empty(self, profile: LocatorProfile) -> LocatorHealth:
        return LocatorHealth(
            schema="chimera.locator-health/1",
            host=profile.host,
            profile_id=profile.profile_id,
            profile_digest=hashlib.sha256(profile.model_dump_json().encode()).hexdigest(),
            policy_digest=self._policy_digest,
            consecutive_misses=0,
            completed_observations=0,
            miss_limit=self._policy.consecutive_miss_limit,
            generic_only=False,
        )

    def _key(self, health: LocatorHealth) -> str:
        return f"{health.profile_digest}:{health.policy_digest}"

    def _validate_file(self) -> None:
        info = self._path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    @contextmanager
    def _connection(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        private_directory(self._directory)
        if write:
            try:
                descriptor = os.open(
                    self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        self._validate_file()
        connection = sqlite3.connect(
            self._path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
            uri=True,
            timeout=self._policy.state_timeout_seconds,
            isolation_level=None,
        )
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            version_row = connection.execute("PRAGMA user_version").fetchone()
            version: object = version_row[0] if version_row is not None else None
            if (
                write
                and version == 0
                and not connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ):
                connection.execute("CREATE TABLE health (identity TEXT PRIMARY KEY, value TEXT)")
                connection.execute("PRAGMA user_version=1")
            elif version != 1:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            yield connection
            if write:
                connection.execute("COMMIT")
        except (OSError, sqlite3.Error, ValueError):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        finally:
            connection.close()

    def _load(self, connection: sqlite3.Connection, empty: LocatorHealth) -> LocatorHealth:
        row = connection.execute(
            "SELECT value FROM health WHERE identity=?", (self._key(empty),)
        ).fetchone()
        if row is None:
            return empty
        payload: object = row[0]
        if not isinstance(payload, str):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        health = LocatorHealth.model_validate_json(payload)
        if (
            health.host != empty.host
            or health.profile_id != empty.profile_id
            or self._key(health) != self._key(empty)
            or health.miss_limit != empty.miss_limit
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        return health

    def read(self, profile: LocatorProfile) -> LocatorHealth:
        empty = self._empty(profile)
        if not self._path.exists() and not self._path.is_symlink():
            return empty
        with self._connection(write=False) as connection:
            return self._load(connection, empty)

    def observe(
        self,
        profile: LocatorProfile,
        source_sha256: str,
        events: tuple[LocatorEvent, ...],
        *,
        completed: bool = True,
    ) -> LocatorHealth:
        empty = self._empty(profile)
        selectors = {
            "body": profile.body,
            "title": profile.title,
            "byline": profile.byline,
            "date": profile.date,
        }
        if len({event.field for event in events}) != len(events) or any(
            event.selector != selectors[event.field] for event in events
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if not events or (not completed and not any(e.status == "missing" for e in events)):
            return self.read(profile)
        with self._connection(write=True) as connection:
            old = self._load(connection, empty)
            if old.generic_only:
                return old
            misses = old.consecutive_misses + 1 if any(e.status == "missing" for e in events) else 0
            data = old.model_dump(by_alias=True)
            data.update(
                consecutive_misses=misses,
                completed_observations=old.completed_observations + 1,
                generic_only=misses >= old.miss_limit,
                last_source_sha256=source_sha256,
            )
            updated = LocatorHealth.model_validate(data)
            connection.execute(
                "INSERT INTO health(identity,value) VALUES(?,?) "
                "ON CONFLICT(identity) DO UPDATE SET value=excluded.value",
                (self._key(updated), updated.model_dump_json()),
            )
            return updated

    def reset(self, profile: LocatorProfile) -> LocatorHealth:
        empty = self._empty(profile)
        if not self._path.exists() and not self._path.is_symlink():
            return empty
        with self._connection(write=True) as connection:
            self._load(connection, empty)
            connection.execute("DELETE FROM health WHERE identity=?", (self._key(empty),))
        return empty


def _cmd_doctor() -> int:
    parser = argparse.ArgumentParser(description="Inspect configured publisher locator health")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    with args.config.open("rb") as stream:
        config = ExtractionConfig.model_validate(tomllib.load(stream))
    if config.locator_drift is None:
        parser.error("this extraction configuration has no locator_drift policy")
    store = LocatorHealthStore(config.locator_directory, config.locator_drift)
    rows = [store.read(profile) for profile in config.profiles]
    print(
        json.dumps(
            [dict(row.model_dump(mode="json", by_alias=True), finding=row.finding) for row in rows]
        )
    )
    return 1 if any(row.generic_only for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(_cmd_doctor())
