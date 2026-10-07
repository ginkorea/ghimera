"""Configured source-local semantic observations, independent of crawler state."""

from datetime import date
from types import MappingProxyType
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
MENTION_KEY_PROMPT_REVISION = "ghimera-semantic-extraction/2"
NATIVE_SPAN_PROMPT_REVISION = "ghimera-semantic-extraction/3"
DEFINED_ONTOLOGY_PROMPT_REVISION = "ghimera-semantic-extraction/4"
SEMANTIC_REVIEW_REVISION = "ghimera-semantic-verification/1"
SEMANTIC_PROFILES = MappingProxyType(
    {
        "ghimera.semantics/1": (None, SEMANTIC_PROMPT_REVISION),
        "ghimera.semantics/2": ("explicit_mention_keys", MENTION_KEY_PROMPT_REVISION),
        "ghimera.semantics/3": ("native_span_keys", NATIVE_SPAN_PROMPT_REVISION),
        "ghimera.semantics/4": ("defined_ontology", DEFINED_ONTOLOGY_PROMPT_REVISION),
    }
)


class SemanticDefinition(GraphRecord):
    name: Name
    definition: Annotated[str, Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def nonblank(self) -> "SemanticDefinition":
        if not self.definition.strip():
            raise ValueError("semantic definitions cannot be blank")
        return self


class SemanticVerificationConfig(GraphRecord):
    schema_version: Literal["ghimera.semantic-verification/1"] = Field(alias="schema")
    model_role: Literal["analyst", "reviewer", "judge"]
    max_calls_per_run: Positive


class SemanticConfig(GraphRecord):
    schema_version: Literal[
        "ghimera.semantics/1", "ghimera.semantics/2", "ghimera.semantics/3", "ghimera.semantics/4"
    ] = Field(alias="schema")
    prompt_profile: (
        Literal["explicit_mention_keys", "native_span_keys", "defined_ontology"] | None
    ) = Field(default=None, exclude_if=lambda value: value is None)
    model_role: Literal["analyst", "reviewer", "judge"]
    window_chars: Positive
    max_windows_per_document: Positive
    max_calls_per_run: Positive
    max_mentions_per_window: Positive
    max_relations_per_window: Positive
    entity_roles: Annotated[tuple[Name, ...], Field(min_length=1)]
    relation_rules: tuple[Name, ...]
    mention_rule: Name
    role_definitions: tuple[SemanticDefinition, ...] = Field(default=(), exclude_if=lambda v: not v)
    relation_definitions: tuple[SemanticDefinition, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )
    verification: SemanticVerificationConfig | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def distinct(self) -> "SemanticConfig":
        if self.prompt_profile != SEMANTIC_PROFILES[self.schema_version][0]:
            raise ValueError("semantic schema requires its exact versioned prompt profile")
        if (
            len(set(self.entity_roles)) != len(self.entity_roles)
            or len(set(self.relation_rules)) != len(self.relation_rules)
            or self.mention_rule in self.relation_rules
        ):
            raise ValueError("semantic roles and relation rules must be distinct")
        defined = self.schema_version == "ghimera.semantics/4"
        if defined:
            roles = {item.name for item in self.role_definitions}
            relations = {item.name for item in self.relation_definitions}
            if (
                roles != set(self.entity_roles)
                or relations != set(self.relation_rules)
                or len(roles) != len(self.role_definitions)
                or len(relations) != len(self.relation_definitions)
                or self.verification is None
            ):
                raise ValueError(
                    "defined ontology requires complete unique definitions and verification"
                )
        elif self.role_definitions or self.relation_definitions or self.verification is not None:
            raise ValueError("definitions and verification require semantics/4")
        return self

    @property
    def effective_prompt_revision(self) -> str:
        return SEMANTIC_PROFILES[self.schema_version][1]


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


ReviewVerdict = Literal["supported", "unsupported", "ambiguous"]


class MentionAssessment(GraphRecord):
    key: MentionKey
    verdict: ReviewVerdict
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class RelationAssessment(GraphRecord):
    index: Count
    verdict: ReviewVerdict
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class SemanticReview(GraphRecord):
    proposal_digest: Digest
    mentions: tuple[MentionAssessment, ...]
    relations: tuple[RelationAssessment, ...]
    coverage: Literal["adequate", "incomplete", "uncertain"]
    coverage_reason: Annotated[str, Field(min_length=1, max_length=4096)]
    model_call: ModelCallEvidence | None = None

    @model_validator(mode="after")
    def unique_assessments(self) -> "SemanticReview":
        if len({item.key for item in self.mentions}) != len(self.mentions) or len(
            {item.index for item in self.relations}
        ) != len(self.relations):
            raise ValueError("each proposed item requires one distinct assessment")
        return self


ExclusionReason = Literal[
    "native_span_missing",
    "unknown_role",
    "citation_mismatch",
    "review_unsupported",
    "review_ambiguous",
    "endpoint_quarantined",
    "unknown_relation",
    "endpoint_role_mismatch",
]


class MentionExclusion(GraphRecord):
    key: MentionKey
    reason: ExclusionReason


class RelationExclusion(GraphRecord):
    index: Count
    reason: ExclusionReason


class SemanticWindow(GraphRecord):
    schema_version: Literal["ghimera.semantic-window/1", "ghimera.semantic-window/2"] = Field(
        alias="schema"
    )
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
    review: SemanticReview | None = Field(default=None, exclude_if=lambda v: v is None)
    excluded_mentions: tuple[MentionExclusion, ...] = Field(default=(), exclude_if=lambda v: not v)
    excluded_relations: tuple[RelationExclusion, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )

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
        if (self.schema_version == "ghimera.semantic-window/2") != (self.review is not None):
            raise ValueError(
                "reviewed windows require semantic-window/2 and actual review evidence"
            )
        if self.review is None and (self.excluded_mentions or self.excluded_relations):
            raise ValueError("legacy windows cannot silently quarantine proposed items")
        if self.review is not None:
            review_call = self.review.model_call
            if (
                review_call is None
                or review_call.task != "semantic_review"
                or review_call.outcome != "success"
                or self.review.proposal_digest != self.proposal.content_digest()
                or {item.key for item in self.review.mentions}
                != {item.key for item in self.proposal.mentions}
                or {item.index for item in self.review.relations}
                != set(range(len(self.proposal.relations)))
                or len({item.key for item in self.excluded_mentions}) != len(self.excluded_mentions)
                or len({item.index for item in self.excluded_relations})
                != len(self.excluded_relations)
                or {item.key for item in self.entities}
                | {item.key for item in self.excluded_mentions}
                != {item.key for item in self.proposal.mentions}
                or bool(
                    {item.key for item in self.entities}
                    & {item.key for item in self.excluded_mentions}
                )
                or not {item.index for item in self.excluded_relations}
                <= set(range(len(self.proposal.relations)))
            ):
                raise ValueError("independent review must bind the complete original proposal")
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
