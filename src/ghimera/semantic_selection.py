"""Selection admission and archive replay; no model work or alternative store."""

import hashlib
from typing import TYPE_CHECKING

from ghimera.judgment_context import (
    select_scored_spans,
    select_scored_windows,
    validate_native_source,
    validate_scoring_source_binding,
)
from ghimera.judgment_validation import validate_scoring_readings
from ghimera.scoring_validation import validate_reference_rows
from ghimera.semantic_selection_types import SemanticSelection, SemanticSelectionRef

if TYPE_CHECKING:
    from ghimera.config import GhimeraConfig
    from ghimera.models import Document, LedgerRow
    from ghimera.semantic_types import SemanticConfig


def make_selection(
    config: "GhimeraConfig",
    document: "Document",
    document_id: str,
    intent: str,
    rows: tuple["LedgerRow", ...],
) -> SemanticSelection:
    policy = config.semantics
    if (
        policy is None
        or policy.window_selection is None
        or config.scoring is None
        or (config.scoring.reference_source != "intent")
    ):
        raise ValueError("ranked semantics requires its explicit intent-scoring policy")
    validate_reference_rows(config, intent, rows)
    validate_scoring_readings(config, rows)
    text_sha256 = hashlib.sha256(document.extracted.text.encode()).hexdigest()
    goal_sha256 = hashlib.sha256(intent.encode()).hexdigest()
    candidates = tuple(
        row
        for row in rows
        if row.similarity is not None
        and row.similarity.text_sha256 == text_sha256
        and row.similarity.goal_sha256 == goal_sha256
        and row.scoring_source is not None
        and row.scoring_source.source_url == document.url
        and row.scoring_source.source_sha256 == document.sha256
    )
    if not candidates:
        raise ValueError("ranked semantics requires a current original-source scoring observation")
    selected = policy.window_selection
    context = select_scored_windows(
        source_url=document.url,
        source_sha256=document.sha256,
        extracted=document.extracted,
        goal_text=intent,
        scoring_row=candidates[-1],
        max_windows=policy.max_windows_per_document,
        max_chars=selected.max_selected_chars,
        window_chars=policy.window_chars,
        padding_chars=selected.padding_chars,
        rows=rows,
    )
    return SemanticSelection(
        schema="ghimera.semantic-selection/1",
        policy_sha256=policy.content_digest(),
        graph_document_id=document_id,
        context=context,
    )


def validate_source(
    selection: SemanticSelection,
    document: "Document",
    policy: "SemanticConfig",
) -> None:
    """Full original-source admission, also usable by a planning projection."""
    selected = policy.window_selection
    context = selection.context
    if (
        selected is None
        or selection.policy_sha256 != policy.content_digest()
        or (
            context.source_url != document.url
            or context.source_sha256 != document.sha256
            or context.text_sha256 != hashlib.sha256(document.extracted.text.encode()).hexdigest()
            or context.total_chars != len(document.extracted.text)
            or len(context.windows) > policy.max_windows_per_document
            or context.selected_chars > selected.max_selected_chars
            or any(window.end - window.start > policy.window_chars for window in context.windows)
        )
    ):
        raise ValueError("semantic selection differs from its original source or effective bounds")
    validate_native_source(document.extracted, document.url, document.sha256)
    if any(
        window.text != document.extracted.text[window.start : window.end]
        for window in context.windows
    ):
        raise ValueError("selected semantic text must remain an exact original source slice")


def validate_attempt(
    selection: SemanticSelection,
    reference: SemanticSelectionRef | None,
    index: int,
    start: int,
    end: int,
    omitted: int,
) -> None:
    context = selection.context
    if (
        reference is None
        or reference.selection_sha256 != selection.content_digest()
        or (reference.window_index != index or index >= len(context.windows))
    ):
        raise ValueError("semantic attempt must bind the next exact selected original window")
    window = context.windows[index]
    expected_omitted = context.omitted_chars if index + 1 == len(context.windows) else 0
    if (start, end, omitted) != (window.start, window.end, expected_omitted):
        raise ValueError("semantic attempt order or unique native omission coverage changed")


