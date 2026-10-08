"""Replay the native selection and separate disposition, never rewrite a model ACK."""

import hashlib
from typing import TYPE_CHECKING

from ghimera.judgment_context import (
    native_parser_text_sha256,
    select_scored_spans,
    validate_scoring_source_binding,
)
from ghimera.judgment_types import (
    DocumentJudgmentEvidence,
    JudgmentContextReservation,
    ScoredNativeContext,
    ScoringNativeReading,
)

if TYPE_CHECKING:
    from ghimera.config import GhimeraConfig
    from ghimera.models import LedgerRow


def validate_native_reading(rows: tuple["LedgerRow", ...], sequence: int) -> ScoringNativeReading:
    row = rows[sequence]
    native = row.scoring_reading
    if native is None or row.event != "scoring_source" or native.parser_sequence >= sequence:
        raise ValueError("retained reading requires its prior native parser operation")
    native = ScoringNativeReading.model_validate(native.model_dump())
    parser = rows[native.parser_sequence]
    reading = parser.document_parse or parser.extraction or parser.source_feed
    text_hash = native_parser_text_sha256(parser)
    if (
        reading is None
        or parser.event != "extraction"
        or parser.refusal is not None
        or (
            row.url != native.source_url
            or parser.url != native.source_url
            or (reading.source_url, reading.source_sha256, text_hash)
            != (native.source_url, native.source_sha256, native.text_sha256)
            or hashlib.sha256(reading.model_dump_json().encode()).hexdigest()
            != native.parser_sha256
        )
    ):
        raise ValueError("retained reading drifted from its exact original parser evidence")
    return native


def validate_scored_context(
    rows: tuple["LedgerRow", ...],
    context: ScoredNativeContext,
    goal_text: str,
    *,
    max_windows: int,
    max_chars: int,
    window_chars: int,
    padding_chars: int,
) -> None:
    context = ScoredNativeContext.model_validate(context.model_dump())
    if context.scoring_sequence >= len(rows):
        raise ValueError("selected context lacks its original scoring observation")
    row = rows[context.scoring_sequence]
    proof = validate_scoring_source_binding(rows, row, context.source_url, context.source_sha256)
    native = validate_native_reading(rows, proof.reading_sequence)
    scored = row.similarity
    goal_hash = hashlib.sha256(goal_text.encode()).hexdigest()
    if (
        scored is None
        or row.event != "scoring"
        or row.refusal is not None
        or (
            hashlib.sha256(row.model_dump_json().encode()).hexdigest() != context.scoring_sha256
            or context.scoring_source != proof
            or (
                context.text_sha256,
                context.total_chars,
                context.references_sha256,
                context.goal_sha256,
            )
            != (native.text_sha256, len(native.native_text), scored.references_sha256, goal_hash)
            or scored.goal_sha256 != goal_hash
            or any(
                seed.reference_source_id != "intent:" + goal_hash
                or seed.reference_text_sha256 != goal_hash
                for seed in scored.windows
            )
            or any(
                hashlib.sha256(native.native_text[seed.start : seed.end].encode()).hexdigest()
                != seed.text_sha256
                for seed in scored.windows
            )
        )
    ):
        raise ValueError("selected context drifted from its original source/intent scoring")
    spans, omissions = select_scored_spans(
        scored,
        max_windows=max_windows,
        max_chars=max_chars,
        window_chars=window_chars,
        padding_chars=padding_chars,
    )
    if tuple((window.start, window.end) for window in context.windows) != spans or (
        context.omissions != omissions
        or any(
            window.text != native.native_text[window.start : window.end]
            or window.anchors
            != tuple(
                seed
                for seed in scored.windows
                if window.start <= seed.start and seed.end <= window.end
            )
            for window in context.windows
        )
    ):
        raise ValueError(
            "selected native spans/padding differ from deterministic original selection"
        )


