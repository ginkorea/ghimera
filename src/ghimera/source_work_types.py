"""Source-work facts: interrupted processing is not an acknowledged result."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.models import Document, Page, Record, Scope

Count = Annotated[int, Field(strict=True, ge=0)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ObservedTime = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class SourceCoordinates(Record):
    """Queued coordinates are not permission to fetch an out-of-scope source."""

    url: Annotated[str, Field(min_length=1)]
    scope: Scope
    depth: Count
    reference_hops: Count
    reference_origin: str | None

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class SourceRequest(SourceCoordinates):
    @model_validator(mode="after")
    def permitted(self) -> "SourceRequest":
        if not self.scope.permits(self.url) or self.depth > self.scope.max_depth:
            raise ValueError("source-work intent must be inside its exact configured scope")
        return self


class SourceFrontierEntry(Record):
    schema_version: Literal["ghimera.source-frontier-entry/1"] = Field(alias="schema")
    entry_id: Digest
    sequence: Count
    request: SourceCoordinates
    priority: Annotated[float, Field(ge=-1, le=0, allow_inf_nan=False)]
    queued_at: ObservedTime
    ledger_start: Count
    discard_reason: Annotated[str, Field(min_length=1)] | None = None
    ledger_end: Count | None = None

    @model_validator(mode="after")
    def coherent(self) -> "SourceFrontierEntry":
        if self.entry_id != self.request.identity:
            raise ValueError("frontier identity must retain its exact source coordinates")
        if (self.discard_reason is None) != (self.ledger_end is None):
            raise ValueError("discarded frontier work needs its acknowledged journal boundary")
        if self.ledger_end is not None and self.ledger_end < self.ledger_start:
            raise ValueError("frontier journal boundaries cannot move backwards")
        return self


class SourceOperation(Record):
    schema_version: Literal["ghimera.source-operation/1"] = Field(alias="schema")
    operation_id: Digest
    sequence: Count
    request: SourceRequest
    state: Literal["fetching", "acquired", "processing", "processed", "refused", "cancelled"]
    ledger_start: Count
    ledger_end: Count | None
    started_at: ObservedTime
    acquired_at: ObservedTime | None
    finished_at: ObservedTime | None
    page: Page | None
    result: Document | None
    reason: str | None

    @model_validator(mode="after")
    def coherent(self) -> "SourceOperation":
        terminal = self.state in {"processed", "refused", "cancelled"}
        if terminal != (self.ledger_end is not None):
            raise ValueError("only terminal source work has a ledger acknowledgement")
        if terminal != (self.finished_at is not None):
            raise ValueError("only terminal source work has an observed completion time")
        if (self.page is not None) != (self.acquired_at is not None):
            raise ValueError("an acknowledged original needs its acquisition observation time")
        if self.ledger_end is not None and self.ledger_end < self.ledger_start:
            raise ValueError("source work cannot move its journal boundary backwards")
        if self.state == "fetching" and self.page is not None:
            raise ValueError("fetching has no acknowledged original")
        if self.state in {"acquired", "processing", "processed"} and self.page is None:
            raise ValueError("processing needs its acknowledged original")
        if self.page is not None and self.page.url != self.request.url:
            raise ValueError("acquisition does not belong to the requested source")
        if self.result is not None and (
            self.state != "processed"
            or self.page is None
            or self.result.url != self.page.final_url
            or self.result.raw != self.page.body
        ):
            raise ValueError("an accepted result needs its exact processed source original")
        if (self.reason is not None) != (self.state in {"refused", "cancelled"}):
            raise ValueError("refused or cancelled work needs an explicit reason")
        return self


class SourceWorkReport(Record):
    schema_version: Literal["ghimera.source-work-report/1"] = Field(alias="schema")
    run_id: str
    journal_header_sha256: Digest
    writer_active: bool
    operations: tuple[SourceOperation, ...]
    frontier: tuple[SourceFrontierEntry, ...] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @property
    def queued(self) -> tuple[SourceFrontierEntry, ...]:
        started = {item.operation_id for item in self.operations}
        return tuple(
            item
            for item in self.frontier or ()
            if item.discard_reason is None and item.entry_id not in started
        )

    @property
    def unresolved(self) -> tuple[SourceOperation, ...]:
        """No inferred failure while the owning writer is still observed alive."""
        if self.writer_active:
            return ()
        return tuple(
            item for item in self.operations if item.state in {"fetching", "acquired", "processing"}
        )
