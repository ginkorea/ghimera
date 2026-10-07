"""Project acknowledged assertions into planning, never into verified facts.

The semantic ledger is the population owner: only success observations after
graph acknowledgement enter this view. Every planning view can be replayed from
its earlier observations, without adding a graph database or another model.
"""

import hashlib

from pydantic import Field, ValidationError

from ghimera.config import GhimeraConfig
from ghimera.evidence_context import native_citation
from ghimera.graph_planning_types import (
    GraphPlanningConfig,
    PlanningEntity,
    PlanningGap,
    PlanningGraph,
    PlanningSource,
)
from ghimera.graph_types import GraphEdge, GraphRecord
from ghimera.identity_planning import build_identity_view
from ghimera.identity_selection import IdentitySelection, SelectionUnit
from ghimera.model_citations import citation_id
from ghimera.models import Document, LedgerRow
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.semantic_grounding import validate_coverage_findings
from ghimera.semantic_types import BatchedSemanticReview, GroundedSemanticReview, OmittedMention


class Population(GraphRecord):
    entities: tuple[PlanningEntity, ...]
    relations: tuple[GraphEdge, ...]
    sources: tuple[PlanningSource, ...]
    gaps: tuple[PlanningGap, ...] = Field(default=(), exclude_if=lambda v: not v)


def policy_of(config: GhimeraConfig) -> GraphPlanningConfig | None:
    return config.research.graph_context if config.research is not None else None


def evidence_size(
    entities: tuple[PlanningEntity, ...],
    relations: tuple[GraphEdge, ...],
    gaps: tuple[PlanningGap, ...] = (),
) -> int:
    return (
        sum(len(entity.evidence.quote) for entity in entities)
        + sum(len(span.quote) for edge in relations for span in edge.evidence)
        + sum(
            len(finding.surface)
            if isinstance(finding, OmittedMention)
            else len(finding.source.surface)
            + len(finding.target.surface)
            + len(finding.evidence.surface)
            for gap in gaps
            for finding in gap.coverage_findings
        )
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
                coverage_findings=(
                    review.coverage_findings
                    if isinstance(review, (GroundedSemanticReview, BatchedSemanticReview))
                    else ()
                ),
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
    total_chars = evidence_size(population.entities, population.relations, population.gaps)
    selector = (
        IdentitySelection(
            policy.identity, tuple(item.node for item in population.entities), population.relations
        )
        if policy.selection == "identity_first" and policy.identity is not None
        else None
    )
    by_edge = {edge.id: edge for edge in population.relations}

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
            schema=policy.view_schema,
            policy_digest=policy.content_digest(),
            population_digest=digest,
            entities=selected,
            relations=edges,
            sources=tuple(source for key, source in sources.items() if key in used),
            omitted_entities=len(entities) - len(selected),
            omitted_relations=len(relations) - len(edges),
            omitted_evidence_chars=total_chars - evidence_size(selected, edges, visible_gaps),
            gaps=visible_gaps,
            omitted_gaps=len(gaps) - len(visible_gaps),
            identity=build_identity_view(
                policy.identity, tuple(item.node for item in selected), edges
            )
            if policy.identity is not None
            else None,
        )

    def fits(candidate: PlanningGraph) -> bool:
        return (
            len(candidate.entities) <= policy.max_entities
            and len(candidate.relations) <= policy.max_relations
            and evidence_size(candidate.entities, candidate.relations, candidate.gaps)
            <= policy.max_evidence_chars
            and len(candidate.model_dump_json()) <= policy.max_context_chars
            and len(candidate.gaps) <= policy.max_gaps
        )

    selected: dict[str, PlanningEntity] = {}
    edges: list[GraphEdge] = []
    current = view((), ())
    if not fits(current):
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)

    def expand(unit: SelectionUnit) -> tuple[dict[str, PlanningEntity], list[GraphEdge]]:
        combined = SelectionUnit(
            tuple(dict.fromkeys((*selected, *unit.node_ids))),
            tuple(dict.fromkeys((*(edge.id for edge in edges), *unit.relation_ids))),
        )
        if selector is not None:
            combined = selector.close(combined)
        return (
            {identity: entities[identity] for identity in combined.node_ids},
            [by_edge[identity] for identity in combined.relation_ids],
        )

    if selector is not None:
        for unit in selector.questions():
            expanded, claims = expand(unit)
            candidate = view(tuple(expanded.values()), tuple(claims))
            if fits(candidate):
                selected, edges, current = expanded, claims, candidate
    for gap in reversed(population.gaps):
        candidate = view(tuple(selected.values()), tuple(edges), (*selected_gaps, gap))
        if fits(candidate):
            selected_gaps.append(gap)
            current = candidate
    # Relations keep both endpoints and complete evidence; never clip a quote.
    for edge in reversed(population.relations):
        expanded, claims = expand(SelectionUnit((edge.source, edge.target), (edge.id,)))
        candidate = view(tuple(expanded.values()), tuple(claims))
        if fits(candidate):
            selected, edges, current = expanded, claims, candidate
    # Isolated observed mentions also help the planner identify relationship gaps.
    for planning_entity in reversed(population.entities):
        if planning_entity.node.id in selected:
            continue
        expanded, claims = expand(SelectionUnit((planning_entity.node.id,), ()))
        candidate = view(tuple(expanded.values()), tuple(claims))
        if fits(candidate):
            selected, edges, current = expanded, claims, candidate
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
        or context.schema_version != policy.view_schema
        or len(context.entities) > policy.max_entities
        or len(context.relations) > policy.max_relations
        or len(context.gaps) > policy.max_gaps
        or evidence_size(context.entities, context.relations, context.gaps)
        > policy.max_evidence_chars
        or len(context.model_dump_json()) > policy.max_context_chars
        or any(entity.node.role not in policy.entity_roles for entity in context.entities)
        or any(edge.rule not in policy.relation_rules for edge in context.relations)
    ):
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    if policy.identity is not None and context.identity != build_identity_view(
        policy.identity, tuple(item.node for item in context.entities), context.relations
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
        document = docs[(gap.source.source_url, gap.source.document_sha256)]
        text = document.extracted.text
        if not 0 <= gap.start < gap.end <= len(text) or gap.omitted_chars not in {
            0,
            len(text) - gap.end,
        }:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        if gap.coverage_findings:
            if config.semantics is None or gap.coverage != "incomplete":
                raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
            citation = native_citation(document, gap.start, gap.end)
            try:
                validate_coverage_findings(
                    config.semantics, gap.coverage_findings, citation.quote, citation_id(citation)
                )
            except GhimeraRefused:
                raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT) from None
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