def validate_scoring_readings(config: "GhimeraConfig", rows: tuple["LedgerRow", ...]) -> None:
    enabled = config.document_judgment is not None or (
        getattr(config.semantics, "window_selection", None) is not None
    )
    for row in rows:
        if row.scoring_reading is not None:
            if not enabled:
                raise ValueError(
                    "private scoring reading requires explicit native selection policy"
                )
            native = validate_native_reading(rows, row.sequence)
            parser = rows[native.parser_sequence]
            if parser.document_parse is not None and (
                config.document_extraction is None
                or parser.document_parse.config_digest
                != config.document_extraction.content_digest()
            ):
                raise ValueError("private native reading requires its original parser policy")
            if parser.extraction is not None and (
                config.extraction is None
                or parser.extraction.config_digest != config.extraction.content_digest()
            ):
                raise ValueError("private HTML reading requires its original parser policy")
            if parser.source_feed is not None and parser.source_feed.policy != config.source_feeds:
                raise ValueError("private feed reading requires its original parser policy")
        if row.scoring_source is not None:
            if not enabled:
                raise ValueError("scoring source requires explicit native selection policy")
            validate_scoring_source_binding(
                rows, row, row.scoring_source.source_url, row.scoring_source.source_sha256
            )


def validate_judgment_rows(
    config: "GhimeraConfig", goal_text: str, rows: tuple["LedgerRow", ...]
) -> None:
    from ghimera.models import Verdict
    from ghimera.scoring_validation import validate_reference_rows

    validate_reference_rows(config, goal_text, rows)
    validate_scoring_readings(config, rows)
    policy = config.document_judgment
    used: set[int] = set()
    for row in rows:
        reservation = row.judgment_context
        if reservation is not None:
            reservation = JudgmentContextReservation.model_validate(reservation.model_dump())
            if (
                policy is None
                or config.models is None
                or (
                    reservation.policy_sha256 != policy.content_digest()
                    or reservation.context.scoring_sequence >= row.sequence
                )
            ):
                raise ValueError("judgment reservation requires its exact original opt-in policy")
            validate_scored_context(
                rows,
                reservation.context,
                goal_text,
                max_windows=policy.max_windows,
                max_chars=policy.expanded_look_max_chars
                if reservation.second_look
                else policy.first_look_max_chars,
                window_chars=config.models.judge.context.window_chars,
                padding_chars=policy.padding_chars,
            )
        evidence = row.document_judgment
        if evidence is None:
            if policy is not None and row.event == "verdict" and row.refusal is None:
                raise ValueError("opt-in verdict requires its separate native judgment disposition")
            continue
        evidence = DocumentJudgmentEvidence.model_validate(evidence.model_dump())
        if (
            policy is None
            or config.models is None
            or not (
                evidence.context_sequence
                < evidence.intent_sequence
                < evidence.ack_sequence
                < row.sequence
            )
            or evidence.context_sequence in used
        ):
            raise ValueError(
                "judgment disposition requires its one original context/intent/ACK chain"
            )
        used.add(evidence.context_sequence)
        reserved = rows[evidence.context_sequence].judgment_context
        intent_row, ack_row = rows[evidence.intent_sequence], rows[evidence.ack_sequence]
        intent, ack = intent_row.model_intent, ack_row.model_ack
        if (
            reserved is None
            or intent is None
            or ack is None
            or ack.stored_output is None
            or (
                reserved.context != evidence.context
                or reserved.policy_sha256 != evidence.policy_sha256
                or reserved.second_look != evidence.second_look
                or evidence.policy_sha256 != policy.content_digest()
                or intent.phase != "verdict"
                or intent.input_scope != "port_input"
                or (intent.input_sha256, intent.input_bytes)
                != (reserved.input_sha256, reserved.input_bytes)
                or ack.intent_sequence != evidence.intent_sequence
                or ack.outcome != "returned"
                or ack.output_sha256 != evidence.output_sha256
                or row.url != evidence.context.source_url
                or intent_row.url != row.url
                or ack_row.url != row.url
                or intent_row.model != row.model
                or ack_row.model != row.model
            )
        ):
            raise ValueError("judgment disposition drifted from its original reservation/ACK")
        original = Verdict.model_validate_json(ack.stored_output.body())
        call = original.model_call
        if (
            call is None
            or original.decision != evidence.original_model_decision
            or (
                row.model_call != call
                or row.reason != f"{original.decision}: {original.reason}"
                or call.service != config.models.judge
                or call.prompt_revision != "ghimera-scored-document-judgment/1"
                or call.selected_spans
                != tuple(
                    (evidence.context.source_sha256, window.start, window.end)
                    for window in evidence.context.windows
                )
                or call.context_sha256 != evidence.context.content_digest()
                or call.omitted_chars != evidence.context.omitted_chars
            )
        ):
            raise ValueError(
                "judgment original model reply and exact selected spans must remain unchanged"
            )
