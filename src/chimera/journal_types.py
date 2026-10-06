"""Versioned journal records; hashes detect corruption, not malicious-owner forgery."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from chimera.config import ChimeraConfig
from chimera.models import Goal, LedgerRow, ModelIdentity, Receipt, Record

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RunId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]


def canonical(record: Record) -> bytes:
    return json.dumps(
        record.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def digest(record: Record) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


class JournalHeader(Record):
    schema_version: Literal["chimera.run-journal-header/1"] = Field(alias="schema")
    run_id: RunId
    goal: Goal
    config: ChimeraConfig
    judge: ModelIdentity


class JournalEntry(Record):
    schema_version: Literal["chimera.run-journal-entry/1"] = Field(alias="schema")
    previous_sha256: Digest
    row: LedgerRow


class JournalDocument(Record):
    url: str
    sha256: Digest
    native_text_sha256: Digest
    raw_bytes: Annotated[int, Field(strict=True, ge=0)]


class JournalSummary(Record):
    schema_version: Literal["chimera.run-journal-summary/1"] = Field(alias="schema")
    run_id: RunId
    header_sha256: Digest
    last_entry_sha256: Digest
    ledger_rows: Annotated[int, Field(strict=True, ge=0)]
    receipt: Receipt
    documents: tuple[JournalDocument, ...]

    @model_validator(mode="after")
    def counts(self) -> "JournalSummary":
        if self.receipt.accepted_documents != len(self.documents):
            raise ValueError("journal summary must account for every accepted document")
        return self


class JournalReport(Record):
    schema_version: Literal["chimera.run-journal-report/1"] = Field(alias="schema")
    state: Literal["unsealed", "complete"]
    header: JournalHeader
    rows: tuple[LedgerRow, ...]
    incomplete_tail: bool
    summary: JournalSummary | None

    @model_validator(mode="after")
    def coherent(self) -> "JournalReport":
        if (self.state == "complete") != (self.summary is not None):
            raise ValueError("only a valid completion summary seals a journal")
        if self.state == "complete" and self.incomplete_tail:
            raise ValueError("a partial journal cannot be complete")
        if tuple(row.sequence for row in self.rows) != tuple(range(len(self.rows))):
            raise ValueError("journal rows must be contiguous")
        return self
