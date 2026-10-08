"""Explicit source-first presentation over the unchanged native judgment packet.

Only selected text fields move. Every original metadata field remains in the
trailing packet, without duplicate text. Character lengths delimit exact strings
even when source text contains quotes, newlines or apparent framing delimiters.
This does not change selection, provenance, relevance policy or model output.
"""

import json

from pydantic import JsonValue, TypeAdapter

from ghimera.judgment_types import ScoredNativeContext


def source_first_packet(packet: str, context: ScoredNativeContext) -> str:
    """Reframe only an exact native verdict packet bound to its selected context."""
    metadata = TypeAdapter(dict[str, JsonValue]).validate_json(packet)
    scored = metadata.get("scored_document")
    if metadata.get("task") != "verdict" or scored != context.model_dump(mode="json"):
        raise ValueError("source-first judgment requires its exact original native context")
    if not isinstance(scored, dict):
        raise ValueError("source-first judgment requires a native context object")
    windows = scored.get("windows")
    if not isinstance(windows, list) or len(windows) != len(context.windows):
        raise ValueError("source-first judgment requires every ordered native window")
    blocks: list[str] = []
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            raise ValueError("source-first judgment requires native window objects")
        text = window.pop("text", None)
        if not isinstance(text, str) or text != context.windows[index].text:
            raise ValueError("source-first judgment cannot rewrite its original selected text")
        blocks.append(f"scored_document.windows[{index}].text ({len(text)} characters):\n{text}\n")
    return (
        "".join(blocks)
        + "\nOriginal native packet metadata:\n"
        + json.dumps(metadata, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    )
