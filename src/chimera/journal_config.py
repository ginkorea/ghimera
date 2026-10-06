"""Explicit storage policy for one run's durable observations."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]


class JournalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.run-journal-config/1"] = Field(alias="schema")
    directory: Path
    max_record_bytes: Positive
    max_journal_bytes: Positive
    max_summary_bytes: Positive
    max_records: Positive

    @model_validator(mode="after")
    def coherent(self) -> "JournalConfig":
        if not self.directory.is_absolute():
            raise ValueError("run journals require an explicit absolute storage root")
        if self.max_record_bytes > self.max_journal_bytes:
            raise ValueError("one record cannot exceed the journal's byte allowance")
        return self
