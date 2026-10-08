"""Owner-private atomic control snapshots and conservative journal-tail admission."""

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Protocol

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.journal_types import JournalReport, canonical
from ghimera.model_work import port_input, uncertain_model_sequences, validate_model_rows
from ghimera.models import LedgerRow
from ghimera.research_recovery_types import (
    ResearchControlSnapshot,
    ResearchPhaseResult,
    ResearchRecoveryModels,
    ResearchRecoveryRead,
    ResearchRecoveryReceipt,
)
from ghimera.research_types import (
    AnswerDraft,
    AnswerReview,
    Assessment,
    PlanningRequest,
    ResearchPlan,
    ResearchRequest,
)


class ResearchRecoveryPolicy(Protocol):
    """A validated operator policy supplied by the owning configuration boundary."""

    @property
    def max_snapshot_bytes(self) -> int: ...


class ResearchRecoveryStore:
    """Storage shares the journal's security and durability helpers, not a second DB.

    The caller owns the native journal writer lease and takes snapshots at a
    quiescent phase boundary. Reads never acquire a writer lease, repair a tail,
    append an acknowledgement or invoke a collaborator.
    """

    def __init__(self, config: GhimeraConfig, run_id: str, policy: ResearchRecoveryPolicy) -> None:
        # Keep the executable journal module out of package initialization,
        # as the established checkpoint store does. Otherwise `-m ghimera.journal`
        # sees an already imported module before its command is executed.
        from ghimera.journal import _run_path

        if (
            config.journal is None
            or config.research is None
            or config.model_work is None
            or config.model_work.results is None
            or type(policy.max_snapshot_bytes) is not int
            or policy.max_snapshot_bytes <= 0
        ):
            raise ValueError("research recovery requires research, journal and retained model work")
        self._config, self._run_id = config, run_id
        self._path = _run_path(config.journal, run_id)
        self._maximum = policy.max_snapshot_bytes

    def _check(self) -> None:
        from ghimera.journal import _private_directory, _run_path

        policy = self._config.journal
        if policy is None or _run_path(policy, self._run_id) != self._path:
            raise ValueError("research recovery storage changed")
        _private_directory(policy.directory)
        _private_directory(self._path)

    def _journal(self, snapshot: ResearchControlSnapshot) -> JournalReport:
        from ghimera.journal import read_journal

        self._check()
        policy = self._config.journal
        if policy is None:
            raise ValueError("research recovery lost its journal")
        report = read_journal(policy, self._run_id)
        harvest = snapshot.progress.harvest
        rows = harvest.ledger
        if (
            snapshot.run_id != self._run_id
            or snapshot.max_snapshot_bytes != self._maximum
            or harvest.receipt.effective_config != self._config
            or report.header.config != self._config
            or report.header.goal != harvest.goal
            or report.header.judge != harvest.receipt.judge
            or report.state != "unsealed"
            or report.incomplete_tail
            or report.rows[: len(rows)] != rows
            or any(row.event == "stop" for row in report.rows)
            or uncertain_model_sequences(rows)
            or validate_model_rows(self._config.model_work, self._config.judge_budget, rows)
            != harvest.receipt.judge_calls
        ):
            raise ValueError("recovery requires the exact intact unsealed original journal prefix")
        # Reuse the native serializer. This inert budget performs no work or
        # reservation; its configured model-work policy selects port serialization.
        request = port_input(RunBudget(self._config, lambda: 0.0), snapshot.model_request)
        pending = snapshot.pending_model
        if pending.input_sha256 != hashlib.sha256(
            request
        ).hexdigest() or pending.input_bytes != len(request):
            raise ValueError("recovery input binding differs from the exact native phase request")
        return report

    def write(self, snapshot: ResearchControlSnapshot) -> ResearchRecoveryReceipt:
        from ghimera.journal import _directory_sync, _read_file, _write_all

        snapshot = ResearchControlSnapshot.model_validate(snapshot.model_dump())
        report = self._journal(snapshot)
        if report.rows != snapshot.progress.harvest.ledger:
            raise ValueError("snapshot cannot omit work already acknowledged by the journal")
        data = canonical(snapshot)
        if len(data) > self._maximum:
            raise ValueError("research control snapshot exceeds its configured byte allowance")
        fd, name = tempfile.mkstemp(prefix=".research-control-", dir=self._path)
        staging = Path(name)
        try:
            try:
                _write_all(fd, data)
            finally:
                os.close(fd)
            if self._journal(snapshot).rows != report.rows:
                raise ValueError("journal changed while saving the research control snapshot")
            target = self._path / "research-control.json"
            if target.exists() or target.is_symlink():
                _read_file(target, self._maximum)
            os.replace(staging, target)
            _directory_sync(self._path)
        finally:
            staging.unlink(missing_ok=True)
        return ResearchRecoveryReceipt(
            schema="ghimera.research-recovery-receipt/1",
            run_id=self._run_id,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            phase=snapshot.phase,
            ledger_rows=len(report.rows),
        )

    @staticmethod
    def _decode(snapshot: ResearchControlSnapshot, body: bytes) -> ResearchPhaseResult:
        if snapshot.phase == "plan":
            return ResearchPlan.model_validate_json(body)
        if snapshot.phase == "assessment":
            return Assessment.model_validate_json(body)
        if snapshot.phase == "answer":
            return AnswerDraft.model_validate_json(body)
        return AnswerReview.model_validate_json(body)

    def _admit_tail(self, snapshot: ResearchControlSnapshot, report: JournalReport) -> int | None:
        tail = report.rows[len(snapshot.progress.harvest.ledger) :]
        if not tail:
            return None
        if uncertain_model_sequences(report.rows):
            raise ValueError("unknown model outcome requires reconciliation, never automatic retry")
        if len(tail) not in {2, 3}:
            raise ValueError("recovery cannot adopt effects beyond one original model return")
        original, acknowledged = tail[:2]
        intent, ack = original.model_intent, acknowledged.model_ack
        pending = snapshot.pending_model
        if (
            intent is None
            or ack is None
            or original.model != pending.model
            or intent.phase != pending.phase
            or intent.input_scope != "port_input"
            or intent.input_sha256 != pending.input_sha256
            or intent.input_bytes != pending.input_bytes
            or original
            != LedgerRow(
                sequence=original.sequence,
                event="model_intent",
                model=pending.model,
                reason="model_invocation_reserved",
                model_intent=intent,
            )
            or ack.intent_sequence != original.sequence
            or ack.outcome != "returned"
            or ack.output_scope != "port_output"
            or ack.stored_output is None
            or acknowledged
            != LedgerRow(
                sequence=acknowledged.sequence,
                event="model_ack",
                model=pending.model,
                reason="model_port_ended",
                model_ack=ack,
            )
        ):
            raise ValueError("recovery tail must bind the exact original retained port return")
        result = self._decode(snapshot, ack.stored_output.body())
        if result.model_call is not None and (
            result.model_call.service.model_id != pending.model.model_id
            or result.model_call.service.revision != pending.model.revision
        ):
            raise ValueError("retained research result changed its original model")
        if len(tail) == 3:
            observed = tail[2]
            expected = LedgerRow(
                sequence=observed.sequence,
                event=snapshot.phase,
                model=pending.model,
                model_call=result.model_call,
                reason="model_response:" + result.content_digest(),
                latency_seconds=observed.latency_seconds,
                planning_graph=snapshot.model_request.graph_context
                if isinstance(snapshot.model_request, PlanningRequest)
                else None,
            )
            if observed != expected:
                raise ValueError("recovery phase event must bind only the exact retained return")
        return original.sequence

    def read(
        self,
        expected_sha256: str,
        *,
        expected_request: ResearchRequest | None = None,
        expected_models: ResearchRecoveryModels | None = None,
    ) -> ResearchRecoveryRead:
        from ghimera.journal import _read_file

        self._check()
        data = _read_file(self._path / "research-control.json", self._maximum)
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("research control snapshot differs from its expected digest")
        snapshot = ResearchControlSnapshot.model_validate_json(data)
        if (
            expected_request is not None
            and snapshot.request != expected_request
            or expected_models is not None
            and snapshot.models != expected_models
        ):
            raise ValueError("recovery request or collaborator identity differs from the original")
        report = self._journal(snapshot)
        return ResearchRecoveryRead(
            snapshot=snapshot,
            journal=report,
            intent_sequence=self._admit_tail(snapshot, report),
        )
