"""Instance-owned cursor consumption; remote effects stay in native owners."""

import hashlib
from typing import TYPE_CHECKING, Literal

from ghimera.graph_types import GraphBatch
from ghimera.models import Extracted, LinkCandidate, Verdict
from ghimera.source_processing import SourceGraphCommit, SourceProcessingCursor, Stage, row_pin

if TYPE_CHECKING:
    from ghimera.ledger import Ledger
    from ghimera.source_work import SourceWorkStore


class SourceProcessingWork:
    def __init__(self, store: "SourceWorkStore", ledger: "Ledger", *, resumed: bool) -> None:
        self.store, self.ledger, self.resumed = store, ledger, resumed
        self.cursor = store.current_processing()

    def save(
        self,
        stage: Stage,
        *,
        extracted: Extracted | None = None,
        ranked: tuple[LinkCandidate, ...] | None = None,
        model_sequence: int | None = None,
        request: bytes | None = None,
        verdict: Verdict | None = None,
        disposition: Literal["accept", "reject", "hold"] | None = None,
        second_look: bool = False,
    ) -> None:
        current = self.cursor
        updated = SourceProcessingCursor(
            schema="ghimera.source-processing-control/1",
            original=current.original,
            operation_sha256=current.operation_sha256,
            stage=stage,
            prefix_sha256=tuple(row_pin(row) for row in self.ledger.snapshot()),
            extracted=extracted if extracted is not None else current.extracted,
            ranked=ranked if ranked is not None else current.ranked,
            first_verdict=verdict
            if verdict is not None and not second_look
            else current.first_verdict,
            first_disposition=disposition
            if verdict is not None and not second_look
            else current.first_disposition,
            second_verdict=verdict
            if verdict is not None and second_look
            else current.second_verdict,
            second_disposition=disposition
            if verdict is not None and second_look
            else current.second_disposition,
            model_sequence=model_sequence,
            model_request_sha256=hashlib.sha256(request).hexdigest()
            if request is not None
            else None,
            graph_commits=current.graph_commits,
        )
        self.store.save_processing(updated)
        self.cursor = updated

    def graph_commit(self, batch: GraphBatch, acknowledged: bool) -> None:
        current = self.cursor
        commits = current.graph_commits
        if acknowledged:
            if not commits or commits[-1].batch != batch or commits[-1].acknowledged:
                raise ValueError("graph acknowledgement lacks its exact original prepared batch")
            commits = commits[:-1] + (SourceGraphCommit(batch=batch, acknowledged=True),)
        else:
            if commits and not commits[-1].acknowledged:
                raise ValueError("unknown original graph application remains held")
            commits += (SourceGraphCommit(batch=batch, acknowledged=False),)
        updated = SourceProcessingCursor.model_validate(
            dict(current.model_dump(), graph_commits=commits)
        )
        self.store.save_processing(updated)
        self.cursor = updated
