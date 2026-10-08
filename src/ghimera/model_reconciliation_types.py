"""Caller attribution is an assertion, never verified approval or a remote ACK."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.model_work_types import Count, Digest, ModelIntent


class ReconciliationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class ModelUnknownObservation(ReconciliationRecord):
    schema_version: Literal["ghimera.model-unknown-observation/1"] = Field(alias="schema")
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    snapshot_sha256: Digest
    header_sha256: Digest
    last_entry_sha256: Digest
    ledger_rows: Count
    original_intent_sequence: Count
    intent: ModelIntent
    intent_row_sha256: Digest
    acknowledgement_row_sha256: Digest | None
    outcome: Literal["unknown"]
    judge_calls: Count


class ModelReconciliationDecision(ReconciliationRecord):
    schema_version: Literal["ghimera.model-reconciliation/1"] = Field(alias="schema")
    action: Literal["abandon_and_authorize_new_attempt"]
    operation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    caller: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]
    observed: ModelUnknownObservation
    observed_sha256: Digest

    @model_validator(mode="after")
    def bound(self) -> "ModelReconciliationDecision":
        if not self.caller.strip() or not self.reason.strip():
            raise ValueError("model decision requires explicit caller attribution and reason")
        if self.observed.sha256 != self.observed_sha256:
            raise ValueError("model decision requires its exact observed state")
        return self


class ModelAttemptAuthorization(ReconciliationRecord):
    schema_version: Literal["ghimera.model-attempt/1"] = Field(alias="schema")
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    decision_sha256: Digest
    snapshot_sha256: Digest
    original_intent_sequence: Count
    attempt_intent_sequence: Count
    judge_reservation: Annotated[int, Field(strict=True, gt=0)]


class ModelAttemptConsumption(ReconciliationRecord):
    schema_version: Literal["ghimera.model-attempt-consumption/1"] = Field(alias="schema")
    authorization: ModelAttemptAuthorization


class ModelReconciliationPolicy(ReconciliationRecord):
    schema_version: Literal["ghimera.model-reconciliation-policy/1"] = Field(alias="schema")
    max_decisions_per_run: Annotated[int, Field(strict=True, gt=0)]
    max_decision_bytes: Annotated[int, Field(strict=True, gt=0)]
