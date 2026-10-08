"""Opt-in semantic selection facts, separate from model assertions and coverage."""

from typing import Literal

from pydantic import Field

from ghimera.graph_types import Count, Digest, GraphRecord, Positive, Text
from ghimera.judgment_types import ScoredNativeContext


class SemanticWindowSelectionConfig(GraphRecord):
    schema_version: Literal["ghimera.semantic-window-selection/1"] = Field(alias="schema")
    strategy: Literal["intent_ranked"]
    max_selected_chars: Positive
    padding_chars: Count


class SemanticSelection(GraphRecord):
    """Durable pre-contact plan; selection itself claims no model or graph ACK."""

    schema_version: Literal["ghimera.semantic-selection/1"] = Field(alias="schema")
    policy_sha256: Digest
    graph_document_id: Text
    context: ScoredNativeContext


class SemanticSelectionRef(GraphRecord):
    selection_sha256: Digest
    window_index: Count
