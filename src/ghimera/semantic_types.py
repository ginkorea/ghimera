"""Configured source-local semantic observations, independent of crawler state."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.graph_types import (
    Confidence,
    Count,
    Digest,
    GraphEdge,
    GraphEvidence,
    GraphNode,
    GraphRecord,
    Name,
    Positive,
    Text,
)
from ghimera.model_types import ModelCallEvidence

CitationId = Annotated[str, Field(pattern=r"^cite:[0-9a-f]{64}$")]
MentionKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
SEMANTIC_PROMPT_REVISION = "ghimera-semantic-extraction/1"


class SemanticConfig(GraphRecord):
    schema_version: Literal["ghimera.semantics/1"] = Field(alias="schema")
    model_role: Literal["analyst", "reviewer", "judge"]
    window_chars: Positive
    max_windows_per_document: Positive
    max_calls_per_run: Positive
    max_mentions_per_window: Positive
    max_relations_per_window: Positive
    entity_roles: Annotated[tuple[Name, ...], Field(min_length=1)]
    relation_rules: tuple[Name, ...]
    mention_rule: Name

    @model_validator(mode="after")
    def distinct(self) -> "SemanticConfig":
        if (
            len(set(self.entity_roles)) != len(self.entity_roles)
            or len(set(self.relation_rules)) != len(self.relation_rules)
            or self.mention_rule in self.relation_rules
        ):
            raise ValueError("semantic roles and relation rules must be distinct")
        return self


class ProposedMention(GraphRecord):
    key: MentionKey
    role: Name
    surface: Text
    citation_id: CitationId
    occurrence: Count
    confidence: Confidence

    @model_validator(mode="after")
    def named(self) -> "ProposedMention":
        if not self.surface.strip():
            raise ValueError("named entity mentions cannot be blank")
        return self


class ProposedRelation(GraphRecord):
    rule: Name
    source: MentionKey
    target: MentionKey
    confidence: Confidence
    citation_ids: Annotated[tuple[CitationId, ...], Field(min_length=1)]
    valid_from: date | None
    valid_to: date | None

    @model_validator(mode="after")
    def temporal(self) -> "ProposedRelation":
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("asserted validity dates cannot run backwards")
        if len(set(self.citation_ids)) != len(self.citation_ids):
            raise ValueError("relation citations must be distinct")
        return self


class SemanticProposal(GraphRecord):
    mentions: tuple[ProposedMention, ...]
    relations: tuple[ProposedRelation, ...]
    model_call: ModelCallEvidence | None = None

    @model_validator(mode="after")
    def keys(self) -> "SemanticProposal":
        names = {item.key for item in self.mentions}
        if len(names) != len(self.mentions) or any(
            item.source not in names or item.target not in names for item in self.relations
        ):
            raise ValueError("relations require unique, observed mention keys")
        return self


class SemanticEntity(GraphRecord):
    key: MentionKey
    node: GraphNode
    evidence: GraphEvidence
    confidence: Confidence


class SemanticWindow(GraphRecord):
    schema_version: Literal["ghimera.semantic-window/1"] = Field(alias="schema")
    policy_digest: Digest
    source_url: Text
    document_sha256: Digest
    text_sha256: Digest
    graph_document_id: Text
    start: Count
    end: Positive
    omitted_chars: Count
    proposal: SemanticProposal
    entities: tuple[SemanticEntity, ...]
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    held_edges: tuple[GraphEdge, ...]

    @model_validator(mode="after")
    def evidence_bound(self) -> "SemanticWindow":
        call = self.proposal.model_call
        if (
            self.end <= self.start
            or call is None
            or call.task != "semantic_extract"
            or call.outcome != "success"
        ):
            raise ValueError("semantic windows require a successful bounded source call")
        for entity in self.entities:
            evidence = entity.evidence
            if (
                evidence.document_id != self.graph_document_id
                or evidence.document_sha256 != self.document_sha256
                or evidence.text_sha256 != self.text_sha256
                or not self.start <= evidence.start < evidence.end <= self.end
                or entity.node.label != evidence.quote
            ):
                raise ValueError("semantic mentions require native evidence in this window")
        for edge in self.edges + self.held_edges:
            if (
                edge.claim_status != "model_asserted"
                or edge.model_request_sha256 != call.request_sha256
            ):
                raise ValueError(
                    "extracted edges are versioned model assertions, never corroborated facts"
                )
        return self
