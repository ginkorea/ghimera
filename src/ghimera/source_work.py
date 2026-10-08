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
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from ghimera.config import GhimeraConfig
from ghimera.journal import _run_path, read_journal
from ghimera.journal_types import JournalHeader, digest
from ghimera.model_reconciliation import unreconciled_model_sequences
from ghimera.model_work import validate_model_rows
from ghimera.models import Document, Goal, ModelIdentity, Page, RetainedOriginal
from ghimera.private_database import PrivateDatabase
from ghimera.refusals import GhimeraRefused
from ghimera.research_recovery_types import ResearchRecoveryModels
from ghimera.research_types import ResearchRequest
from ghimera.retained_graph import validate_original
from ghimera.session_state import SessionState
from ghimera.source_acquisition import SourceAcquisitionRead, SourceAcquisitionSnapshot
from ghimera.source_completion import (
    SourceCompletionRead,
    SourceCompletionRuntime,
    SourceCompletionSnapshot,
)
from ghimera.source_frontier import SourceFrontier
from ghimera.source_work_types import (
    LocalSourceRequest,
    Operation,
    RetainedSourceOperation,
    RetainedSourceRequest,
    SourceCoordinates,
    SourceOperation,
    SourceRequest,
    SourceWorkReport,
)


class SourceWorkFailure(RuntimeError):
    """Fatal persistence failure; never turn this into an ordinary source refusal."""


@dataclass(frozen=True)
class SourceWorkToken:
    operation_id: str
    journal_header_sha256: str


