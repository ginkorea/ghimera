"""Capture originals before processing, beside the existing run journal.

This boundary never refetches, invokes a model, or replays graph side effects.
An unacknowledged fetch/processing operation remains explicit for reconciliation;
the native journal remains the authority for actual recorded spend. Ledger
indices are high-water marks, not attribution of interleaved work to one source.
"""

import argparse
import hashlib
import sqlite3
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from ghimera.config import GhimeraConfig
from ghimera.journal import _run_path, read_journal
from ghimera.journal_types import JournalHeader, digest
from ghimera.models import Document, Goal, ModelIdentity, Page
from ghimera.private_database import PrivateDatabase
from ghimera.refusals import GhimeraRefused
from ghimera.source_work_types import SourceOperation, SourceRequest, SourceWorkReport


class SourceWorkFailure(RuntimeError):
    """Fatal persistence failure; never turn this into an ordinary source refusal."""


@dataclass(frozen=True)
class SourceWorkToken:
    operation_id: str
    journal_header_sha256: str


def _identity(request: SourceRequest) -> str:
    return hashlib.sha256(request.model_dump_json().encode()).hexdigest()


class SourceWorkStore:
    """One run owner; original and result transitions are committed before ack."""

    def __init__(self, config: GhimeraConfig, run_id: str, *, create: bool) -> None:
        if config.source_work is None or config.journal is None:
            raise ValueError("source work requires its declared policy and native journal")
        self._config, self._policy = config, config.source_work
        self._run_id = run_id
        report = read_journal(config.journal, run_id)
        if report.header.config != config:
            raise ValueError("source work requires the exact original run recipe")
        self._header = digest(report.header)
        self._private = PrivateDatabase(
            _run_path(config.journal, run_id) / "source-work",
            "operations.sqlite",
            timeout=self._policy.database_timeout_seconds,
            create=create,
        )
        self._owner = ExitStack()
        self._writing = False
        self._operations: dict[str, SourceOperation] = {}
        self._reservation = 0
        try:
            if create:
                self._owner.enter_context(self._private.writer())
                self._writing = True
                self._private.db.executescript(
                    "CREATE TABLE binding(id INTEGER PRIMARY KEY CHECK(id=1),"
                    " schema TEXT NOT NULL, header TEXT NOT NULL,"
                    " operation_count INTEGER NOT NULL);"
                    "CREATE TABLE operations(id TEXT PRIMARY KEY, sequence INTEGER UNIQUE NOT NULL,"
                    " payload BLOB NOT NULL, sha256 TEXT NOT NULL);"
                )
                self._private.db.execute(
                    "INSERT INTO binding VALUES(1,?,?,0)",
                    ("ghimera.source-work-store/1", self._header),
                )
                self._private.db.commit()
                self._private.seal_directory()
            else:
                row = self._private.db.execute(
                    "SELECT schema,header FROM binding WHERE id=1"
                ).fetchone()
                if row != ("ghimera.source-work-store/1", self._header):
                    raise ValueError("source store does not belong to the recorded run")
                operations = self._read_operations()
                self._operations = {item.operation_id: item for item in operations}
                self._reservation = sum(self._reserved(item) for item in operations)
        except BaseException:
            self.close()
            raise

    @classmethod
    def open(
        cls, config: GhimeraConfig, run_id: str, goal: Goal, judge: ModelIdentity
    ) -> "SourceWorkStore":
        if config.journal is None:
            raise ValueError("source work requires the native journal")
        journal = read_journal(config.journal, run_id)
        if journal.state != "unsealed" or journal.incomplete_tail:
            raise ValueError("source-work creation requires a clean, unsealed run journal")
        header = journal.header
        expected = JournalHeader(
            schema="chimera.run-journal-header/1",
            run_id=run_id,
            goal=goal,
            config=config,
            judge=judge,
        )
        if header != expected:
            raise ValueError("source work is bound to its original goal and judge")
        return cls(config, run_id, create=True)

    def close(self) -> None:
        self._owner.close()
        self._writing = False
        self._private.close()

    @classmethod
    def resume(cls, config: GhimeraConfig, run_id: str, ledger_rows: int) -> "SourceWorkStore":
        """Adopt only a quiescent checkpoint, never guess at interrupted work."""
        store = cls(config, run_id, create=False)
        try:
            store._owner.enter_context(store._private.writer())
            store._writing = True
            if config.journal is None:
                raise ValueError("source work requires the native journal")
            journal = read_journal(config.journal, run_id)
            if journal.state != "unsealed" or journal.incomplete_tail:
                raise ValueError("only a clean, unsealed run can resume source work")
            if ledger_rows != len(journal.rows):
                raise ValueError("source-work continuation needs the exact native journal prefix")
            if any(
                item.ledger_end is None or item.ledger_end > ledger_rows
                for item in store._operations.values()
            ):
                raise ValueError(
                    "source work after the checkpoint requires explicit reconciliation"
                )
            return store
        except BaseException:
            store.close()
            raise

    def _read_operations(self) -> tuple[SourceOperation, ...]:
        self._private.check()
        count = self._private.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
        acknowledged = self._private.db.execute(
            "SELECT operation_count FROM binding WHERE id=1"
        ).fetchone()
        if acknowledged != (count,):
            raise ValueError("an acknowledged source operation is missing")
        if count > self._policy.max_operations:
            raise ValueError("source store exceeds its declared operation capacity")
        limit = self._policy.max_operation_bytes
        oversized = self._private.db.execute(
            "SELECT 1 FROM operations WHERE length(payload)>? LIMIT 1", (limit,)
        ).fetchone()
        if oversized is not None:
            raise ValueError("source operation exceeds its configured byte bound")
        values = self._private.db.execute(
            "SELECT id,sequence,payload,sha256 FROM operations ORDER BY sequence"
        )
        operations: list[SourceOperation] = []
        total = 0
        for operation_id, sequence, payload, pin in values:
            if not isinstance(payload, bytes) or len(payload) > limit:
                raise ValueError("source operation exceeds its configured byte bound")
            if hashlib.sha256(payload).hexdigest() != pin:
                raise ValueError("source operation changed after acknowledgement")
            item = SourceOperation.model_validate_json(payload)
            if (
                item.operation_id != operation_id
                or item.sequence != sequence
                or sequence != len(operations)
                or operation_id != _identity(item.request)
            ):
                raise ValueError("source operation identity or order changed")
            self._sizes(item)
            total += self._reserved(item)
            operations.append(item)
        if total > self._policy.max_store_bytes:
            raise ValueError("source store exceeds its configured byte capacity")
        return tuple(operations)

    def _sizes(self, item: SourceOperation) -> None:
        if (
            item.page is not None
            and len(item.page.model_dump_json().encode()) > self._policy.max_page_bytes
        ):
            raise ValueError("original page exceeds its declared acquisition bound")
        if (
            item.result is not None
            and len(item.result.model_dump_json().encode()) > self._policy.max_result_bytes
        ):
            raise ValueError("accepted document exceeds its declared result bound")
        if item.result is not None:
            item.result.validate_policy(self._config)

    def _reserved(self, item: SourceOperation) -> int:
        # Include the whole serialized envelope in actual storage. Active work
        # reserves both declared byte bounds before any source request.
        if item.state in {"fetching", "acquired", "processing"}:
            return self._policy.max_operation_bytes
        return len(item.model_dump_json().encode())

    def _write(self, item: SourceOperation, *, new: bool) -> None:
        if not self._writing:
            raise SourceWorkFailure("source-work writer is not owned by this session")
        try:
            with self._private.transaction():
                existing = self._operations
                if new and len(existing) >= self._policy.max_operations:
                    raise ValueError("source-work operation capacity exhausted before acquisition")
                self._sizes(item)
                previous = existing.get(item.operation_id)
                if (previous is None) != new:
                    raise ValueError("source operation already exists or is missing")
                size = (
                    self._reservation
                    + self._reserved(item)
                    - (self._reserved(previous) if previous is not None else 0)
                )
                if size > self._policy.max_store_bytes:
                    raise ValueError("source-work byte capacity exhausted")
                payload = item.model_dump_json().encode()
                if len(payload) > self._policy.max_operation_bytes:
                    raise ValueError("source operation exceeds its declared envelope bound")
                self._private.db.execute(
                    "INSERT INTO operations VALUES(?,?,?,?)"
                    if new
                    else "UPDATE operations SET sequence=?,payload=?,sha256=? WHERE id=?",
                    (item.operation_id, item.sequence, payload, hashlib.sha256(payload).hexdigest())
                    if new
                    else (
                        item.sequence,
                        payload,
                        hashlib.sha256(payload).hexdigest(),
                        item.operation_id,
                    ),
                )
                if new:
                    self._private.db.execute(
                        "UPDATE binding SET operation_count=operation_count+1 WHERE id=1"
                    )
            self._operations[item.operation_id] = item
            self._reservation = size
        except (OSError, ValueError, sqlite3.Error, GhimeraRefused) as exc:
            raise SourceWorkFailure("source work was not durably acknowledged") from exc

    def begin(self, request: SourceRequest, ledger_start: int) -> SourceWorkToken:
        request = SourceRequest.model_validate(request.model_dump())
        item = SourceOperation(
            schema="ghimera.source-operation/1",
            operation_id=_identity(request),
            sequence=len(self._operations),
            request=request,
            state="fetching",
            ledger_start=ledger_start,
            ledger_end=None,
            started_at=time.time(),
            acquired_at=None,
            finished_at=None,
            page=None,
            result=None,
            reason=None,
        )
        self._write(item, new=True)
        return SourceWorkToken(item.operation_id, self._header)

    def _get(self, token: SourceWorkToken) -> SourceOperation:
        self._private.check()
        if token.journal_header_sha256 != self._header:
            raise SourceWorkFailure("source-work token belongs to another run")
        try:
            return self._operations[token.operation_id]
        except KeyError:
            raise SourceWorkFailure("source-work identity is missing") from None

    def acquired(self, token: SourceWorkToken, page: Page) -> None:
        item = self._get(token)
        if item.state != "fetching":
            raise SourceWorkFailure("source original was already acknowledged")
        updated = SourceOperation.model_validate(
            dict(item.model_dump(), state="acquired", page=page, acquired_at=time.time())
        )
        self._write(updated, new=False)

    def processing(self, token: SourceWorkToken) -> None:
        item = self._get(token)
        if item.state != "acquired":
            raise SourceWorkFailure("processing requires a newly acknowledged original")
        self._write(
            SourceOperation.model_validate(dict(item.model_dump(), state="processing")), new=False
        )

    def processed(self, token: SourceWorkToken, result: Document | None, ledger_end: int) -> None:
        item = self._get(token)
        if item.state != "processing":
            raise SourceWorkFailure("source processing was not started")
        updated = SourceOperation.model_validate(
            dict(
                item.model_dump(),
                state="processed",
                result=result,
                ledger_end=ledger_end,
                finished_at=time.time(),
            )
        )
        self._write(updated, new=False)

    def refused(
        self, token: SourceWorkToken, reason: str, ledger_end: int, *, cancelled: bool = False
    ) -> None:
        item = self._get(token)
        if item.state not in {"fetching", "acquired", "processing"}:
            raise SourceWorkFailure("terminal source work cannot be rewritten")
        updated = SourceOperation.model_validate(
            dict(
                item.model_dump(),
                state="cancelled" if cancelled else "refused",
                reason=reason,
                ledger_end=ledger_end,
                finished_at=time.time(),
            )
        )
        self._write(updated, new=False)

    def report(self) -> SourceWorkReport:
        with ExitStack() as inspection:
            if self._writing:
                active = True
            else:
                try:
                    inspection.enter_context(self._private.writer())
                    active = False
                except BlockingIOError:
                    active = True
            return self._report(active)

    def assert_quiescent(self) -> None:
        if any(item.ledger_end is None for item in self._operations.values()):
            raise SourceWorkFailure("unacknowledged source work cannot be checkpointed or sealed")

    def _report(self, active: bool) -> SourceWorkReport:
        operations = self._read_operations()
        if self._config.journal is None:
            raise ValueError("source work requires its native journal")
        journal = read_journal(self._config.journal, self._run_id)
        if any(
            item.ledger_start > len(journal.rows)
            or (item.ledger_end is not None and item.ledger_end > len(journal.rows))
            for item in operations
        ):
            raise ValueError("source work acknowledges observations absent from its journal")
        return SourceWorkReport(
            schema="ghimera.source-work-report/1",
            run_id=self._run_id,
            journal_header_sha256=self._header,
            writer_active=active,
            operations=operations,
        )


def read_source_work(config: GhimeraConfig, run_id: str) -> SourceWorkReport:
    """Read acknowledged originals/results without source, model or graph contact."""
    store = SourceWorkStore(config, run_id, create=False)
    try:
        return store.report()
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect durable source work without replaying it."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    arguments = parser.parse_args()
    try:
        report = read_source_work(GhimeraConfig.from_toml(arguments.config), arguments.run_id)
        sys.stdout.write(
            f"run_id={report.run_id} operations={len(report.operations)} "
            f"writer_active={report.writer_active} unresolved={len(report.unresolved)}\n"
        )
        return 0
    except (OSError, ValueError, sqlite3.Error, SourceWorkFailure, GhimeraRefused):
        sys.stderr.write("Source-work inspection refused; preserve the run and inspect storage.\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
