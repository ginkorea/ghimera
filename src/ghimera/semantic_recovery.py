"""Bounded failed-window observations, separate from accepted graph facts."""

import hashlib
from typing import Literal

from ghimera.config import GhimeraConfig
from ghimera.models import Document, LedgerRow
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.semantic_selection_types import SemanticSelectionRef
from ghimera.semantic_types import SemanticProposal, SemanticRefusal


class SemanticRecoveryStopped(GhimeraRefused):
    """Declared recovery exhausted or disallowed; collection cannot skip past it."""


def failed_window(
    config: GhimeraConfig,
    document: Document,
    document_id: str,
    start: int,
    end: int,
    omitted: int,
    phase: Literal["extract", "review", "projection"],
    proposal: SemanticProposal | None,
    reviews: tuple[int, ...],
    rows: tuple[LedgerRow, ...],
    code: RefusalCode,
    *,
    allow_continue: bool,
    record_terminal: bool = False,
    selection: SemanticSelectionRef | None = None,
) -> SemanticRefusal | None:
    policy = config.semantics
    if policy is None or (policy.failure is None and not record_terminal):
        return None
    recovered = sum(
        row.semantic_refusal is not None and row.semantic_refusal.continued for row in rows
    )
    continued = (
        allow_continue
        and policy.failure is not None
        and phase in {"extract", "review"}
        and code.value in policy.failure.allowed_refusals
        and recovered < policy.failure.max_failed_windows_per_run
    )
    return SemanticRefusal(
        schema="ghimera.semantic-refusal/1",
        policy_digest=policy.content_digest(),
        graph_document_id=document_id,
        source_url=document.url,
        document_sha256=document.sha256,
        text_sha256=hashlib.sha256(document.extracted.text.encode()).hexdigest(),
        start=start,
        end=end,
        omitted_chars=omitted,
        phase=phase,
        proposal=proposal,
        review_sequences=reviews,
        continued=continued,
        selection=selection,
    )
