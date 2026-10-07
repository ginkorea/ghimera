"""Bounded planning projections of acknowledged source-local assertions."""

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
from ghimera.identity_planning_types import IdentityPlanningConfig, IdentityPlanningView

GRAPH_PLANNING_REVISION = "ghimera-graph-planning/1"
GRAPH_GAP_PLANNING_REVISION = "ghimera-graph-planning/2"
GRAPH_IDENTITY_PLANNING_REVISION = "ghimera-graph-planning/3"


class GraphPlanningConfig(GraphRecord):
    schema_version: Literal[
        "ghimera.graph-planning/1", "ghimera.graph-planning/2", "ghimera.graph-planning/3"
    ] = Field(alias="schema")
    entity_roles: Annotated[tuple[Name, ...], Field(min_length=1)]
    relation_rules: tuple[Name, ...]
    selection: Literal["newest_first"]
    max_entities: Positive
    max_relations: Positive
    max_evidence_chars: Positive
    max_context_chars: Annotated[int, Field(strict=True, ge=500)]
    max_gaps: Count = Field(default=0, exclude_if=lambda v: v == 0)
    identity: IdentityPlanningConfig | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def unique(self) -> "GraphPlanningConfig":
        if (self.schema_version != "ghimera.graph-planning/1") != (self.max_gaps > 0):
            raise ValueError("graph-planning/2 and /3 require an explicit positive gap limit")
        if (self.schema_version == "ghimera.graph-planning/3") != (self.identity is not None):
            raise ValueError("graph-planning/3 requires its explicit identity policy")
        if self.identity is not None and not set(
            self.identity.alias_rules + self.identity.exclusive_relations
        ) <= set(self.relation_rules):
            raise ValueError("identity rules must belong to the selected planning ontology")
        if len(set(self.entity_roles)) != len(self.entity_roles) or len(
            set(self.relation_rules)
        ) != len(self.relation_rules):
            raise ValueError("graph planning roles and relations must be unique")
        return self

    @property
    def view_schema(
        self,
    ) -> Literal[
        "ghimera.planning-graph/1", "ghimera.planning-graph/2", "ghimera.planning-graph/3"
    ]:
        if self.identity is not None:
            return "ghimera.planning-graph/3"
        return "ghimera.planning-graph/2" if self.max_gaps else "ghimera.planning-graph/1"


class PlanningSource(GraphRecord):
    document_id: Text
    source_url: Text
    document_sha256: Digest
    text_sha256: Digest


class PlanningEntity(GraphRecord):
    node: GraphNode
    evidence: GraphEvidence
    confidence: Confidence


class PlanningGap(GraphRecord):
    """Model-assessed coverage/quarantine, not an asserted entity or relation."""

    id: Annotated[str, Field(pattern=r"^gap:[0-9a-f]{64}$")]
    source: PlanningSource
    start: Count
    end: Positive
    coverage: Literal["adequate", "incomplete", "uncertain"]
    coverage_reason: Annotated[str, Field(min_length=1, max_length=4096)]
    excluded_mentions: Count
    excluded_relations: Count
    held_edges: Count
    omitted_chars: Count
    review_request_sha256: Digest
    review_model_id: Text
    review_model_revision: Text

    @model_validator(mode="after")
    def bounded(self) -> "PlanningGap":
        if self.end <= self.start:
            raise ValueError("planning gap requires a nonempty native window")
        return self


class PlanningGraph(GraphRecord):
    schema_version: Literal[
        "ghimera.planning-graph/1", "ghimera.planning-graph/2", "ghimera.planning-graph/3"
    ] = Field(alias="schema")
    policy_digest: Digest
    population_digest: Digest
    entities: tuple[PlanningEntity, ...]
    relations: tuple[GraphEdge, ...]
    sources: tuple[PlanningSource, ...]
    omitted_entities: Count
    omitted_relations: Count
    omitted_evidence_chars: Count
    gaps: tuple[PlanningGap, ...] = Field(default=(), exclude_if=lambda v: not v)
    omitted_gaps: Count = Field(default=0, exclude_if=lambda v: v == 0)
    identity: IdentityPlanningView | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def closed(self) -> "PlanningGraph":
        if self.schema_version == "ghimera.planning-graph/1" and (self.gaps or self.omitted_gaps):
            raise ValueError("planning gaps require planning-graph/2")
        if (self.schema_version == "ghimera.planning-graph/3") != (self.identity is not None):
            raise ValueError("planning-graph/3 requires its retained identity/dispute view")
        entities = {item.node.id for item in self.entities}
        sources = {item.document_id: item for item in self.sources}
        if (
            len(entities) != len(self.entities)
            or len(sources) != len(self.sources)
            or len({item.id for item in self.relations}) != len(self.relations)
            or len({item.id for item in self.gaps}) != len(self.gaps)
        ):
            raise ValueError("planning graph identities must be unique")
        used: set[str] = set()
        for gap in self.gaps:
            if sources.get(gap.source.document_id) != gap.source:
                raise ValueError("planning gap requires its retained source identity")
            used.add(gap.source.document_id)
        for edge in self.relations:
            if edge.claim_status != "model_asserted" or not {edge.source, edge.target} <= entities:
                raise ValueError("planning relations require asserted, selected endpoints")
        for evidence in (
            *(item.evidence for item in self.entities),
            *(span for edge in self.relations for span in edge.evidence),
        ):
            source = sources.get(evidence.document_id)
            if source is None or (source.document_sha256, source.text_sha256) != (
                evidence.document_sha256,
                evidence.text_sha256,
            ):
                raise ValueError("planning evidence requires its retained source identity")
            used.add(source.document_id)
        if used != sources.keys():
            raise ValueError("planning graph cannot add unreferenced source identities")
        if self.identity is not None:
            edges = {edge.id for edge in self.relations}
            group_refs = {group.id for group in self.identity.groups}
            if any(
                not set(group.node_ids) <= entities or not set(group.alias_relation_ids) <= edges
                for group in self.identity.groups
            ) or any(
                {dispute.left_relation_id, dispute.right_relation_id} - edges
                or dispute.subject_reference not in (entities | group_refs)
                for dispute in self.identity.disputes
            ):
                raise ValueError("identity questions require their selected original observations")
        return self

    @property
    def references(self) -> frozenset[str]:
        return (
            frozenset(item.node.id for item in self.entities)
            | frozenset(item.id for item in self.relations)
            | frozenset(item.id for item in self.gaps)
            | (self.identity.references if self.identity is not None else frozenset())
        )

    @property
    def prompt_revision(self) -> str:
        if self.identity is not None:
            return GRAPH_IDENTITY_PLANNING_REVISION
        return (
            GRAPH_GAP_PLANNING_REVISION
            if self.schema_version == "ghimera.planning-graph/2"
            else GRAPH_PLANNING_REVISION
        )
