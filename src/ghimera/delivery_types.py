"""Immutable result delivery contracts; an acknowledgement is not a quality verdict."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.models import Harvest, Record
from ghimera.persistent_collector import PersistentCollection
from ghimera.research_types import ResearchResult

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(strict=True, gt=0)]
Result = Harvest | ResearchResult | PersistentCollection


class DeliveryTarget(Record):
    schema_version: Literal["ghimera.delivery-target/1"] = Field(alias="schema")
    id: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", max_length=120)]
    revision: Annotated[str, Field(min_length=1, max_length=240)]

    @model_validator(mode="after")
    def named(self) -> "DeliveryTarget":
        if not self.revision.strip() or any(ord(c) < 32 for c in self.revision):
            raise ValueError("delivery target revision must be explicit non-secret text")
        return self


class DeliveryItem(Record):
    schema_version: Literal["ghimera.delivery-item/1"] = Field(alias="schema")
    target: DeliveryTarget
    result: Result

    @property
    def payload(self) -> bytes:
        return self.result.model_dump_json().encode()

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    @property
    def identity(self) -> str:
        # The target and complete schema-qualified result both own this identity.
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class DeliveryAck(Record):
    schema_version: Literal["ghimera.delivery-ack/1"] = Field(alias="schema")
    delivery_id: Digest
    payload_sha256: Digest
    target: DeliveryTarget
    payload_bytes: Positive
    reference: Annotated[str, Field(min_length=1, max_length=1024)]

    def validate_item(self, item: DeliveryItem) -> None:
        if (
            self.delivery_id != item.identity
            or self.payload_sha256 != item.payload_sha256
            or self.target != item.target
            or self.payload_bytes != len(item.payload)
        ):
            raise ValueError("delivery acknowledgement must bind the exact target and result")


class DeliveryState(Record):
    schema_version: Literal["ghimera.delivery-state/1"] = Field(alias="schema")
    delivery_id: Digest
    status: Literal["pending", "delivering", "acknowledged"]
    attempts: Annotated[int, Field(strict=True, ge=0)]
    exhausted: bool
    payload_retained: bool
    last_failure: Literal["unavailable", "refused", "uncertain"] | None
    acknowledged: DeliveryAck | None

    @model_validator(mode="after")
    def reconciled(self) -> "DeliveryState":
        if (self.status == "acknowledged") != (self.acknowledged is not None):
            raise ValueError("delivery state requires its exact durable acknowledgement")
        if self.acknowledged is not None and self.acknowledged.delivery_id != self.delivery_id:
            raise ValueError("delivery state acknowledgement belongs to another item")
        if self.exhausted and self.status == "acknowledged":
            raise ValueError("acknowledged delivery is not retry-exhausted")
        if not self.payload_retained and self.status != "acknowledged":
            raise ValueError("unacknowledged delivery must retain its original payload")
        if self.status == "delivering" and self.attempts == 0:
            raise ValueError("a delivering item requires its durable admitted attempt")
        return self
