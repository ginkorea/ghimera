"""One deterministic bounded projection from original scored native windows."""

import hashlib
from typing import TYPE_CHECKING, Literal

from ghimera.judgment_types import (
    ScoredNativeContext,
    ScoredNativeWindow,
    ScoringNativeReading,
    ScoringSourceBinding,
)
from ghimera.scoring_types import SimilarityEvidence

if TYPE_CHECKING:
    from ghimera.models import Extracted, LedgerRow


def validate_native_source(extracted: "Extracted", source_url: str, source_sha256: str) -> None:
    """Use owning parser provenance; no generated reading becomes native text."""
    if extracted.pdf_transcription is not None:
        raise ValueError("scored native selection does not relabel generated PDF readings")
    reading = extracted.document_parse or extracted.extraction or extracted.source_feed
    if reading is None or (reading.source_url, reading.source_sha256) != (
        source_url,
        source_sha256,
    ):
        raise ValueError("selection requires exact original source/parser provenance")
    text_sha256 = hashlib.sha256(extracted.text.encode()).hexdigest()
    if extracted.document_parse is not None and extracted.document_parse.text_sha256 != text_sha256:
        raise ValueError("document text drifted from its original native parse")
    if extracted.extraction is not None and extracted.extraction.text_sha256 != text_sha256:
        raise ValueError("HTML text drifted from its original extraction")
    if extracted.source_feed is not None and extracted.source_feed.text != extracted.text:
        raise ValueError("feed text drifted from its original declaration")


def native_parser_text_sha256(row: "LedgerRow") -> str | None:
    if row.document_parse is not None:
        return row.document_parse.text_sha256
    if row.extraction is not None:
        return row.extraction.text_sha256
    if row.source_feed is not None:
        return hashlib.sha256(row.source_feed.text.encode()).hexdigest()
    return None


def native_scoring_reading(
    rows: tuple["LedgerRow", ...], extracted: "Extracted"
) -> ScoringNativeReading:
    reading = extracted.document_parse or extracted.extraction or extracted.source_feed
    if reading is None:
        raise ValueError("scoring projection requires observed native parser provenance")
    validate_native_source(extracted, reading.source_url, reading.source_sha256)
    digest = hashlib.sha256(reading.model_dump_json().encode()).hexdigest()
    observed = tuple(
        row
        for row in rows
        if row.event == "extraction"
        and (row.document_parse or row.extraction or row.source_feed) == reading
        and row.url == reading.source_url
        and row.refusal is None
    )
    if not observed:
        raise ValueError("native scoring requires its exact prior parser observation")
    return ScoringNativeReading(
        schema="ghimera.scoring-native-reading/1",
        source_url=reading.source_url,
        source_sha256=reading.source_sha256,
        text_sha256=hashlib.sha256(extracted.text.encode()).hexdigest(),
        parser_sequence=observed[-1].sequence,
        parser_sha256=digest,
        native_text=extracted.text,
    )


def scoring_source_binding(
    rows: tuple["LedgerRow", ...], extracted: "Extracted"
) -> ScoringSourceBinding:
    native = native_scoring_reading(rows, extracted)
    observed = tuple(
        row for row in rows if row.event == "scoring_source" and row.scoring_reading == native
    )
    if not observed:
        raise ValueError("original native reading must be committed before scoring")
    row = observed[-1]
    return ScoringSourceBinding(
        schema="ghimera.scoring-source/1",
        source_url=native.source_url,
        source_sha256=native.source_sha256,
        text_sha256=native.text_sha256,
        reading_sequence=row.sequence,
        reading_sha256=hashlib.sha256(row.model_dump_json().encode()).hexdigest(),
    )


def validate_scoring_source_binding(
    rows: tuple["LedgerRow", ...],
    scoring_row: "LedgerRow",
    source_url: str,
    source_sha256: str,
    extracted: "Extracted | None" = None,
) -> ScoringSourceBinding:
    proof = scoring_row.scoring_source
    if (
        proof is None
        or scoring_row.sequence >= len(rows)
        or (
            rows[scoring_row.sequence] != scoring_row
            or proof.reading_sequence >= scoring_row.sequence
            or (proof.source_url, proof.source_sha256) != (source_url, source_sha256)
        )
    ):
        raise ValueError(
            "scored context requires native source-bound scoring, not legacy text equality"
        )
    retained_row = rows[proof.reading_sequence]
    native = retained_row.scoring_reading
    if (
        native is None
        or retained_row.event != "scoring_source"
        or (
            native.parser_sequence >= retained_row.sequence
            or hashlib.sha256(retained_row.model_dump_json().encode()).hexdigest()
            != proof.reading_sha256
            or (native.source_url, native.source_sha256, native.text_sha256)
            != (proof.source_url, proof.source_sha256, proof.text_sha256)
        )
    ):
        raise ValueError("source reading must be committed before its encoder invocation")
    parser = rows[native.parser_sequence]
    reading = parser.document_parse or parser.extraction or parser.source_feed
    if (
        reading is None
        or parser.event != "extraction"
        or parser.refusal is not None
        or (
            parser.url != proof.source_url
            or (reading.source_url, reading.source_sha256)
            != (proof.source_url, proof.source_sha256)
            or hashlib.sha256(reading.model_dump_json().encode()).hexdigest()
            != native.parser_sha256
            or scoring_row.similarity is None
            or scoring_row.similarity.text_sha256 != proof.text_sha256
            or native_parser_text_sha256(parser) != proof.text_sha256
        )
    ):
        raise ValueError("scoring source drifted from its original parser operation")
    if hashlib.sha256(native.native_text.encode()).hexdigest() != proof.text_sha256:
        raise ValueError("retained native source text drifted")
    if (
        extracted is not None
        and scoring_source_binding(rows[: scoring_row.sequence], extracted) != proof
    ):
        raise ValueError("scoring belongs to a different native source operation")
    return proof


