"""Validate caller decisions in the native journal; never rewrite uncertain outcomes."""

from typing import TYPE_CHECKING

from ghimera.model_reconciliation_types import (
    ModelAttemptAuthorization,
    ModelUnknownObservation,
)

if TYPE_CHECKING:
    from ghimera.config import GhimeraConfig
    from ghimera.journal_types import JournalReport
    from ghimera.models import LedgerRow


def authorization(row: "LedgerRow") -> ModelAttemptAuthorization:
    decision, intent = row.model_decision, row.model_intent
    if decision is None or intent is None:
        raise ValueError("attempt requires its durable decision and separately reserved intent")
    return ModelAttemptAuthorization(
        schema="ghimera.model-attempt/1",
        run_id=decision.observed.run_id,
        decision_sha256=decision.sha256,
        snapshot_sha256=decision.observed.snapshot_sha256,
        original_intent_sequence=decision.observed.original_intent_sequence,
        attempt_intent_sequence=row.sequence,
        judge_reservation=intent.judge_reservation,
    )


def validate_decisions(rows: tuple["LedgerRow", ...]) -> tuple[ModelAttemptAuthorization, ...]:
    from ghimera.journal_types import JournalEntry, digest
    from ghimera.model_work import uncertain_model_sequences

    receipts: list[ModelAttemptAuthorization] = []
    operations: set[str] = set()
    originals: set[int] = set()
    consumed: set[int] = set()
    for row in rows:
        decision = row.model_decision
        if decision is not None:
            observed, intent = decision.observed, row.model_intent
            sequence = observed.original_intent_sequence
            if sequence >= row.sequence:
                raise ValueError("decision cannot refer to a later model intent")
            original = rows[sequence]
            ack_rows = tuple(
                item
                for item in rows[: row.sequence]
                if item.model_ack is not None and item.model_ack.intent_sequence == sequence
            )
            previous = observed.header_sha256
            for item in rows[: row.sequence]:
                previous = digest(
                    JournalEntry(
                        schema="chimera.run-journal-entry/1", previous_sha256=previous, row=item
                    )
                )
            if (
                decision.operation_id in operations
                or sequence in originals
                or original.model_decision is not None
                or observed.intent.phase not in {"plan", "assessment", "answer", "review"}
                or observed.intent.input_scope != "port_input"
                or original.url is not None
                or original.model_intent != observed.intent
                or digest(original) != observed.intent_row_sha256
                or sequence not in uncertain_model_sequences(rows[: row.sequence])
                or observed.ledger_rows != row.sequence
                or observed.last_entry_sha256 != previous
                or observed.judge_calls
                != sum(item.model_intent is not None for item in rows[: row.sequence])
                or observed.acknowledgement_row_sha256
                != (digest(ack_rows[0]) if ack_rows else None)
                or intent is None
                or intent
                != observed.intent.model_copy(
                    update={"judge_reservation": observed.judge_calls + 1}
                )
                or row.model != original.model
                or row.url != original.url
            ):
                raise ValueError("model decision lost its exact original unknown/chain binding")
            receipts.append(authorization(row))
            operations.add(decision.operation_id)
            originals.add(sequence)
        if row.model_attempt is not None:
            receipt = row.model_attempt.authorization
            sequence = receipt.attempt_intent_sequence
            if receipt not in receipts or sequence in consumed or sequence >= row.sequence:
                raise ValueError("model attempt requires one exact unconsumed authorization")
            if row.model != rows[sequence].model or row.url != rows[sequence].url:
                raise ValueError("model attempt changed its original model binding")
            consumed.add(sequence)
        if row.model_ack is not None:
            sequence = row.model_ack.intent_sequence
            if sequence in originals:
                raise ValueError("caller-abandoned original cannot receive a later acknowledgement")
            if rows[sequence].model_decision is not None and sequence not in consumed:
                raise ValueError("authorized attempt cannot acknowledge before durable consumption")
    return tuple(receipts)


def validate_policy(config: "GhimeraConfig", rows: tuple["LedgerRow", ...]) -> None:
    # Full header identities are checked by observation admission. Here validate
    # every decision against the immutable original configuration, never later quotas.
    receipts = validate_decisions(rows)
    if not receipts:
        return
    recovery = config.research_recovery
    policy = recovery.model_reconciliation if recovery is not None else None
    if policy is None or len(receipts) > policy.max_decisions_per_run:
        raise ValueError("model decisions require their original bounded opt-in policy")
    for row in rows:
        decision = row.model_decision
        if (
            decision is not None
            and len(decision.model_dump_json().encode()) > policy.max_decision_bytes
        ):
            raise ValueError("model decision exceeds its original byte allowance")


def unreconciled_model_sequences(rows: tuple["LedgerRow", ...]) -> tuple[int, ...]:
    from ghimera.model_work import uncertain_model_sequences

    abandoned = {receipt.original_intent_sequence for receipt in validate_decisions(rows)}
    return tuple(
        sequence for sequence in uncertain_model_sequences(rows) if sequence not in abandoned
    )


def observe(
    report: "JournalReport", snapshot_sha256: str, sequence: int
) -> ModelUnknownObservation:
    from ghimera.journal_types import JournalEntry, digest
    from ghimera.model_work import uncertain_model_sequences

    if sequence not in uncertain_model_sequences(report.rows):
        raise ValueError("decision requires an actual uncertain original invocation")
    original = report.rows[sequence]
    if original.model_intent is None or original.model_decision is not None:
        raise ValueError("replacement chains are not admitted")
    previous = digest(report.header)
    for row in report.rows:
        previous = digest(
            JournalEntry(schema="chimera.run-journal-entry/1", previous_sha256=previous, row=row)
        )
    acknowledgements = tuple(
        row
        for row in report.rows
        if row.model_ack is not None and row.model_ack.intent_sequence == sequence
    )
    return ModelUnknownObservation(
        schema="ghimera.model-unknown-observation/1",
        run_id=report.header.run_id,
        snapshot_sha256=snapshot_sha256,
        header_sha256=digest(report.header),
        last_entry_sha256=previous,
        ledger_rows=len(report.rows),
        original_intent_sequence=sequence,
        intent=original.model_intent,
        intent_row_sha256=digest(original),
        acknowledgement_row_sha256=digest(acknowledgements[0]) if acknowledgements else None,
        outcome="unknown",
        judge_calls=sum(row.model_intent is not None for row in report.rows),
    )
