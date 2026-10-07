"""Project acknowledged assertions into planning, never into verified facts.

The semantic ledger is the population owner: only success observations after
graph acknowledgement enter this view. Every planning view can be replayed from
its earlier observations, without adding a graph database or another model.
"""

import hashlib

from pydantic import Field, ValidationError

from ghimera.config import GhimeraConfig
from ghimera.graph_planning_types import (
    GraphPlanningConfig,
    PlanningEntity,
    PlanningGap,
    PlanningGraph,
    PlanningSource,
)
from ghimera.graph_types import GraphEdge, GraphRecord
from ghimera.models import Document, LedgerRow
from ghimera.refusals import GhimeraRefused, RefusalCode


class Population(GraphRecord):
    entities: tuple[PlanningEntity, ...]
    relations: tuple[GraphEdge, ...]
    sources: tuple[PlanningSource, ...]
    gaps: tuple[PlanningGap, ...] = Field(default=(), exclude_if=lambda v: not v)


def policy_of(config: GhimeraConfig) -> GraphPlanningConfig | None:
    return config.research.graph_context if config.research is not None else None


def evidence_size(entities: tuple[PlanningEntity, ...], relations: tuple[GraphEdge, ...]) -> int:
    return sum(len(entity.evidence.quote) for entity in entities) + sum(
        len(span.quote) for edge in relations for span in edge.evidence
    )


def build_context(config: GhimeraConfig, rows: tuple[LedgerRow, ...]) -> PlanningGraph | None:
    policy = policy_of(config)
    if policy is None:
        return None
    entities: dict[str, PlanningEntity] = {}
    relations: dict[str, GraphEdge] = {}
    sources: dict[str, PlanningSource] = {}
    gaps: dict[str, PlanningGap] = {}
    for row in rows:
        window = row.semantic_window
        if window is None:
            continue
        sources[window.graph_document_id] = PlanningSource(
            document_id=window.graph_document_id,
            source_url=window.source_url,
            document_sha256=window.document_sha256,
            text_sha256=window.text_sha256,
        )
        review = window.review
        if (
            policy.max_gaps
            and review is not None
            and review.model_call is not None
            and (
                review.coverage != "adequate"
                or window.excluded_mentions
                or window.excluded_relations
                or window.held_edges
                or window.omitted_chars
            )
        ):
            identity = "gap:" + window.content_digest()
            gaps[identity] = PlanningGap(
                id=identity,
                source=sources[window.graph_document_id],
                start=window.start,
                end=window.end,
                coverage=review.coverage,
                coverage_reason=review.coverage_reason,
                excluded_mentions=len(window.excluded_mentions),
                excluded_relations=len(window.excluded_relations),
                held_edges=len(window.held_edges),
                omitted_chars=window.omitted_chars,
                review_request_sha256=review.model_call.request_sha256,
                review_model_id=review.model_call.service.model_id,
                review_model_revision=review.model_call.service.revision,
            )
        admitted = {node.id for node in window.nodes}
        for entity in window.entities:
            if entity.node.id in admitted and entity.node.role in policy.entity_roles:
                entities[entity.node.id] = PlanningEntity(
                    node=entity.node,
                    evidence=entity.evidence,
                    confidence=entity.confidence,
                )
        for edge in window.edges:
            if edge.rule in policy.relation_rules and edge.claim_status == "model_asserted":
                relations[edge.id] = edge
    if any(not {edge.source, edge.target} <= entities.keys() for edge in relations.values()):
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    population = Population(
        entities=tuple(entities.values()),
        relations=tuple(relations.values()),
        sources=tuple(sources.values()),
        gaps=tuple(gaps.values()),
    )
    digest = population.content_digest()
    total_chars = evidence_size(population.entities, population.relations)

    selected_gaps: list[PlanningGap] = []

    def view(
        selected: tuple[PlanningEntity, ...],
        edges: tuple[GraphEdge, ...],
        pending_gaps: tuple[PlanningGap, ...] | None = None,
    ) -> PlanningGraph:
        visible_gaps = tuple(selected_gaps) if pending_gaps is None else pending_gaps
        used = {entity.evidence.document_id for entity in selected} | {
            span.document_id for edge in edges for span in edge.evidence
        }
        used.update(gap.source.document_id for gap in visible_gaps)
        return PlanningGraph(
            schema="ghimera.planning-graph/2" if policy.max_gaps else "ghimera.planning-graph/1",
            policy_digest=policy.content_digest(),
            population_digest=digest,
            entities=selected,
            relations=edges,
            sources=tuple(source for key, source in sources.items() if key in used),
            omitted_entities=len(entities) - len(selected),
            omitted_relations=len(relations) - len(edges),
            omitted_evidence_chars=total_chars - evidence_size(selected, edges),
            gaps=visible_gaps,
            omitted_gaps=len(gaps) - len(visible_gaps),
        )

    def fits(candidate: PlanningGraph) -> bool:
        return (
            len(candidate.entities) <= policy.max_entities
            and len(candidate.relations) <= policy.max_relations
            and evidence_size(candidate.entities, candidate.relations) <= policy.max_evidence_chars
            and len(candidate.model_dump_json()) <= policy.max_context_chars
            and len(candidate.gaps) <= policy.max_gaps
        )

    selected: dict[str, PlanningEntity] = {}
    edges: list[GraphEdge] = []
    current = view((), ())
    if not fits(current):
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    for gap in reversed(population.gaps):
        candidate = view((), (), (*selected_gaps, gap))
        if fits(candidate):
            selected_gaps.append(gap)
            current = candidate
    # Relations keep both endpoints and complete evidence; never clip a quote.
    for edge in reversed(population.relations):
        expanded = dict(selected)
        for identity in (edge.source, edge.target):
            expanded[identity] = entities[identity]
        candidate = view(tuple(expanded.values()), (*edges, edge))
        if fits(candidate):
            selected, edges, current = expanded, [*edges, edge], candidate
    # Isolated observed mentions also help the planner identify relationship gaps.
    for planning_entity in reversed(population.entities):
        if planning_entity.node.id in selected:
            continue
        candidate = view((*selected.values(), planning_entity), tuple(edges))
        if fits(candidate):
            selected[planning_entity.node.id], current = planning_entity, candidate
    return current