def select_scored_windows(
    *,
    source_url: str,
    source_sha256: str,
    extracted: "Extracted",
    goal_text: str,
    scoring_row: "LedgerRow",
    max_windows: int,
    max_chars: int,
    window_chars: int,
    padding_chars: int,
    rows: tuple["LedgerRow", ...],
) -> ScoredNativeContext:
    """No inference, retry, synthetic document, new index or acceptance decision."""
    validate_native_source(extracted, source_url, source_sha256)
    proof = validate_scoring_source_binding(rows, scoring_row, source_url, source_sha256, extracted)
    if any(
        type(value) is not int or value <= 0 for value in (max_windows, max_chars, window_chars)
    ) or (type(padding_chars) is not int or padding_chars < 0):
        raise ValueError("scored selection requires explicit finite native limits")
    text, scored = extracted.text, scoring_row.similarity
    text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    goal_sha256 = hashlib.sha256(goal_text.encode()).hexdigest()
    if (
        scored is None
        or scoring_row.event != "scoring"
        or scoring_row.refusal is not None
        or (
            scored.text_sha256 != text_sha256
            or scored.total_chars != len(text)
            or scored.goal_sha256 != goal_sha256
            or scoring_row.url not in {extracted.canonical_url, source_url}
            or any(
                seed.reference_text_sha256 != goal_sha256
                or seed.reference_source_id != "intent:" + goal_sha256
                for seed in scored.windows
            )
        )
    ):
        raise ValueError("selection requires its exact original source/intent scoring observation")
    for seed in scored.windows:
        if not 0 <= seed.start < seed.end <= len(text) or (
            hashlib.sha256(text[seed.start : seed.end].encode()).hexdigest() != seed.text_sha256
        ):
            raise ValueError("scoring window drifted from the original native source")
    spans, omissions = select_scored_spans(
        scored,
        max_windows=max_windows,
        max_chars=max_chars,
        window_chars=window_chars,
        padding_chars=padding_chars,
    )
    windows = tuple(
        ScoredNativeWindow(
            start=start,
            end=end,
            text=text[start:end],
            text_sha256=hashlib.sha256(text[start:end].encode()).hexdigest(),
            anchors=tuple(
                seed for seed in scored.windows if start <= seed.start and seed.end <= end
            ),
        )
        for start, end in spans
    )
    selected = sum(window.end - window.start for window in windows)
    return ScoredNativeContext.model_validate(
        {
            "schema": "ghimera.scored-native-context/1",
            "source_url": source_url,
            "source_sha256": source_sha256,
            "text_sha256": text_sha256,
            "goal_sha256": goal_sha256,
            "scoring_sequence": scoring_row.sequence,
            "references_sha256": scored.references_sha256,
            "scoring_source": proof,
            "scoring_sha256": hashlib.sha256(scoring_row.model_dump_json().encode()).hexdigest(),
            "total_chars": len(text),
            "selected_chars": selected,
            "omitted_chars": len(text) - selected,
            "windows": windows,
            "omissions": omissions,
        }
    )


Omission = Literal["max_chars", "max_windows", "scoring_omissions"]


def select_scored_spans(
    scored: SimilarityEvidence,
    *,
    max_windows: int,
    max_chars: int,
    window_chars: int,
    padding_chars: int,
) -> tuple[tuple[tuple[int, int], ...], tuple[Omission, ...]]:
    """The same bounded ranking/union for live selection and text-free journal replay."""
    scored = SimilarityEvidence.model_validate(scored.model_dump())
    if any(
        type(value) is not int or value <= 0 for value in (max_windows, max_chars, window_chars)
    ) or (type(padding_chars) is not int or padding_chars < 0):
        raise ValueError("scored selection requires explicit finite native limits")
    spans: list[tuple[int, int]] = []
    omissions: set[Omission] = set()
    if scored.omitted_chars:
        omissions.add("scoring_omissions")
    for seed in sorted(scored.windows, key=lambda item: (-item.cosine, item.start, item.end)):
        if any(start <= seed.start and seed.end <= end for start, end in spans):
            continue
        room = max_chars - sum(end - start for start, end in spans)
        seed_chars = seed.end - seed.start
        if seed_chars > min(window_chars, room):
            omissions.add("max_chars")
            continue
        extra = min(2 * padding_chars, window_chars - seed_chars, room - seed_chars)
        start = max(0, seed.start - extra // 2)
        end = min(scored.total_chars, seed.end + extra - (seed.start - start))
        neighbors = [(a, b) for a, b in spans if a <= end and start <= b]
        merged = (min([start] + [a for a, _ in neighbors]), max([end] + [b for _, b in neighbors]))
        remaining = [span for span in spans if span not in neighbors]
        if merged[1] - merged[0] > window_chars:
            # Do not duplicate overlap or secretly enlarge a configured window.
            omissions.add("max_windows")
            continue
        if len(remaining) >= max_windows:
            omissions.add("max_windows")
            continue
        if sum(b - a for a, b in remaining) + merged[1] - merged[0] > max_chars:
            omissions.add("max_chars")
            continue
        spans = sorted(remaining + [merged])
    if not spans:
        raise ValueError("no scored original window fits the configured judgment context")
    return tuple(spans), tuple(sorted(omissions))
