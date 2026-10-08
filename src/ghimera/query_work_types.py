"""Leaf original-query records; the native journal remains their authority."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.corpus_config import CorpusConfig

Count = Annotated[int, Field(strict=True, ge=0)]
Positive = Annotated[int, Field(strict=True, gt=0)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class QueryRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class QueryCursor(QueryRecord):
    stage: Literal["initial_retained", "planned_retained", "discovery", "cited_by"]
    round_number: Positive
    query_index: Count
    provider_index: Count = 0
    parent_sequence: Count | None = Field(default=None, exclude_if=lambda v: v is None)


class QueryCorpusBinding(QueryRecord):
    configuration: CorpusConfig
    corpus_id: Annotated[str, Field(min_length=1)]
    generation: Count
    encoding_invocation_sha256: Digest


class QueryReservation(QueryRecord):
    schema_version: Literal["ghimera.query-reservation/1"] = Field(alias="schema")
    operation_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    cursor: QueryCursor
    channel: Literal["discovery", "retained"]
    provider: Annotated[str, Field(min_length=1)]
    provider_revision: Annotated[str, Field(min_length=1)]
    request_json: bytes
    request_sha256: Digest
    search_reservation: Positive | None = None
    fetch_reservation: Positive | None = None
    retained_reservation: Positive | None = None
    input_chars: Count = 0
    corpus: QueryCorpusBinding | None = None
    rerank_operation_key: Annotated[str, Field(min_length=1)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def exact(self) -> "QueryReservation":
        if (
            hashlib.sha256(self.request_json).hexdigest() != self.request_sha256
            or not self.request_json
            or (self.channel == "discovery") != (self.search_reservation is not None)
            or (self.channel == "discovery") != (self.fetch_reservation is not None)
            or (self.channel == "retained") != (self.retained_reservation is not None)
            or (self.channel == "retained")
            != (self.cursor.stage in {"initial_retained", "planned_retained"})
            or (self.cursor.stage == "cited_by") != (self.cursor.parent_sequence is not None)
            or self.channel == "retained"
            and (self.corpus is None or self.input_chars <= 0)
        ):
            raise ValueError(
                "query reservation must bind its exact original native input and spend"
            )
        return self


class QueryAcknowledgement(QueryRecord):
    schema_version: Literal["ghimera.query-ack/1"] = Field(alias="schema")
    reservation_sequence: Count
    outcome: Literal["returned", "refused", "cancelled"]
    result_json: bytes | None = None
    result_sha256: Digest | None = None

    @model_validator(mode="after")
    def exact(self) -> "QueryAcknowledgement":
        if (self.outcome == "returned") != (self.result_json is not None) or (
            self.result_sha256
            != (
                hashlib.sha256(self.result_json).hexdigest()
                if self.result_json is not None
                else None
            )
        ):
            raise ValueError("query ACK must retain the exact bounded return, not a digest alone")
        return self
