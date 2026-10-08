"""Owner-private atomic control snapshots and conservative journal-tail admission."""

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.journal_types import JournalReport, canonical
from ghimera.model_reconciliation import (
    authorization,
    observe,
    unreconciled_model_sequences,
    validate_decisions,
)
from ghimera.model_reconciliation_types import (
    ModelAttemptAuthorization,
    ModelReconciliationDecision,
    ModelUnknownObservation,
)
from ghimera.model_work import port_input, validate_model_rows
from ghimera.models import LedgerRow
from ghimera.research_recovery_types import (
    ResearchControlSnapshot,
    ResearchPhaseResult,
    ResearchQueryControlSnapshot,
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
    SearchRequest,
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

    def _journal(
        self, snapshot: ResearchControlSnapshot | ResearchQueryControlSnapshot
    ) -> JournalReport:
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
            or unreconciled_model_sequences(rows)
            or validate_model_rows(self._config.model_work, self._config.judge_budget, rows)
            != harvest.receipt.judge_calls
        ):
            raise ValueError("recovery requires the exact intact unsealed original journal prefix")
        # Reuse the native serializer. This inert budget performs no work or
        # reservation; its configured model-work policy selects port serialization.
        if isinstance(snapshot, ResearchQueryControlSnapshot):
            return report
        request = port_input(RunBudget(self._config, lambda: 0.0), snapshot.model_request)
        pending = snapshot.pending_model
        if pending.input_sha256 != hashlib.sha256(
            request
        ).hexdigest() or pending.input_bytes != len(request):
            raise ValueError("recovery input binding differs from the exact native phase request")
        return report

    def write(
        self, snapshot: ResearchControlSnapshot | ResearchQueryControlSnapshot
    ) -> ResearchRecoveryReceipt:
        from ghimera.journal import _directory_sync, _read_file, _write_all

        snapshot = TypeAdapter(
            ResearchControlSnapshot | ResearchQueryControlSnapshot
        ).validate_python(snapshot.model_dump())
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
        if unreconciled_model_sequences(report.rows):
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

    def _unknown_tail(
        self, snapshot: ResearchControlSnapshot, report: JournalReport, pin: str
    ) -> ModelUnknownObservation:
        tail = report.rows[len(snapshot.progress.harvest.ledger) :]
        if len(tail) not in {1, 2, 3}:
            raise ValueError("unknown recovery admits only one exact pending research invocation")
        original = tail[0]
        intent, pending = original.model_intent, snapshot.pending_model
        if (
            intent is None
            or original.model_decision is not None
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
            or unreconciled_model_sequences(report.rows) != (original.sequence,)
        ):
            raise ValueError("unknown model tail changed its exact saved pending operation")
        if len(tail) > 1:
            ack = tail[1].model_ack
            if (
                ack is None
                or ack.intent_sequence != original.sequence
                or not ack.uncertain
                or tail[1]
                != LedgerRow(
                    sequence=tail[1].sequence,
                    event="model_ack",
                    model=pending.model,
                    reason="model_port_ended",
                    model_ack=ack,
                )
            ):
                raise ValueError("unknown tail lost its original local end evidence")
        if len(tail) == 3:
            phase = tail[2]
            if (
                phase.event != snapshot.phase
                or phase.model != pending.model
                or phase.model_call is not None
                and (
                    phase.model_call.service.model_id != pending.model.model_id
                    or phase.model_call.service.revision != pending.model.revision
                )
                or phase.reason not in {"model_failed", "model_cancelled"}
                or phase
                != LedgerRow(
                    sequence=phase.sequence,
                    event=snapshot.phase,
                    model=pending.model,
                    model_call=phase.model_call,
                    refusal=phase.refusal,
                    reason=phase.reason,
                    latency_seconds=phase.latency_seconds,
                    planning_graph=snapshot.model_request.graph_context
                    if isinstance(snapshot.model_request, PlanningRequest)
                    else None,
                )
            ):
                raise ValueError(
                    "unknown tail includes later effects, not its failed original phase"
                )
        return observe(report, pin, original.sequence)

    def _attempt_tail(
        self,
        snapshot: ResearchControlSnapshot,
        report: JournalReport,
        pin: str,
        supplied: ModelAttemptAuthorization | None,
    ) -> tuple[int | None, ModelAttemptAuthorization | None]:
        tail = report.rows[len(snapshot.progress.harvest.ledger) :]
        choices = tuple(row for row in tail if row.model_decision is not None)
        if len(choices) != 1:
            raise ValueError("recovery requires one original decision, never an unresolved chain")
        chosen = choices[0]
        decision = chosen.model_decision
        if decision is None or decision.observed.snapshot_sha256 != pin:
            raise ValueError("decision changed its exact original snapshot")
        prefix = report.model_copy(update={"rows": report.rows[: chosen.sequence]})
        if self._unknown_tail(snapshot, prefix, pin) != decision.observed:
            raise ValueError("decision changed its original unknown phase observation")
        receipt = authorization(chosen)
        if chosen != LedgerRow(
            sequence=chosen.sequence,
            event="model_intent",
            model=snapshot.pending_model.model,
            reason="model_reconciliation_reserved",
            model_intent=chosen.model_intent,
            model_decision=decision,
        ):
            raise ValueError("attempt changed its atomic native decision reservation")
        remainder = report.rows[chosen.sequence + 1 :]
        if not remainder:
            if supplied != receipt:
                raise ValueError(
                    "unconsumed attempt requires its caller's exact durable authorization"
                )
            return None, receipt
        consumed = remainder[0]
        if (
            consumed.model_attempt is None
            or consumed.model_attempt.authorization != receipt
            or consumed
            != LedgerRow(
                sequence=consumed.sequence,
                event="model_attempt",
                model=chosen.model,
                reason="model_attempt_consumed",
                model_attempt=consumed.model_attempt,
            )
        ):
            raise ValueError("attempt lost its durable one-shot consumption")
        if len(remainder) not in {2, 3} or unreconciled_model_sequences(report.rows):
            raise ValueError(
                "consumed model attempt has unknown/later effects; never contact again"
            )
        ackrow = remainder[1]
        ack = ackrow.model_ack
        if (
            ack is None
            or ack.intent_sequence != chosen.sequence
            or ack.outcome != "returned"
            or ack.output_scope != "port_output"
            or ack.stored_output is None
            or ackrow
            != LedgerRow(
                sequence=ackrow.sequence,
                event="model_ack",
                model=chosen.model,
                reason="model_port_ended",
                model_ack=ack,
            )
        ):
            raise ValueError("attempt requires its exact retained native return")
        result = self._decode(snapshot, ack.stored_output.body())
        if result.model_call is not None and (
            result.model_call.service.model_id != snapshot.pending_model.model.model_id
            or result.model_call.service.revision != snapshot.pending_model.model.revision
        ):
            raise ValueError("attempt result changed its original model")
        if len(remainder) == 3:
            row = remainder[2]
            if row != LedgerRow(
                sequence=row.sequence,
                event=snapshot.phase,
                model=chosen.model,
                model_call=result.model_call,
                reason="model_response:" + result.content_digest(),
                latency_seconds=row.latency_seconds,
                planning_graph=snapshot.model_request.graph_context
                if isinstance(snapshot.model_request, PlanningRequest)
                else None,
            ):
                raise ValueError("attempt phase changed its retained result")
        if supplied is not None and supplied != receipt:
            raise ValueError("supplied attempt authorization changed")
        return chosen.sequence, None

    def read(
        self,
        expected_sha256: str,
        *,
        expected_request: ResearchRequest | None = None,
        expected_models: ResearchRecoveryModels | None = None,
        decision: ModelReconciliationDecision | None = None,
        attempt: ModelAttemptAuthorization | None = None,
        observe_unknown: bool = False,
    ) -> ResearchRecoveryRead:
        from ghimera.journal import _read_file

        self._check()
        data = _read_file(self._path / "research-control.json", self._maximum)
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("research control snapshot differs from its expected digest")
        snapshot: ResearchControlSnapshot | ResearchQueryControlSnapshot = TypeAdapter(
            ResearchControlSnapshot | ResearchQueryControlSnapshot
        ).validate_json(data)
        if (
            expected_request is not None
            and snapshot.request != expected_request
            or expected_models is not None
            and snapshot.models != expected_models
        ):
            raise ValueError("recovery request or collaborator identity differs from the original")
        report = self._journal(snapshot)
        if isinstance(snapshot, ResearchQueryControlSnapshot):
            if decision is not None or attempt is not None or observe_unknown:
                raise ValueError("query control cannot authorize an unknown model attempt")
            return self._query_tail(snapshot, report)
        tail = report.rows[len(snapshot.progress.harvest.ledger) :]
        if any(row.model_decision is not None for row in tail):
            validate_decisions(report.rows)
            sequence, admitted = self._attempt_tail(snapshot, report, expected_sha256, attempt)
            if decision is not None:
                raise ValueError("original decision already reserved; use exact authorization")
            return ResearchRecoveryRead(
                snapshot=snapshot, journal=report, intent_sequence=sequence, attempt=admitted
            )
        if decision is not None or observe_unknown:
            policy = self._config.research_recovery
            if policy is None or policy.model_reconciliation is None:
                raise ValueError("unknown reconciliation requires the original explicit policy")
            observation = self._unknown_tail(snapshot, report, expected_sha256)
            if decision is not None and (
                decision.observed != observation
                or len(decision.model_dump_json().encode())
                > policy.model_reconciliation.max_decision_bytes
            ):
                raise ValueError("model decision is stale, changed or exceeds original bounds")
            return ResearchRecoveryRead(
                snapshot=snapshot,
                journal=report,
                intent_sequence=None,
                decision=decision,
                observation=observation,
            )
        if attempt is not None:
            raise ValueError("attempt has no exact durable original decision")
        return ResearchRecoveryRead(
            snapshot=snapshot,
            journal=report,
            intent_sequence=self._admit_tail(snapshot, report),
        )

    def _query_tail(
        self, snapshot: ResearchQueryControlSnapshot, report: JournalReport
    ) -> ResearchRecoveryRead:
        from ghimera.query_work import validate_query_rows

        tail = report.rows[len(snapshot.progress.harvest.ledger) :]
        if not tail:
            return ResearchRecoveryRead(snapshot=snapshot, journal=report, intent_sequence=None)
        if tail[0].query_reservation != snapshot.pending_query:
            raise ValueError("query requires its exact durable original outer reservation")
        original = tail[0]
        remaining = list(tail[1:])
        rerank_sequence = None
        if remaining and remaining[0].rerank_reservation is not None:
            intent = remaining.pop(0)
            if not remaining or remaining[0].model_ack is None:
                raise ValueError("unknown original query score remains held")
            acknowledged = remaining.pop(0)
            ack = acknowledged.model_ack
            corpus = snapshot.pending_query.corpus
            reservation = intent.rerank_reservation
            if (
                ack is None
                or ack.intent_sequence != intent.sequence
                or ack.outcome != "returned"
                or ack.stored_output is None
                or corpus is None
                or reservation is None
                or reservation.corpus != corpus.configuration
                or reservation.request.corpus_id != corpus.corpus_id
                or reservation.request.generation != corpus.generation
                or reservation.channel != snapshot.pending_query.channel
                or reservation.operation_key != snapshot.pending_query.rerank_operation_key
                or reservation.request.query
                != (
                    SearchRequest.model_validate_json(
                        snapshot.pending_query.request_json
                    ).query.text
                    if snapshot.pending_query.channel == "discovery"
                    else snapshot.pending_query.request_json.decode()
                )
            ):
                raise ValueError("query score ACK lost its exact original corpus/run binding")
            rerank_sequence = intent.sequence
            if remaining and remaining[0].model_replay is not None:
                replay = remaining.pop(0).model_replay
                if replay is None or replay.intent_sequence != intent.sequence:
                    raise ValueError("query replay changed its original score intent")
        result = None
        if remaining:
            if len(remaining) != 1:
                raise ValueError("later query/source/graph effects require reconciliation")
            result = remaining[0]
            if (
                result.query_ack is None
                or result.query_ack.reservation_sequence != original.sequence
                or result.query_ack.outcome != "returned"
            ):
                raise ValueError("query requires its exact retained successful outer ACK")
        if result is None and rerank_sequence is None:
            raise ValueError("unknown outer query remains charged and held, never contacted")
        pending = validate_query_rows(self._config, report.rows)
        if set(pending) - (
            {original.sequence} if result is None else set()
        ) or unreconciled_model_sequences(report.rows):
            raise ValueError("unresolved query/model chains remain held")
        return ResearchRecoveryRead(
            snapshot=snapshot,
            journal=report,
            intent_sequence=None,
            query_sequence=original.sequence,
            query_ack_sequence=result.sequence if result is not None else None,
            query_rerank_sequence=rerank_sequence,
        )

    def attempt_history(self) -> tuple[ModelAttemptAuthorization, ...]:
        """Read durable receipts, never another grant or a fresh reservation."""
        from ghimera.journal import read_journal

        self._check()
        if self._config.journal is None:
            raise ValueError("attempt history lost its native journal")
        report = read_journal(self._config.journal, self._run_id)
        if report.incomplete_tail or report.header.config != self._config:
            raise ValueError("attempt history requires its intact original configuration")
        return validate_decisions(report.rows)