def _identity(request: SourceRequest | LocalSourceRequest | RetainedSourceRequest) -> str:
    return request.identity


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
        self._decoder: TypeAdapter[Operation] = TypeAdapter(Operation)
        self._private = PrivateDatabase(
            _run_path(config.journal, run_id) / "source-work",
            "operations.sqlite",
            timeout=self._policy.database_timeout_seconds,
            create=create,
        )
        self._owner = ExitStack()
        self._writing = False
        self._operations: dict[str, Operation] = {}
        self._reservation = 0
        self._frontier: SourceFrontier | None = None
        recovery = config.research_recovery
        self._completion = recovery.source_completion if recovery is not None else None
        self._acquisition = recovery.source_acquisition if recovery is not None else None
        self._acquisition_bytes = 0
        self._completion_bytes = 0
        schema = (
            "ghimera.source-work-store/3"
            if self._acquisition is not None
            else "ghimera.source-work-store/2"
            if self._completion is not None
            else "ghimera.source-work-store/1"
        )
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
                    (schema, self._header),
                )
                if self._completion is not None:
                    self._private.db.execute(
                        "CREATE TABLE source_completion(id INTEGER PRIMARY KEY CHECK(id=1),"
                        " operation_id TEXT NOT NULL, operation_sha256 TEXT NOT NULL,"
                        " payload BLOB NOT NULL, sha256 TEXT NOT NULL)"
                    )
                if self._acquisition is not None:
                    self._private.db.execute(
                        "CREATE TABLE source_acquisition(id INTEGER PRIMARY KEY CHECK(id=1),"
                        " payload BLOB NOT NULL, sha256 TEXT NOT NULL)"
                    )
                self._private.db.commit()
            else:
                row = self._private.db.execute(
                    "SELECT schema,header FROM binding WHERE id=1"
                ).fetchone()
                if row != (schema, self._header):
                    raise ValueError("source store does not belong to the recorded run")
                operations = self._read_operations()
                self._operations = {item.operation_id: item for item in operations}
                self._reservation = sum(self._reserved(item) for item in operations)
                if self._completion is not None:
                    row = self._private.db.execute(
                        "SELECT length(payload) FROM source_completion WHERE id=1"
                    ).fetchone()
                    self._completion_bytes = row[0] if row is not None else 0
                    if self._completion_bytes > self._completion.max_capsule_bytes:
                        raise ValueError("source control exceeds its declared byte bound")
                if self._acquisition is not None:
                    row = self._private.db.execute(
                        "SELECT length(payload) FROM source_acquisition WHERE id=1"
                    ).fetchone()
                    self._acquisition_bytes = row[0] if row is not None else 0
                    if self._acquisition_bytes > self._acquisition.max_capsule_bytes:
                        raise ValueError("acquisition control exceeds its declared byte bound")
            if self._policy.frontier is not None:
                self._frontier = SourceFrontier(
                    self._private,
                    self._policy.frontier,
                    create=create,
                    input_policy=self._config.local_inputs,
                )
                if (
                    self._reservation
                    + self._frontier.payload_bytes
                    + self._completion_bytes
                    + self._acquisition_bytes
                    > self._policy.max_store_bytes
                ):
                    raise ValueError("source work and frontier exceed their shared capacity")
            if create:
                self._private.seal_directory()
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
    def resume(
        cls,
        config: GhimeraConfig,
        run_id: str,
        ledger_rows: int,
        *,
        acquisition: SourceAcquisitionRead | None = None,
    ) -> "SourceWorkStore":
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
            if acquisition is not None:
                if store._read_acquisition(acquisition.sha256) != acquisition:
                    raise ValueError("acquisition changed before writer-owned restoration")
                return store
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

    def _read_operations(self) -> tuple[Operation, ...]:
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
        operations: list[Operation] = []
        total = 0
        for operation_id, sequence, payload, pin in values:
            if not isinstance(payload, bytes) or len(payload) > limit:
                raise ValueError("source operation exceeds its configured byte bound")
            if hashlib.sha256(payload).hexdigest() != pin:
                raise ValueError("source operation changed after acknowledgement")
            item = self._decoder.validate_json(payload)
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

    def _sizes(self, item: Operation) -> None:
        if isinstance(item, RetainedSourceOperation):
            validate_original(self._config, item.original)
            if len(item.original.model_dump_json().encode()) > self._policy.max_page_bytes:
                raise ValueError("retained original exceeds its declared acquisition bound")
            return
        if isinstance(item.request, LocalSourceRequest):
            item.request.validate_policy(self._config.local_inputs)
            if item.page is not None and item.page.local_input is not None:
                item.page.local_input.validate_policy(self._config.local_inputs)
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

    def _reserved(self, item: Operation) -> int:
        # Include the whole serialized envelope in actual storage. Active work
        # reserves both declared byte bounds before any source request.
        if item.state in {"fetching", "acquired", "processing"}:
            return self._policy.max_operation_bytes
        return len(item.model_dump_json().encode())

    def _write(
        self,
        item: Operation,
        *,
        new: bool,
        control: Callable[[], SourceCompletionSnapshot] | None = None,
        acquisition_control: Callable[[SourceOperation], SourceAcquisitionSnapshot] | None = None,
    ) -> None:
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
                if (
                    size + self._frontier_bytes + self._completion_bytes + self._acquisition_bytes
                    > self._policy.max_store_bytes
                ):
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
                completion_bytes = self._completion_bytes
                acquisition_bytes = self._acquisition_bytes
                if acquisition_control is not None:
                    if (
                        self._acquisition is None
                        or not isinstance(item, SourceOperation)
                        or item.state != "acquired"
                        or previous is None
                    ):
                        raise ValueError(
                            "acquisition control requires its exact newly acquired original"
                        )
                    self._operations[item.operation_id] = item
                    try:
                        snapshot_acquired = acquisition_control(item)
                        data_acquired = snapshot_acquired.model_dump_json().encode()
                        self._validate_acquisition(snapshot_acquired, item)
                    finally:
                        self._operations[item.operation_id] = previous
                    acquisition_bytes = len(data_acquired)
                    if (
                        acquisition_bytes > self._acquisition.max_capsule_bytes
                        or size + self._frontier_bytes + completion_bytes + acquisition_bytes
                        > self._policy.max_store_bytes
                    ):
                        raise ValueError("acquisition control exceeds original shared capacity")
                    self._private.db.execute(
                        "INSERT OR REPLACE INTO source_acquisition VALUES(1,?,?)",
                        (data_acquired, hashlib.sha256(data_acquired).hexdigest()),
                    )
                if control is not None:
                    if self._completion is None or previous is None or item.state != "processed":
                        raise ValueError(
                            "source control requires explicit policy and a completed original"
                        )
                    # Stage only the proved terminal state during this SAME transaction.
                    # The driver has naturally released its source operation and every
                    # other operation must be quiescent. Failure rolls back both rows.
                    self._operations[item.operation_id] = item
                    try:
                        self.assert_quiescent()
                        snapshot = control()
                        data = snapshot.model_dump_json().encode()
                        self._validate_completion(snapshot)
                    finally:
                        self._operations[item.operation_id] = previous
                    completion_bytes = len(data)
                    if (
                        completion_bytes > self._completion.max_capsule_bytes
                        or size + self._frontier_bytes + completion_bytes + acquisition_bytes
                        > self._policy.max_store_bytes
                    ):
                        raise ValueError(
                            "source completion exceeds configured shared storage capacity"
                        )
                    self._private.db.execute(
                        "INSERT OR REPLACE INTO source_completion VALUES(1,?,?,?,?)",
                        (
                            item.operation_id,
                            hashlib.sha256(payload).hexdigest(),
                            data,
                            hashlib.sha256(data).hexdigest(),
                        ),
                    )
            self._operations[item.operation_id] = item
            self._reservation = size
            self._completion_bytes = completion_bytes
            self._acquisition_bytes = acquisition_bytes
        except (OSError, ValueError, sqlite3.Error, GhimeraRefused) as exc:
            raise SourceWorkFailure("source work was not durably acknowledged") from exc

    @property
    def _frontier_bytes(self) -> int:
        return self._frontier.payload_bytes if self._frontier is not None else 0

    @property
    def _available_bytes(self) -> int:
        return (
            self._policy.max_store_bytes
            - self._reservation
            - self._frontier_bytes
            - self._completion_bytes
            - self._acquisition_bytes
        )

    def enqueue(self, request: SourceCoordinates, priority: float, ledger_start: int) -> None:
        if not self._writing:
            raise SourceWorkFailure("frontier writer is not owned by this session")
        if self._frontier is None:
            return
        if request.identity in self._operations:
            return
        try:
            self._frontier.enqueue(
                request,
                priority,
                ledger_start,
                available_bytes=self._available_bytes,
            )
        except (OSError, ValueError, sqlite3.Error, GhimeraRefused) as exc:
            raise SourceWorkFailure("frontier intent was not durably acknowledged") from exc

    def discard_queued(self, request: SourceCoordinates, reason: str, ledger_end: int) -> None:
        if not self._writing:
            raise SourceWorkFailure("frontier writer is not owned by this session")
        if self._frontier is None or request.identity in self._operations:
            return
        try:
            self._frontier.discard(
                request,
                reason,
                ledger_end,
                available_bytes=self._available_bytes,
            )
        except (OSError, ValueError, sqlite3.Error, GhimeraRefused) as exc:
            raise SourceWorkFailure("frontier discard was not durably acknowledged") from exc

    def enqueue_local_batch(
        self, requests: tuple[LocalSourceRequest, ...], ledger_start: int
    ) -> None:
        if not self._writing:
            raise SourceWorkFailure("frontier writer is not owned by this session")
        if self._frontier is None:
            return
        if any(request.identity in self._operations for request in requests):
            raise SourceWorkFailure(
                "local source already has an operation; inspect it before retry"
            )
        try:
            self._frontier.enqueue_local_batch(
                requests, ledger_start, available_bytes=self._available_bytes
            )
        except (OSError, ValueError, sqlite3.Error, GhimeraRefused) as exc:
            raise SourceWorkFailure("local batch was not durably acknowledged") from exc

    def begin(self, request: SourceRequest, ledger_start: int) -> SourceWorkToken:
        request = SourceRequest.model_validate(request.model_dump())
        if self._frontier is not None:
            try:
                self._frontier.require_queued(request)
            except ValueError as exc:
                raise SourceWorkFailure("acquisition requires its queued source intent") from exc
        return self._begin(request, ledger_start)

    def begin_local(self, request: LocalSourceRequest, ledger_start: int) -> SourceWorkToken:
        """Acknowledge the declared owned-file read before reserving or opening it."""
        request = LocalSourceRequest.model_validate(request.model_dump())
        try:
            request.validate_policy(self._config.local_inputs)
            if self._frontier is not None:
                self._frontier.require_queued(request)
        except ValueError as exc:
            raise SourceWorkFailure("local acquisition requires its declared input policy") from exc
        return self._begin(request, ledger_start)

    def _begin(
        self, request: SourceRequest | LocalSourceRequest, ledger_start: int
    ) -> SourceWorkToken:
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

    def capture_retained(self, original: RetainedOriginal, ledger_start: int) -> SourceWorkToken:
        """Capture the already-read capsule before current graph/model side effects."""
        original = RetainedOriginal.model_validate(original.model_dump())
        request = RetainedSourceRequest(
            schema="ghimera.retained-source-request/1",
            url=original.document.url,
            origin=original.origin,
        )
        observed = time.time()
        item = RetainedSourceOperation(
            schema="ghimera.retained-source-operation/1",
            operation_id=request.identity,
            sequence=len(self._operations),
            request=request,
            state="acquired",
            ledger_start=ledger_start,
            ledger_end=None,
            started_at=observed,
            acquired_at=observed,
            finished_at=None,
            original=original,
            reason=None,
        )
        self._write(item, new=True)
        return SourceWorkToken(item.operation_id, self._header)

    def _get(self, token: SourceWorkToken) -> Operation:
        self._private.check()
        if token.journal_header_sha256 != self._header:
            raise SourceWorkFailure("source-work token belongs to another run")
        try:
            return self._operations[token.operation_id]
        except KeyError:
            raise SourceWorkFailure("source-work identity is missing") from None

    def acquired(
        self,
        token: SourceWorkToken,
        page: Page,
        *,
        control: Callable[[SourceOperation], SourceAcquisitionSnapshot] | None = None,
    ) -> None:
        item = self._get(token)
        if not isinstance(item, SourceOperation) or item.state != "fetching":
            raise SourceWorkFailure("source original was already acknowledged")
        updated = SourceOperation.model_validate(
            dict(item.model_dump(), state="acquired", page=page, acquired_at=time.time())
        )
        self._write(updated, new=False, acquisition_control=control)

    def acquired_operation(self, token: SourceWorkToken) -> SourceOperation:
        item = self._get(token)
        if not isinstance(item, SourceOperation) or item.state != "acquired" or item.page is None:
            raise SourceWorkFailure("only exact acquired original can enter processing")
        return item

    def admitted_token(self, admission: SourceAcquisitionRead) -> SourceWorkToken:
        if not self._writing or self._read_acquisition(admission.sha256) != admission:
            raise SourceWorkFailure("acquisition admission changed under its owning writer")
        return SourceWorkToken(admission.operation.operation_id, self._header)

    def pending_operation(self, token: SourceWorkToken) -> SourceOperation:
        item = self._get(token)
        if not isinstance(item, SourceOperation) or item.state != "fetching":
            raise SourceWorkFailure("acquisition requires its exact pending original")
        return item

    def acquisition_state(self, operation_id: str) -> None:
        active = tuple(item for item in self._operations.values() if item.ledger_end is None)
        if (
            len(active) != 1
            or active[0].operation_id != operation_id
            or active[0].state != "acquired"
        ):
            raise SourceWorkFailure("acquisition capture requires exactly its one active original")

    def _validate_acquisition(
        self, snapshot: SourceAcquisitionSnapshot, item: SourceOperation
    ) -> None:
        snapshot = SourceAcquisitionSnapshot.model_validate(snapshot.model_dump())
        self.acquisition_state(item.operation_id)
        journal = (
            read_journal(self._config.journal, self._run_id)
            if self._config.journal is not None
            else None
        )
        h = snapshot.progress.harvest
        page = item.page
        if (
            self._acquisition is None
            or page is None
            or journal is None
            or (
                snapshot.run_id != self._run_id
                or h.receipt.effective_config != self._config
                or journal.header.config != self._config
                or journal.header.goal != h.goal
                or journal.header.judge != h.receipt.judge
                or journal.state != "unsealed"
                or journal.incomplete_tail
                or journal.rows != h.ledger
                or snapshot.operation_id != item.operation_id
                or snapshot.operation_sha256
                != hashlib.sha256(item.model_dump_json().encode()).hexdigest()
                or snapshot.return_sequence != len(journal.rows) - 1
                or unreconciled_model_sequences(journal.rows)
                or validate_model_rows(
                    self._config.model_work, self._config.judge_budget, journal.rows
                )
                != h.receipt.judge_calls
            )
        ):
            raise ValueError(
                "acquisition requires its original operation and exact acknowledged journal"
            )
        observed = journal.rows[snapshot.return_sequence]
        returned = observed.source_acquisition
        if returned is None or (
            snapshot.return_sha256
            != hashlib.sha256(observed.model_dump_json().encode()).hexdigest()
            or returned.operation_id != item.operation_id
            or returned.request_sha256
            != hashlib.sha256(item.request.model_dump_json().encode()).hexdigest()
            or returned.page_sha256 != hashlib.sha256(page.model_dump_json().encode()).hexdigest()
            or returned.source_sha256 != hashlib.sha256(page.body).hexdigest()
            or returned.source_url != page.url
            or returned.kind
            != ("owned_file" if isinstance(item.request, LocalSourceRequest) else "fetched")
            or snapshot.phase == "initial_local"
            and not isinstance(item.request, LocalSourceRequest)
            or snapshot.phase == "collection"
            and (
                not isinstance(item.request, SourceRequest)
                or snapshot.scope is None
                or not snapshot.scope.permits(item.request.url)
            )
        ):
            raise ValueError("acquisition return differs from its exact operation/request/Page")
        if isinstance(item.request, LocalSourceRequest):
            if item.request.seed not in snapshot.request.local_documents:
                raise ValueError("acquired owned source was not admitted by the original request")
            item.request.validate_policy(self._config.local_inputs)
        self.verify_frontier(snapshot.session)

    def _read_acquisition(self, expected_sha256: str | None = None) -> SourceAcquisitionRead:
        if self._acquisition is None:
            raise ValueError("acquisition recovery is not configured")
        row = self._private.db.execute(
            "SELECT payload,sha256 FROM source_acquisition WHERE id=1"
        ).fetchone()
        if row is None:
            raise ValueError("no atomic acquired source control exists")
        data, pin = row
        if (
            not isinstance(data, bytes)
            or len(data) > self._acquisition.max_capsule_bytes
            or hashlib.sha256(data).hexdigest() != pin
            or expected_sha256 is not None
            and pin != expected_sha256
        ):
            raise ValueError("acquisition control differs from its original bounded digest")
        snapshot = SourceAcquisitionSnapshot.model_validate_json(data)
        operations = self._read_operations()
        if not operations or not isinstance(operations[-1], SourceOperation):
            raise ValueError("acquisition lacks its exact latest original operation")
        operation = operations[-1]
        self._validate_acquisition(snapshot, operation)
        if self._config.journal is None:
            raise ValueError("acquisition lost original journal")
        return SourceAcquisitionRead(
            snapshot=snapshot,
            operation=operation,
            journal=read_journal(self._config.journal, self._run_id),
            sha256=pin,
        )

    @classmethod
    def acquisition(
        cls,
        config: GhimeraConfig,
        run_id: str,
        expected_sha256: str | None = None,
        *,
        expected_request: ResearchRequest | None = None,
        expected_models: ResearchRecoveryModels | None = None,
        expected_runtime: SourceCompletionRuntime | None = None,
    ) -> SourceAcquisitionRead:
        store = cls(config, run_id, create=False)
        try:
            with store._private.writer(), store._private.transaction():
                result = store._read_acquisition(expected_sha256)
                if (
                    expected_request is not None
                    and result.snapshot.request != expected_request
                    or expected_models is not None
                    and result.snapshot.models != expected_models
                    or expected_runtime is not None
                    and result.snapshot.runtime != expected_runtime
                ):
                    raise ValueError("acquisition request or original collaborator runtime changed")
                return result
        finally:
            store.close()

    def processing(self, token: SourceWorkToken) -> None:
        item = self._get(token)
        if item.state != "acquired":
            raise SourceWorkFailure("processing requires a newly acknowledged original")
        self._write(
            type(item).model_validate(dict(item.model_dump(), state="processing")), new=False
        )

    def processed(
        self,
        token: SourceWorkToken,
        result: Document | None,
        ledger_end: int,
        *,
        control: Callable[[], SourceCompletionSnapshot] | None = None,
    ) -> None:
        item = self._get(token)
        if not isinstance(item, SourceOperation) or item.state != "processing":
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
        self._write(updated, new=False, control=control)

    def _validate_completion(self, snapshot: SourceCompletionSnapshot) -> None:
        journal = (
            read_journal(self._config.journal, self._run_id)
            if self._config.journal is not None
            else None
        )
        h = snapshot.progress.harvest
        if (
            self._completion is None
            or journal is None
            or snapshot.run_id != self._run_id
            or snapshot.max_capsule_bytes != self._completion.max_capsule_bytes
            or h.receipt.effective_config != self._config
            or journal.header.config != self._config
            or journal.header.goal != h.goal
            or journal.header.judge != h.receipt.judge
            or journal.state != "unsealed"
            or journal.incomplete_tail
            or journal.rows != h.ledger
            or unreconciled_model_sequences(journal.rows)
            or validate_model_rows(self._config.model_work, self._config.judge_budget, journal.rows)
            != h.receipt.judge_calls
            or any(
                op.ledger_end is None or op.ledger_end > len(journal.rows)
                for op in self._operations.values()
            )
        ):
            raise ValueError("source completion requires the exact quiescent original journal")
        self.verify_frontier(snapshot.session)

    @classmethod
    def completion(
        cls,
        config: GhimeraConfig,
        run_id: str,
        expected_sha256: str | None = None,
        *,
        expected_request: ResearchRequest | None = None,
        expected_models: ResearchRecoveryModels | None = None,
        expected_runtime: SourceCompletionRuntime | None = None,
    ) -> SourceCompletionRead:
        """Bounded read/admission under the native writer lock; never replay or repair."""
        store = cls(config, run_id, create=False)
        try:
            with store._private.writer(), store._private.transaction():
                if store._completion is None:
                    raise ValueError("source completion recovery is not configured")
                row = store._private.db.execute(
                    "SELECT operation_id,operation_sha256,payload,sha256"
                    " FROM source_completion WHERE id=1"
                ).fetchone()
                if row is None:
                    raise ValueError("no atomic completed source control exists")
                operation_id, operation_sha256, data, pin = row
                if (
                    not isinstance(data, bytes)
                    or len(data) > store._completion.max_capsule_bytes
                    or hashlib.sha256(data).hexdigest() != pin
                    or expected_sha256 is not None
                    and pin != expected_sha256
                ):
                    raise ValueError("source control differs from its original bounded digest")
                operations = store._read_operations()
                if (
                    not operations
                    or operations[-1].operation_id != operation_id
                    or operations[-1].state != "processed"
                    or hashlib.sha256(operations[-1].model_dump_json().encode()).hexdigest()
                    != operation_sha256
                ):
                    raise ValueError("source control cannot omit later or changed source intents")
                snapshot = SourceCompletionSnapshot.model_validate_json(data)
                if (
                    expected_request is not None
                    and snapshot.request != expected_request
                    or expected_models is not None
                    and snapshot.models != expected_models
                    or expected_runtime is not None
                    and snapshot.runtime != expected_runtime
                ):
                    raise ValueError("source control request or current collaborators changed")
                store._validate_completion(snapshot)
                if operations[-1].ledger_end != len(snapshot.progress.harvest.ledger):
                    raise ValueError("source completion ledger cursor changed")
                if config.journal is None:
                    raise ValueError("source completion lost journal policy")
                return SourceCompletionRead(
                    snapshot=snapshot, journal=read_journal(config.journal, run_id), sha256=pin
                )
        finally:
            store.close()

    def completed_retained(self, token: SourceWorkToken, ledger_end: int) -> None:
        item = self._get(token)
        if not isinstance(item, RetainedSourceOperation) or item.state != "processing":
            raise SourceWorkFailure("retained admission processing was not started")
        self._write(
            RetainedSourceOperation.model_validate(
                dict(
                    item.model_dump(),
                    state="processed",
                    ledger_end=ledger_end,
                    finished_at=time.time(),
                )
            ),
            new=False,
        )

    def refused(
        self, token: SourceWorkToken, reason: str, ledger_end: int, *, cancelled: bool = False
    ) -> None:
        item = self._get(token)
        if item.state not in {"fetching", "acquired", "processing"}:
            raise SourceWorkFailure("terminal source work cannot be rewritten")
        updated = type(item).model_validate(
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

    def verify_frontier(self, state: SessionState) -> None:
        """A completed-round checkpoint may not omit or invent acknowledged queued work."""
        if self._frontier is None:
            if state.local_frontier:
                raise SourceWorkFailure("local pending work requires its configured frontier")
            return
        report = self.report()
        if state.local_frontier != report.queued_local:
            raise SourceWorkFailure("checkpoint changed acknowledged local pending work")
        queued = {
            (item.request.url, item.request.depth): item
            for item in report.queued
            if isinstance(item.request, SourceCoordinates)
        }
        observed: dict[str, float] = {}
        all_keys = {
            (item.request.url, item.request.depth)
            for item in report.frontier or ()
            if isinstance(item.request, SourceCoordinates)
        }
        for priority, url, depth in state.frontier:
            if (url, depth) not in all_keys:
                raise SourceWorkFailure("checkpoint contains unacknowledged frontier work")
            entry = queued.get((url, depth))
            if entry is None:
                # Duplicate heap entries already acquired or discarded are not new work.
                continue
            request = entry.request
            if not isinstance(request, SourceCoordinates):
                raise SourceWorkFailure("local work cannot enter the web priority heap")
            if (
                request.reference_hops != state.reference_hops.get(url, 0)
                or request.reference_origin != state.reference_origins.get(url)
                or request.scope != state.reference_scopes.get(url, request.scope)
            ):
                raise SourceWorkFailure("checkpoint changed queued reference ancestry or scope")
            observed[entry.entry_id] = min(priority, observed.get(entry.entry_id, priority))
        if observed != {item.entry_id: item.priority for item in report.queued}:
            raise SourceWorkFailure("checkpoint lost or reprioritized acknowledged queued work")

    def _report(self, active: bool) -> SourceWorkReport:
        # One read snapshot binds the frontier to its acquisition acknowledgements.
        # A live writer may otherwise advance between the two SELECTs.
        own_transaction = not self._private.db.in_transaction
        if own_transaction:
            self._private.db.execute("BEGIN")
        try:
            operations = self._read_operations()
            frontier = self._frontier.read() if self._frontier is not None else None
        finally:
            if own_transaction:
                self._private.db.rollback()
        if (
            sum(self._reserved(item) for item in operations)
            + sum(len(item.model_dump_json().encode()) for item in frontier or ())
            + self._completion_bytes
            > self._policy.max_store_bytes
        ):
            raise ValueError("source work and frontier exceed their shared capacity")
        if self._config.journal is None:
            raise ValueError("source work requires its native journal")
        journal = read_journal(self._config.journal, self._run_id)
        if any(
            item.ledger_start > len(journal.rows)
            or (item.ledger_end is not None and item.ledger_end > len(journal.rows))
            for item in operations
        ):
            raise ValueError("source work acknowledges observations absent from its journal")
        if frontier is not None and any(
            item.ledger_start > len(journal.rows)
            or (item.ledger_end is not None and item.ledger_end > len(journal.rows))
            for item in frontier
        ):
            raise ValueError("frontier acknowledges observations absent from its journal")
        return SourceWorkReport(
            schema="ghimera.source-work-report/1",
            run_id=self._run_id,
            journal_header_sha256=self._header,
            writer_active=active,
            operations=operations,
            frontier=frontier,
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
        queued = f" queued={len(report.queued)}" if report.frontier is not None else ""
        local = f" local_queued={len(report.queued_local)}" if report.queued_local else ""
        sys.stdout.write(
            f"run_id={report.run_id} operations={len(report.operations)} "
            f"writer_active={report.writer_active} unresolved={len(report.unresolved)}"
            f"{queued}{local}\n"
        )
        return 0
    except (OSError, ValueError, sqlite3.Error, SourceWorkFailure, GhimeraRefused):
        sys.stderr.write("Source-work inspection refused; preserve the run and inspect storage.\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
