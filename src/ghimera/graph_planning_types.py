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

GRAPH_PLANNING_REVISION = "ghimera-graph-planning/1"


class GraphPlanningConfig(GraphRecord):
    schema_version: Literal["ghimera.graph-planning/1"] = Field(alias="schema")
    entity_roles: Annotated[tuple[Name, ...], Field(min_length=1)]
    relation_rules: tuple[Name, ...]
    selection: Literal["newest_first"]
    max_entities: Positive
    max_relations: Positive
    max_evidence_chars: Positive
    max_context_chars: Annotated[int, Field(strict=True, ge=500)]

    @model_validator(mode="after")
    def unique(self) -> "GraphPlanningConfig":
        if len(set(self.entity_roles)) != len(self.entity_roles) or len(
            set(self.relation_rules)
        ) != len(self.relation_rules):
            raise ValueError("graph planning roles and relations must be unique")
        return self


class PlanningSource(GraphRecord):
    document_id: Text
    source_url: Text
    document_sha256: Digest
    text_sha256: Digest


class PlanningEntity(GraphRecord):
    node: GraphNode
    evidence: GraphEvidence
    confidence: Confidence


class PlanningGraph(GraphRecord):
    schema_version: Literal["ghimera.planning-graph/1"] = Field(alias="schema")
    policy_digest: Digest
    population_digest: Digest
    entities: tuple[PlanningEntity, ...]
    relations: tuple[GraphEdge, ...]
    sources: tuple[PlanningSource, ...]
    omitted_entities: Count
    omitted_relations: Count
    omitted_evidence_chars: Count

    @model_validator(mode="after")
    def closed(self) -> "PlanningGraph":
        entities = {item.node.id for item in self.entities}
        sources = {item.document_id: item for item in self.sources}
        if (
            len(entities) != len(self.entities)
            or len(sources) != len(self.sources)
            or len({item.id for item in self.relations}) != len(self.relations)
        ):
            raise ValueError("planning graph identities must be unique")
        used: set[str] = set()
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
        return self

    @property
    def references(self) -> frozenset[str]:
        return frozenset(item.node.id for item in self.entities) | frozenset(
            item.id for item in self.relations
        )
