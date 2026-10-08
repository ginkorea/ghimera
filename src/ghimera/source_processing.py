"""One original source's local consumer cursor in the existing SourceWork store.

No remote retry or content-key lookup is authorized by this cursor. Parser work
without a retained reading remains held, including locator-health side effects.
"""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.graph_types import GraphBatch
from ghimera.journal_types import JournalReport
from ghimera.models import Extracted, LinkCandidate, Record, Verdict
from ghimera.source_acquisition import SourceAcquisitionSnapshot
from ghimera.source_work_types import SourceOperation

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Stage = Literal[
    "parsing",
    "parsed",
    "scoring",
    "scored",
    "first_verdict",
    "second_verdict",
    "judged",
    "consuming",
]


class SourceGraphCommit(Record):
    batch: GraphBatch
    acknowledged: Annotated[bool, Field(strict=True)]


class SourceProcessingCursor(Record):
    schema_version: Literal["ghimera.source-processing-control/1"] = Field(alias="schema")
    original: SourceAcquisitionSnapshot
    operation_sha256: Digest
    stage: Stage
    prefix_sha256: tuple[Digest, ...]
    extracted: Extracted | None
    ranked: tuple[LinkCandidate, ...] | None
    first_verdict: Verdict | None
    first_disposition: Literal["accept", "reject", "hold"] | None
    second_verdict: Verdict | None
    second_disposition: Literal["accept", "reject", "hold"] | None
    model_sequence: Annotated[int, Field(strict=True, ge=0)] | None
    model_request_sha256: Digest | None
    graph_commits: tuple[SourceGraphCommit, ...]

    @model_validator(mode="after")
    def coherent(self) -> "SourceProcessingCursor":
        if self.stage != "parsing" and self.extracted is None:
            raise ValueError("processing requires its retained original parser reading")
        if (
            self.stage in {"scored", "first_verdict", "second_verdict", "judged", "consuming"}
            and self.ranked is None
        ):
            raise ValueError("verdict consumption requires acknowledged native scoring")
        if (self.first_verdict is None) != (self.first_disposition is None) or (
            self.second_verdict is None
        ) != (self.second_disposition is None):
            raise ValueError("retained native verdict requires its original client disposition")
        if self.stage == "second_verdict" and self.first_disposition != "hold":
            raise ValueError("second look requires the original first look HOLD")
        pending = self.stage in {"first_verdict", "second_verdict"}
        if (self.model_sequence is not None) != pending or (
            self.model_request_sha256 is not None
        ) != pending:
            raise ValueError("pending verdict requires exact original reservation coordinates")
        if any(not commit.acknowledged for commit in self.graph_commits[:-1]):
            raise ValueError("an unacknowledged graph batch must be the final original commit")
        return self


class SourceProcessingRead(Record):
    cursor: SourceProcessingCursor
    operation: SourceOperation
    journal: JournalReport
    sha256: Digest


def row_pin(value: Record) -> str:
    return hashlib.sha256(value.model_dump_json().encode()).hexdigest()
