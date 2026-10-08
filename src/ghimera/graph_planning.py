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
    PlanningRefusalGap,
    PlanningSelectionGap,
    PlanningSource,
    ResearchGap,
)
from ghimera.graph_types import GraphEdge, GraphRecord, IdentityDecision
from ghimera.identity_automation import acknowledged_history
from ghimera.identity_planning import build_identity_view
from ghimera.identity_resolution import resolve_identities
from ghimera.identity_selection import IdentitySelection, SelectionUnit
from ghimera.model_citations import citation_id
from ghimera.models import Document, LedgerRow
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.semantic_grounding import validate_coverage_findings
from ghimera.semantic_selection import validate_rows as validate_selection_rows
from ghimera.semantic_selection import validate_source
from ghimera.semantic_types import BatchedSemanticReview, GroundedSemanticReview, OmittedMention
from ghimera.visual_evidence import graph_visual_readings


class Population(GraphRecord):
    entities: tuple[PlanningEntity, ...]
    relations: tuple[GraphEdge, ...]
    sources: tuple[PlanningSource, ...]
    gaps: tuple[ResearchGap, ...] = Field(default=(), exclude_if=lambda v: not v)
    decisions: tuple[IdentityDecision, ...] = Field(default=(), exclude_if=lambda v: not v)


def policy_of(config: GhimeraConfig) -> GraphPlanningConfig | None:
    return config.research.graph_context if config.research is not None else None


def planning_call_revision(config: GhimeraConfig, context: PlanningGraph) -> str:
    """Pin the combined prompt without rewriting the published graph-view identity."""
    retained = config.research is not None and config.research.retained_evidence is not None
    return (
        context.prompt_revision + "+retained-snapshots/1" if retained else context.prompt_revision
    )


def evidence_size(
    entities: tuple[PlanningEntity, ...],
    relations: tuple[GraphEdge, ...],
    gaps: tuple[ResearchGap, ...] = (),
) -> int:
    return (
        sum(len(entity.evidence.quote) for entity in entities)
        + sum(len(span.quote) for edge in relations for span in edge.evidence)
        + sum(gap.selection.context.selected_chars for gap in gaps if gap.selection is not None)
        + sum(
            len(finding.surface)
            if isinstance(finding, OmittedMention)
            else len(finding.source.surface)
            + len(finding.target.surface)
            + len(finding.evidence.surface)
            for gap in gaps
            if isinstance(gap, PlanningGap)
            for finding in gap.coverage_findings
        )
    )


