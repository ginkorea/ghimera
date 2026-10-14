"""Run ceilings were removed in 0.4.12; a recipe that still names one is refused.

The core runs on the operator's own hardware and has no spend ceilings: a run
ends on the mission's own rules (goal satisfied, every question answered,
saturation, an empty frontier, cancellation or a contract failure). Spend is
still counted on receipts, rounds and ledger rows. A stale key is refused at
load, by name, rather than silently ignored.
"""

from typing import Any

REMOVED_RUN_CEILINGS = frozenset({"page_budget", "byte_budget", "judge_budget", "wall_seconds"})
REMOVED_RESEARCH_CEILINGS = frozenset({"max_rounds", "query_budget"})
REMOVED_SCORING_CEILINGS = frozenset({"encoding_call_budget", "encoding_char_budget"})
REMOVED_PROVIDER_CEILINGS = frozenset({"max_calls", "byte_budget", "call_seconds_budget"})
REMOVED_REFERENCE_CEILINGS = frozenset({"cited_by_query_budget"})
# Per-run call and byte totals of optional features: spend ceilings by another name.
REMOVED_SEMANTIC_CEILINGS = frozenset({"max_calls_per_run"})
REMOVED_IDENTITY_CEILINGS = frozenset({"max_proposal_calls", "max_review_calls"})
REMOVED_RERANK_CEILINGS = frozenset({"max_calls"})
REMOVED_LOCAL_INPUT_CEILINGS = frozenset({"max_files_per_run", "max_total_bytes"})
REMOVED_RESULT_CEILINGS = frozenset({"max_total_result_bytes"})
REMOVED_MODEL_WORK_CEILINGS = frozenset({"max_unanswered_calls", "max_input_bytes"})


def removed_ceiling_sentence(key: str) -> str:
    return (
        f"recipe key {key!r} is refused: the core no longer has run ceilings "
        "(removed in 0.4.12); delete the key"
    )


def refuse_removed(data: Any, removed: frozenset[str], section: str = "") -> Any:
    """A mode="before" validator body: name the first stale ceiling key, or pass."""
    if isinstance(data, dict):
        stale = sorted(key for key in removed if key in data)
        if stale:
            raise ValueError(removed_ceiling_sentence(section + stale[0]))
    return data
