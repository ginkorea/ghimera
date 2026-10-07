"""Explicit non-secret storage, destination and retry policy."""

import hashlib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.delivery_types import DeliveryTarget, Positive
from ghimera.models import Record

Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class DeliveryStoreConfig(Record):
    """Shared store policy; concrete stores declare their own versioned schema."""

    directory: Path
    target: DeliveryTarget
    max_items: Positive
    max_item_bytes: Positive
    max_total_item_bytes: Positive
    database_timeout_seconds: Seconds

    @model_validator(mode="after")
    def bounded_store(self) -> "DeliveryStoreConfig":
        if (
            not self.directory.is_absolute()
            or self.directory == Path("/")
            or self.max_item_bytes > self.max_total_item_bytes
        ):
            raise ValueError("delivery store requires a bounded explicit non-root directory")
        return self


class DirectoryDeliveryConfig(DeliveryStoreConfig):
    schema_version: Literal["ghimera.directory-delivery/1"] = Field(alias="schema")


class DeliveryOutboxConfig(DeliveryStoreConfig):
    schema_version: Literal["ghimera.delivery-outbox/1"] = Field(alias="schema")
    max_ack_bytes: Positive
    max_attempt_records: Positive
    max_attempts: Positive
    max_claims_per_dispatch: Positive
    dispatch_concurrency: Positive
    delivery_timeout_seconds: Seconds
    claim_seconds: Seconds
    retry_delay_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def coherent(self) -> "DeliveryOutboxConfig":
        if (
            self.max_item_bytes + self.max_ack_bytes > self.max_total_item_bytes
            or self.claim_seconds <= self.delivery_timeout_seconds
            or self.max_claims_per_dispatch > self.max_items
            or self.dispatch_concurrency > self.max_claims_per_dispatch
        ):
            raise ValueError("delivery storage, capacity and retry horizons must be coherent")
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class DeliveryWorkerConfig(Record):
    """Background policy independent of the published store/result identities."""

    schema_version: Literal["ghimera.delivery-worker/1"] = Field(alias="schema")
    poll_seconds: Seconds
    shutdown_grace_seconds: Seconds
    retention: Literal["keep", "prune_acknowledged"]
    max_prunes_per_cycle: Positive

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
