"""Caller-owned decisions over exact native encoding observations and original quotas."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.corpus_config import CorpusConfig
from ghimera.embedding_types import Count, Digest, EncodingIntent, EncodingRecoveryState


class EncodingAdmission(BaseModel):
    """Captured before contact; unavailable historical policy is never inferred."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.encoding-admission/1"] = Field(alias="schema")
    configuration: CorpusConfig

    @model_validator(mode="after")
    def configured(self) -> "EncodingAdmission":
        if self.configuration.encoding_recovery is None:
            raise ValueError("encoding admission requires its original recovery allowance")
        return self

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class EncodingInvocationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.encoding-observation/1"] = Field(alias="schema")
    invocation_sha256: Digest
    intent: EncodingIntent
    status: Literal["unknown", "acknowledged", "refused", "cancelled"]
    reserved_bytes: Count
    result_sha256: Digest | None
    original_call_id: Annotated[int, Field(strict=True, gt=0)] | None
    admission: EncodingAdmission | None
    outgoing_decision_sha256: Digest | None
    attempt_consumed: bool | None
    current_generation: Count
    reservations: EncodingRecoveryState

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class EncodingReconciliationDecision(BaseModel):
    """Attribution is supplied by the caller, not a claim of verified human approval."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.encoding-reconciliation/1"] = Field(alias="schema")
    action: Literal["abandon_and_authorize_new_attempt"]
    caller: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]
    observed: EncodingInvocationObservation
    observed_sha256: Digest

    @model_validator(mode="after")
    def bound(self) -> "EncodingReconciliationDecision":
        if not self.caller.strip() or not self.reason.strip():
            raise ValueError("encoding decision requires explicit caller attribution and reason")
        if self.observed.sha256 != self.observed_sha256:
            raise ValueError("encoding decision does not bind its exact observed state")
        return self

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class EncodingAttemptAuthorization(BaseModel):
    """An exact durable decision receipt; consumption is still checked by its native owner."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.encoding-attempt/1"] = Field(alias="schema")
    original_invocation_sha256: Digest
    decision_sha256: Digest
    admission_sha256: Digest
    intent: EncodingIntent

    @property
    def invocation_sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