def validate_context(
    config: GhimeraConfig, context: PlanningGraph | None, documents: tuple[Document, ...]
) -> None:
    policy = policy_of(config)
    if (policy is None) != (context is None):
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    if context is None or policy is None:
        return
    try:
        context = PlanningGraph.model_validate(context.model_dump())
    except ValidationError:
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT) from None
    if (
        context.policy_digest != policy.content_digest()
        or context.schema_version
        != ("ghimera.planning-graph/2" if policy.max_gaps else "ghimera.planning-graph/1")
        or len(context.entities) > policy.max_entities
        or len(context.relations) > policy.max_relations
        or len(context.gaps) > policy.max_gaps
        or evidence_size(context.entities, context.relations) > policy.max_evidence_chars
        or len(context.model_dump_json()) > policy.max_context_chars
        or any(entity.node.role not in policy.entity_roles for entity in context.entities)
        or any(edge.rule not in policy.relation_rules for edge in context.relations)
    ):
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    docs = {(doc.url, doc.sha256): doc for doc in documents}
    sources = {source.document_id: source for source in context.sources}
    for source in context.sources:
        document = docs.get((source.source_url, source.document_sha256))
        if (
            document is None
            or hashlib.sha256(document.raw).hexdigest() != source.document_sha256
            or hashlib.sha256(document.extracted.text.encode()).hexdigest() != source.text_sha256
        ):
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    for span in (
        *(entity.evidence for entity in context.entities),
        *(span for edge in context.relations for span in edge.evidence),
    ):
        source = sources[span.document_id]
        document = docs[(source.source_url, source.document_sha256)]
        text = document.extracted.text
        if not 0 <= span.start < span.end <= len(text) or text[span.start : span.end] != span.quote:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    for gap in context.gaps:
        text = docs[(gap.source.source_url, gap.source.document_sha256)].extracted.text
        if not 0 <= gap.start < gap.end <= len(text) or gap.omitted_chars not in {
            0,
            len(text) - gap.end,
        }:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    if any(entity.node.label != entity.evidence.quote for entity in context.entities):
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)


def validate_rows(config: GhimeraConfig, rows: tuple[LedgerRow, ...]) -> None:
    prefix: list[LedgerRow] = []
    for row in rows:
        if row.event == "plan" and row.planning_graph != build_context(config, tuple(prefix)):
            raise ValueError(
                "planning graph must replay exactly from earlier acknowledged assertions"
            )
        if row.event == "plan" and row.planning_graph is not None and row.model_call is not None:
            if config.models is None or (
                row.model_call.service != config.models.planner
                or row.model_call.task != "plan"
                or row.model_call.prompt_revision != row.planning_graph.prompt_revision
            ):
                raise ValueError("graph-aware planning calls must bind their configured planner")
        prefix.append(row)