def build_context(config: GhimeraConfig, rows: tuple[LedgerRow, ...]) -> PlanningGraph | None:
    policy = policy_of(config)
    if policy is None:
        return None
    selections = validate_selection_rows(config, rows)
    entities: dict[str, PlanningEntity] = {}
    relations: dict[str, GraphEdge] = {}
    sources: dict[str, PlanningSource] = {}
    gaps: dict[str, ResearchGap] = {}
    for row in rows:
        selected_plan = row.semantic_selection
        if selected_plan is not None and selected_plan.context.omitted_chars and policy.max_gaps:
            source = PlanningSource(
                document_id=selected_plan.graph_document_id,
                source_url=selected_plan.context.source_url,
                document_sha256=selected_plan.context.source_sha256,
                text_sha256=selected_plan.context.text_sha256,
            )
            sources[source.document_id] = source
            identity = "gap:" + selected_plan.content_digest()
            gaps[identity] = PlanningSelectionGap(
                kind="semantic_selection",
                id=identity,
                source=source,
                start=0,
                end=selected_plan.context.total_chars,
                omitted_chars=selected_plan.context.omitted_chars,
                selection=selected_plan,
            )
        failed = row.semantic_refusal
        if failed is not None and policy.schema_version in {
            "ghimera.graph-planning/4",
            "ghimera.graph-planning/5",
        }:
            if row.refusal is None:
                raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
            source = PlanningSource(
                document_id=failed.graph_document_id,
                source_url=failed.source_url,
                document_sha256=failed.document_sha256,
                text_sha256=failed.text_sha256,
            )
            sources[source.document_id] = source
            identity = "gap:" + hashlib.sha256(row.model_dump_json().encode()).hexdigest()
            gaps[identity] = PlanningRefusalGap(
                kind="semantic_refusal",
                id=identity,
                source=source,
                start=failed.start,
                end=failed.end,
                omitted_chars=failed.omitted_chars,
                refusal=row.refusal,
                phase=failed.phase,
                observation_sequence=row.sequence,
                refusal_digest=failed.content_digest(),
                proposal_digest=failed.proposal.content_digest()
                if failed.proposal is not None
                else None,
                review_sequences=failed.review_sequences,
                continued=failed.continued,
                selection=selections.get(failed.graph_document_id),
            )
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
                selection=selections.get(window.graph_document_id),
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
        decisions=acknowledged_history(rows)
        if policy.schema_version == "ghimera.graph-planning/5"
        else (),
    )
    digest = population.content_digest()
    total_chars = evidence_size(population.entities, population.relations, population.gaps)
    selector = (
        IdentitySelection(
            policy.identity if policy.selection == "identity_first" else None,
            tuple(item.node for item in population.entities),
            population.relations,
            population.decisions,
        )
        if policy.selection == "identity_first"
        and policy.identity is not None
        or policy.schema_version == "ghimera.graph-planning/5"
        else None
    )
    by_edge = {edge.id: edge for edge in population.relations}

    selected_gaps: list[ResearchGap] = []

    def view(
        selected: tuple[PlanningEntity, ...],
        edges: tuple[GraphEdge, ...],
        pending_gaps: tuple[ResearchGap, ...] | None = None,
    ) -> PlanningGraph:
        visible_gaps = tuple(selected_gaps) if pending_gaps is None else pending_gaps
        used = {entity.evidence.document_id for entity in selected} | {
            span.document_id for edge in edges for span in edge.evidence
        }
        used.update(gap.source.document_id for gap in visible_gaps)
        included = {item.node.id for item in selected}
        history = (
            population.decisions
            if all(set(d.members) <= included for d in population.decisions)
            else ()
        )
        if history:
            used.update(e.document_id for d in history for e in d.evidence)
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
            resolved=resolve_identities(
                tuple(item.node for item in selected), history, as_of=policy.resolved_as_of
            )
            if policy.schema_version == "ghimera.graph-planning/5"
            else None,
            resolution_history=history,
            omitted_resolution_decisions=len(population.decisions) - len(history),
        )

    def fits(candidate: PlanningGraph) -> bool:
        return (
            len(candidate.entities) <= policy.max_entities
            and len(candidate.relations) <= policy.max_relations
            and evidence_size(candidate.entities, candidate.relations, candidate.gaps)
            + sum(len(e.quote) for d in candidate.resolution_history for e in d.evidence)
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
        + sum(len(e.quote) for d in context.resolution_history for e in d.evidence)
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
    if context.resolved is not None and context.resolved != resolve_identities(
        tuple(item.node for item in context.entities),
        context.resolution_history,
        as_of=policy.resolved_as_of,
    ):
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    docs: dict[str, tuple[Document, ...]] = {}
    for source in context.sources:
        readings = tuple(
            doc
            for doc in documents
            if doc.url == source.source_url
            and doc.sha256 == source.document_sha256
            and hashlib.sha256(doc.raw).hexdigest() == source.document_sha256
            and hashlib.sha256(doc.extracted.text.encode()).hexdigest() == source.text_sha256
        )
        if not readings:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        docs[source.document_id] = readings
    for span in (
        *(entity.evidence for entity in context.entities),
        *(span for edge in context.relations for span in edge.evidence),
        *(span for decision in context.resolution_history for span in decision.evidence),
    ):
        if not any(
            span.matches_reading(
                doc.sha256,
                doc.extracted.text,
                pdf_reading=doc.extracted.pdf_transcription.graph_reading()
                if doc.extracted.pdf_transcription is not None
                else None,
                visual_readings=graph_visual_readings(doc.images),
            )
            for doc in docs[span.document_id]
        ):
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    for gap in context.gaps:
        document = docs[gap.source.document_id][0]
        text = document.extracted.text
        if not 0 <= gap.start < gap.end <= len(text):
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        if gap.selection is None:
            if gap.omitted_chars not in {0, len(text) - gap.end}:
                raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        else:
            if (
                config.semantics is None
                or gap.source.document_id != gap.selection.graph_document_id
            ):
                raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
            try:
                validate_source(gap.selection, document, config.semantics)
            except ValueError:
                raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT) from None
            if not isinstance(gap, PlanningSelectionGap):
                windows = gap.selection.context.windows
                matches = tuple(
                    i for i, w in enumerate(windows) if (w.start, w.end) == (gap.start, gap.end)
                )
                if len(matches) != 1 or gap.omitted_chars != (
                    gap.selection.context.omitted_chars if matches[0] + 1 == len(windows) else 0
                ):
                    raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        if isinstance(gap, PlanningRefusalGap):
            semantics = config.semantics
            if (
                semantics is None
                or semantics.failure is None
                or (gap.continued and gap.refusal.value not in semantics.failure.allowed_refusals)
            ):
                raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        if isinstance(gap, PlanningGap) and gap.coverage_findings:
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
                or row.model_call.prompt_revision
                != planning_call_revision(config, row.planning_graph)
            ):
                raise ValueError("graph-aware planning calls must bind their configured planner")
        prefix.append(row)