def validate_rows(
    config: "GhimeraConfig",
    rows: tuple["LedgerRow", ...],
) -> dict[str, SemanticSelection]:
    """Always called, including policy-none and incomplete native journals."""
    plans: dict[str, SemanticSelection] = {}
    validate_scoring_readings(config, rows)
    counts: dict[str, int] = {}
    policy = config.semantics
    for position, row in enumerate(rows):
        plan = row.semantic_selection
        if (row.event == "semantic_selection") != (plan is not None):
            raise ValueError("semantic selection must retain its native event and evidence")
        if plan is not None:
            plan = SemanticSelection.model_validate(plan.model_dump())
            context = plan.context
            selected = policy.window_selection if policy is not None else None
            if (
                policy is None
                or selected is None
                or config.scoring is None
                or config.graph is None
                or (
                    config.scoring.reference_source != "intent"
                    or plan.policy_sha256 != policy.content_digest()
                    or plan.graph_document_id in plans
                    or len(plans) >= config.graph.max_nodes
                    or len(context.windows) > policy.max_windows_per_document
                    or context.selected_chars > selected.max_selected_chars
                    or any(w.end - w.start > policy.window_chars for w in context.windows)
                    or context.scoring_sequence >= position
                )
            ):
                raise ValueError(
                    "semantic selection requires one bounded original policy/source plan"
                )
            scored = rows[context.scoring_sequence]
            if (
                scored.similarity is None
                or scored.scoring_source is None
                or (
                    scored.event != "scoring"
                    or hashlib.sha256(scored.model_dump_json().encode()).hexdigest()
                    != context.scoring_sha256
                    or scored.scoring_source.source_url != context.source_url
                    or scored.scoring_source.source_sha256 != context.source_sha256
                    or scored.similarity.text_sha256 != context.text_sha256
                    or scored.similarity.goal_sha256 != context.goal_sha256
                    or scored.similarity.total_chars != context.total_chars
                    or scored.similarity.references_sha256 != context.references_sha256
                )
            ):
                raise ValueError(
                    "semantic selection must bind its exact earlier raw-source scoring row"
                )
            proof = validate_scoring_source_binding(
                rows, scored, context.source_url, context.source_sha256
            )
            if proof != context.scoring_source:
                raise ValueError("semantic selection must retain its original parser operation")
            native = rows[proof.reading_sequence].scoring_reading
            if (
                native is None
                or context.total_chars != len(native.native_text)
                or any(
                    window.text != native.native_text[window.start : window.end]
                    for window in context.windows
                )
            ):
                raise ValueError(
                    "padded semantic windows must replay from the original retained native reading"
                )
            if any(
                hashlib.sha256(native.native_text[seed.start : seed.end].encode()).hexdigest()
                != seed.text_sha256
                or seed.reference_source_id != "intent:" + context.goal_sha256
                or seed.reference_text_sha256 != context.goal_sha256
                for seed in scored.similarity.windows
            ):
                raise ValueError(
                    "the full observed scoring pool must retain its original source/intent anchors"
                )
            references = tuple(
                item.intent_reference
                for item in rows[: context.scoring_sequence]
                if item.intent_reference is not None
            )
            if (
                len(references) != 1
                or references[0].goal_sha256 != context.goal_sha256
                or (references[0].references.sha256 != context.references_sha256)
            ):
                raise ValueError(
                    "ranked semantics requires the original acknowledged intent vectors"
                )
            spans, omissions = select_scored_spans(
                scored.similarity,
                max_windows=policy.max_windows_per_document,
                max_chars=selected.max_selected_chars,
                window_chars=policy.window_chars,
                padding_chars=selected.padding_chars,
            )
            if (
                spans != tuple((w.start, w.end) for w in context.windows)
                or omissions != context.omissions
            ):
                raise ValueError("semantic selection must replay the exact bounded native ranking")
            for window in context.windows:
                anchors = tuple(
                    seed
                    for seed in scored.similarity.windows
                    if window.start <= seed.start and seed.end <= window.end
                )
                if window.anchors != anchors or any(
                    seed.reference_source_id != "intent:" + context.goal_sha256
                    or seed.reference_text_sha256 != context.goal_sha256
                    for seed in anchors
                ):
                    raise ValueError(
                        "semantic selection anchors must bind the original intent and scores"
                    )
            plans[plan.graph_document_id] = plan
        observation = row.semantic_window or row.semantic_refusal
        if observation is None:
            continue
        if policy is None or policy.window_selection is None:
            if observation.selection is not None:
                raise ValueError("semantic selection metadata requires its original opt-in policy")
            continue
        plan = plans.get(observation.graph_document_id)
        if plan is None or (
            observation.source_url != plan.context.source_url
            or observation.document_sha256 != plan.context.source_sha256
            or observation.text_sha256 != plan.context.text_sha256
        ):
            raise ValueError(
                "semantic observations require their earlier original-source selection"
            )
        index = counts.get(observation.graph_document_id, 0)
        validate_attempt(
            plan,
            observation.selection,
            index,
            observation.start,
            observation.end,
            observation.omitted_chars,
        )
        counts[observation.graph_document_id] = index + 1
    return plans
