"""Single-writer owner-private JSONL journal, fsynced before observation ack.

No recovery refetch, source/model call, signature or multi-process database.
The reader retains and identifies a torn final line of an unsealed run; it never
repairs files. Existing run identities cannot be overwritten or reused.
"""

import argparse
import fcntl
import hashlib
import os
import re
import stat
import sys
import tempfile
import threading
from pathlib import Path

from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.journal_config import JournalConfig
from ghimera.journal_types import (
    JournalDocument,
    JournalEntry,
    JournalHeader,
    JournalReport,
    JournalRetainedDocument,
    JournalSummary,
    canonical,
    digest,
)
from ghimera.models import Goal, Harvest, LedgerRow, ModelIdentity, count_fetch_attempts
from ghimera.refusals import GhimeraRefused, RefusalCode


def _refuse() -> GhimeraRefused:
    return GhimeraRefused(RefusalCode.LEDGER_SINK_FAILED)


def _run_path(policy: JournalConfig, run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", run_id):
        raise _refuse()
    path = policy.directory / run_id
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise _refuse()
    return path


def _private_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise _refuse()


def _checked_file(path: Path, flags: int, limit: int) -> int:
    fd = os.open(path, flags | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > limit
        ):
            raise _refuse()
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_file(path: Path, limit: int) -> bytes:
    with os.fdopen(_checked_file(path, os.O_RDONLY, limit), "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise _refuse()
    return data


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise OSError("journal write made no progress")
        offset += written
    os.fsync(fd)


def _directory_sync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _immutable_file(path: Path, data: bytes, limit: int) -> None:
    if len(data) > limit:
        raise _refuse()
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    staging = Path(name)
    try:
        try:
            _write_all(fd, data)
        finally:
            os.close(fd)
        os.link(staging, path)
    finally:
        staging.unlink(missing_ok=True)
    _directory_sync(path.parent)


class DirectoryLedgerSink:
    def __init__(
        self,
        config: GhimeraConfig,
        run_id: str,
        goal: Goal,
        judge: ModelIdentity,
        *,
        resume_rows: tuple[LedgerRow, ...] | None = None,
    ) -> None:
        if config.journal is None:
            raise _refuse()
        self._policy = config.journal
        self._path = _run_path(self._policy, run_id)
        self._header = JournalHeader(
            schema="chimera.run-journal-header/1",
            run_id=run_id,
            goal=goal,
            config=config,
            judge=judge,
        )
        self._previous = digest(self._header)
        self._rows: list[LedgerRow] = []
        self._size = 0
        self._closed = False
        self._failed = False
        self._lock = threading.Lock()
        self._lease_fd = -1
        try:
            # Validate the first serialized record before creating any run.
            header = canonical(self._header)
            if len(header) > self._policy.max_record_bytes:
                raise _refuse()
            if resume_rows is not None:
                _private_directory(self._policy.directory)
                _private_directory(self._path)
                self._lease_fd = _checked_file(
                    self._path / "ledger.jsonl",
                    os.O_WRONLY | os.O_APPEND,
                    self._policy.max_journal_bytes,
                )
                fcntl.flock(self._lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                report = read_journal(self._policy, run_id)
                if (
                    report.state != "unsealed"
                    or report.incomplete_tail
                    or report.header != self._header
                    or report.rows != resume_rows
                ):
                    raise _refuse()
                self._rows = list(resume_rows)
                for row in resume_rows:
                    entry = JournalEntry(
                        schema="chimera.run-journal-entry/1",
                        previous_sha256=self._previous,
                        row=row,
                    )
                    self._previous = digest(entry)
                self._size = os.fstat(self._lease_fd).st_size
                return
            self._policy.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            _private_directory(self._policy.directory)
            self._path.mkdir(mode=0o700, exist_ok=False)
            _directory_sync(self._policy.directory)
            _immutable_file(self._path / "header.json", header, self._policy.max_record_bytes)
            self._lease_fd = os.open(
                self._path / "ledger.jsonl",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            fcntl.flock(self._lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fsync(self._lease_fd)
            _directory_sync(self._path)
        except (OSError, GhimeraRefused):
            self._release()
            raise _refuse() from None

    def _release(self) -> None:
        if self._lease_fd >= 0:
            os.close(self._lease_fd)
            self._lease_fd = -1

    def _active(self) -> None:
        if self._closed or self._failed:
            raise _refuse()
        if _run_path(self._policy, self._header.run_id) != self._path:
            raise _refuse()
        _private_directory(self._policy.directory)
        _private_directory(self._path)
        held = os.fstat(self._lease_fd)
        named = (self._path / "ledger.jsonl").lstat()
        if (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
            raise _refuse()

    @property
    def effective_config(self) -> GhimeraConfig:
        """The native durable sink binds its original header, not caller metadata."""
        return self._header.config

    @property
    def committed_rows(self) -> tuple[LedgerRow, ...]:
        with self._lock:
            self._active()
            return tuple(self._rows)

    def append(self, row: LedgerRow) -> None:
        with self._lock:
            try:
                self._active()
                if row.sequence != len(self._rows) or len(self._rows) >= self._policy.max_records:
                    raise _refuse()
                entry = JournalEntry(
                    schema="chimera.run-journal-entry/1", previous_sha256=self._previous, row=row
                )
                data = canonical(entry) + b"\n"
                if (
                    len(data) > self._policy.max_record_bytes
                    or self._size + len(data) > self._policy.max_journal_bytes
                ):
                    raise _refuse()
                fd = _checked_file(
                    self._path / "ledger.jsonl",
                    os.O_WRONLY | os.O_APPEND,
                    self._policy.max_journal_bytes,
                )
                try:
                    if os.fstat(fd).st_size != self._size:
                        raise _refuse()
                    _write_all(fd, data)
                finally:
                    os.close(fd)
                self._size += len(data)
                self._previous = digest(entry)
                self._rows.append(row)
            except (OSError, GhimeraRefused):
                self._failed = True
                raise _refuse() from None

    def finish(self, harvest: Harvest) -> None:
        with self._lock:
            try:
                self._active()
                if (
                    harvest.ledger != tuple(self._rows)
                    or harvest.goal != self._header.goal
                    or harvest.receipt.effective_config != self._header.config
                    or harvest.receipt.judge != self._header.judge
                ):
                    raise _refuse()
                # Verify persisted observations, not only our volatile copy.
                report = read_journal(self._policy, self._header.run_id)
                if (
                    report.state != "unsealed"
                    or report.incomplete_tail
                    or report.rows != harvest.ledger
                ):
                    raise _refuse()
                summary = JournalSummary(
                    schema="chimera.run-journal-summary/2"
                    if harvest.retained_sources
                    else "chimera.run-journal-summary/1",
                    run_id=self._header.run_id,
                    header_sha256=digest(self._header),
                    last_entry_sha256=self._previous,
                    ledger_rows=len(self._rows),
                    receipt=harvest.receipt,
                    documents=tuple(
                        JournalDocument(
                            url=doc.url,
                            sha256=doc.sha256,
                            native_text_sha256=hashlib.sha256(
                                doc.extracted.text.encode()
                            ).hexdigest(),
                            raw_bytes=len(doc.raw),
                        )
                        for doc in harvest.documents
                    ),
                    retained_documents=tuple(
                        JournalRetainedDocument(
                            url=item.document.url,
                            sha256=item.document.sha256,
                            native_text_sha256=hashlib.sha256(
                                item.document.extracted.text.encode()
                            ).hexdigest(),
                            raw_bytes=len(item.document.raw),
                            origin=item.origin,
                        )
                        for item in harvest.retained_sources
                    ),
                )
                _immutable_file(
                    self._path / "summary.json", canonical(summary), self._policy.max_summary_bytes
                )
                self._closed = True
                self._release()
            except (OSError, GhimeraRefused):
                self._failed = True
                raise _refuse() from None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._release()


def read_journal(policy: JournalConfig, run_id: str) -> JournalReport:
    """Read a bounded committed prefix without changing storage or doing I/O to sources."""
    try:
        path = _run_path(policy, run_id)
        _private_directory(policy.directory)
        _private_directory(path)
        header = JournalHeader.model_validate_json(
            _read_file(path / "header.json", policy.max_record_bytes)
        )
        if header.run_id != run_id or header.config.journal != policy:
            raise _refuse()
        previous = digest(header)
        rows: list[LedgerRow] = []
        total = 0
        incomplete = False
        with os.fdopen(
            _checked_file(path / "ledger.jsonl", os.O_RDONLY, policy.max_journal_bytes), "rb"
        ) as stream:
            while data := stream.readline(policy.max_record_bytes + 1):
                total += len(data)
                if len(data) > policy.max_record_bytes or total > policy.max_journal_bytes:
                    raise _refuse()
                if not data.endswith(b"\n"):
                    incomplete = True
                    break
                entry = JournalEntry.model_validate_json(data)
                if (
                    len(rows) >= policy.max_records
                    or entry.previous_sha256 != previous
                    or entry.row.sequence != len(rows)
                ):
                    raise _refuse()
                rows.append(entry.row)
                previous = digest(entry)
        summary = None
        inputs = tuple(row for row in rows if row.event == "local_input")
        input_policy = header.config.local_inputs
        if inputs and (
            input_policy is None
            or len(inputs) > input_policy.max_files_per_run
            or sum(row.bytes_read for row in inputs) > input_policy.max_total_bytes
        ):
            raise _refuse()
        for row in inputs:
            if row.local_input is not None:
                row.local_input.validate_policy(input_policy)
        from ghimera.graph_planning import validate_rows as validate_planning_rows
        from ghimera.semantic_graph import validate_rows

        validate_rows(header.config, tuple(rows))
        validate_planning_rows(header.config, tuple(rows))
        from ghimera.identity_automation import validate_identity_rows

        validate_identity_rows(header.config, tuple(rows))
        from ghimera.judgment_validation import validate_judgment_rows

        validate_judgment_rows(header.config, header.goal.text, tuple(rows))
        summary_path = path / "summary.json"
        if summary_path.exists() or summary_path.is_symlink():
            summary = JournalSummary.model_validate_json(
                _read_file(summary_path, policy.max_summary_bytes)
            )
            receipt = summary.receipt
            retained = tuple(row for row in rows if row.retained_source is not None)
            if len(retained) != len(summary.retained_documents) or tuple(
                (row.url, row.retained_source) for row in retained
            ) != tuple((item.url, item.origin) for item in summary.retained_documents):
                raise _refuse()
            encoding = tuple(row.encoding_call for row in rows if row.encoding_call is not None)
            if (
                incomplete
                or summary.run_id != run_id
                or summary.header_sha256 != digest(header)
                or summary.last_entry_sha256 != previous
                or summary.ledger_rows != len(rows)
                or receipt.effective_config != header.config
                or receipt.judge != header.judge
                or receipt.fetches != count_fetch_attempts(header.config, tuple(rows))
                or receipt.bytes_read != sum(row.bytes_read for row in rows)
                or receipt.encoding_calls != len(encoding)
                or receipt.encoding_chars != sum(call.input_chars for call in encoding)
                or receipt.judge_calls
                != (
                    sum(row.model_intent is not None for row in rows)
                    if header.config.model_work is not None
                    else sum(
                        row.event
                        in {
                            "verdict",
                            "grade",
                            "plan",
                            "assessment",
                            "answer",
                            "review",
                            "semantic",
                            "semantic_review",
                            "identity_propose",
                            "identity_review",
                            "transcription_model",
                        }
                        for row in rows
                    )
                )
                or not rows
                or rows[-1].event != "stop"
                or rows[-1].reason != receipt.stop_reason
            ):
                raise _refuse()
        return JournalReport(
            schema="chimera.run-journal-report/1",
            state="complete" if summary is not None else "unsealed",
            header=header,
            rows=tuple(rows),
            incomplete_tail=incomplete,
            summary=summary,
        )
    except (OSError, ValueError, ValidationError):
        raise _refuse() from None


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a run journal without resuming it.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    arguments = parser.parse_args()
    try:
        config = GhimeraConfig.from_toml(arguments.config)
        if config.journal is None:
            raise _refuse()
        report = read_journal(config.journal, arguments.run_id)
        # Deliberately do not print full source bodies, model context, or effective endpoints.
        summary = report.summary
        sys.stdout.write(
            f"run_id={report.header.run_id} state={report.state} rows={len(report.rows)} "
            f"incomplete_tail={str(report.incomplete_tail).lower()} "
            f"stop_reason={summary.receipt.stop_reason if summary else 'unsealed'}\n"
        )
        return 0 if report.state == "complete" else 1
    except (GhimeraRefused, OSError, ValueError):
        sys.stderr.write(f"{RefusalCode.LEDGER_SINK_FAILED.value}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
