"""Sequential native extraction windows and deterministic graph projection.

The model proposes observations. This owner checks native spans, ontology and
policy before the existing graph owner acknowledges a transaction. No alias
merge, corroboration upgrade, model fallback or graph store lives here.
"""

import asyncio
import re
from typing import Protocol

from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.evidence_context import ContextSelector, native_citation
from ghimera.graph import MemoryGraphSink, ResearchGraph
from ghimera.graph_types import GraphEdge, GraphEvidence, GraphNode
from ghimera.ledger import Ledger
from ghimera.model_citations import citation_id
from ghimera.model_config import ModelServiceConfig
from ghimera.model_types import ModelCallEvidence
from ghimera.models import Document, Harvest, LedgerRow, ModelIdentity
from ghimera.refusals import GhimeraRefused, ModelCancelled, ModelFailure, RefusalCode
from ghimera.semantic_types import SemanticConfig, SemanticEntity, SemanticProposal, SemanticWindow

MENTION_REVISION = "ghimera-source-mention/1"


class SemanticExtractor(Protocol):
    @property
    def model(self) -> ModelIdentity: ...

    async def semantic_extract(
        self, intent: str, document: Document, start: int, end: int, policy: SemanticConfig
    ) -> SemanticProposal: ...


def bound_service(config: GhimeraConfig) -> ModelServiceConfig:
    policy, models = config.semantics, config.models
    if policy is None or models is None:
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    return models.service(policy.model_role)


def project(
    config: GhimeraConfig,
    graph: ResearchGraph,
    document_id: str,
    document: Document,
    proposal: SemanticProposal,
    start: int,
    end: int,
    omitted: int,
    intent: str,
) -> SemanticWindow:
    proposal = SemanticProposal.model_validate(proposal.model_dump())
    policy, graph_policy, call = config.semantics, config.graph, proposal.model_call
    if (
        policy is None
        or graph_policy is None
        or call is None
        or call.service != bound_service(config)
        or call.task != "semantic_extract"
        or call.prompt_revision != policy.effective_prompt_revision
        or call.outcome != "success"
        or len(proposal.mentions) > policy.max_mentions_per_window
        or len(proposal.relations) > policy.max_relations_per_window
        or not 0 <= start < end <= len(document.extracted.text)
        or end - start > policy.window_chars
        or omitted not in {0, len(document.extracted.text) - end}
    ):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    citation = native_citation(document, start, end)
    context = ContextSelector(
        call.service.context.model_copy(
            update={
                "max_documents": 1,
                "max_windows_per_document": 1,
            }
        )
    ).build(intent, (document,), required=(citation,))
    if (
        call.context_sha256 != context.content_digest()
        or call.selected_spans != ((citation.document_id, start, end),)
        or call.omitted_chars != len(document.extracted.text) - (end - start)
    ):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    reference = citation_id(citation)
    evidence = GraphEvidence(
        document_id=document_id,
        document_sha256=document.sha256,
        text_sha256=citation.text_sha256,
        start=start,
        end=end,
        quote=citation.quote,
    )
    revision = f"{policy.effective_prompt_revision}@{call.service.model_id}@{call.service.revision}"
    entities: list[SemanticEntity] = []
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    held: dict[str, GraphEdge] = {}
    for mention in proposal.mentions:
        if mention.role not in policy.entity_roles or mention.citation_id != reference:
            raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
        matches = tuple(re.finditer(re.escape(mention.surface), citation.quote))
        if mention.occurrence >= len(matches):
            raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
        match = matches[mention.occurrence]
        absolute_start, absolute_end = start + match.start(), start + match.end()
        # Document representation and exact native occurrence, not name alone.
        identity = f"{document_id}:{absolute_start}:{absolute_end}"
        node = graph.node(mention.role, identity, mention.surface, MENTION_REVISION)
        span = GraphEvidence(
            document_id=document_id,
            document_sha256=document.sha256,
            text_sha256=citation.text_sha256,
            start=absolute_start,
            end=absolute_end,
            quote=mention.surface,
        )
        entities.append(
            SemanticEntity(key=mention.key, node=node, evidence=span, confidence=mention.confidence)
        )
        edge = graph.edge(
            policy.mention_rule,
            document_id,
            node.id,
            revision,
            evidence=(span,),
            confidence=mention.confidence,
            claim_status="model_asserted",
            model_request_sha256=call.request_sha256,
        )
        if mention.confidence >= graph_policy.semantic_min_confidence:
            nodes[node.id], edges[edge.id] = node, edge
        else:
            held[edge.id] = edge
    by_key = {entity.key: entity for entity in entities}
    rules = {rule.name: rule for rule in graph_policy.relations}
    for relation in proposal.relations:
        source, target = by_key[relation.source], by_key[relation.target]
        rule = rules.get(relation.rule)
        if (
            relation.rule not in policy.relation_rules
            or rule is None
            or source.node.role not in rule.source_roles
            or target.node.role not in rule.target_roles
            or relation.citation_ids != (reference,)
        ):
            raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
        edge = graph.edge(
            relation.rule,
            source.node.id,
            target.node.id,
            revision,
            evidence=(evidence,),
            confidence=relation.confidence,
            claim_status="model_asserted",
            model_request_sha256=call.request_sha256,
            valid_from=relation.valid_from.isoformat() if relation.valid_from else None,
            valid_to=relation.valid_to.isoformat() if relation.valid_to else None,
        )
        if (
            relation.confidence >= graph_policy.semantic_min_confidence
            and source.node.id in nodes
            and target.node.id in nodes
        ):
            edges[edge.id] = edge
        else:
            held[edge.id] = edge
    return SemanticWindow(
        schema="ghimera.semantic-window/1",
        policy_digest=policy.content_digest(),
        source_url=document.url,
        document_sha256=document.sha256,
        text_sha256=citation.text_sha256,
        graph_document_id=document_id,
        start=start,
        end=end,
        omitted_chars=omitted,
        proposal=proposal,
        entities=tuple(entities),
        nodes=tuple(nodes.values()),
        edges=tuple(edges.values()),
        held_edges=tuple(held.values()),
    )


class SemanticStage:
    def __init__(self, config: GhimeraConfig, extractor: SemanticExtractor) -> None:
        config = GhimeraConfig.model_validate(config.model_dump())
        service = bound_service(config)
        if extractor.model != ModelIdentity(
            model_id=service.model_id, revision=service.revision, location="self_hosted"
        ):
            raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
        self._config, self._extractor = config, extractor

    async def extract(
        self,
        intent: str,
        document: Document,
        document_id: str,
        graph: ResearchGraph,
        budget: RunBudget,
        ledger: Ledger,
    ) -> None:
        policy = self._config.semantics
        graph_policy = self._config.graph
        if (
            policy is None
            or graph_policy is None
            or budget.config != self._config
            or graph.config_digest != graph_policy.content_digest()
        ):
            raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
        text = document.extracted.text
        for number, start in enumerate(range(0, len(text), policy.window_chars)):
            if number >= policy.max_windows_per_document:
                break
            end = min(start + policy.window_chars, len(text))
            last = number + 1 == policy.max_windows_per_document or end == len(text)
            budget.reserve_semantic()
            call: ModelCallEvidence | None = None
            try:
                async with asyncio.timeout(budget.remaining_seconds):
                    proposal = await self._extractor.semantic_extract(
                        intent, document, start, end, policy
                    )
                call = proposal.model_call
                proposal = SemanticProposal.model_validate(proposal.model_dump())
                observation = project(
                    self._config,
                    graph,
                    document_id,
                    document,
                    proposal,
                    start,
                    end,
                    len(text) - end if last else 0,
                    intent,
                )
                pending = asyncio.create_task(
                    graph.append(nodes=observation.nodes, edges=observation.edges)
                )
                cancelled = False
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    cancelled = True
                    await pending
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="semantic",
                        url=document.url,
                        model=self._extractor.model,
                        model_call=call,
                        reason="source_local_model_assertions",
                        semantic_window=observation,
                    )
                )
                if cancelled:
                    raise asyncio.CancelledError
            except ModelCancelled as exc:
                self._failure(
                    ledger, document.url, RefusalCode.SEMANTIC_EXTRACTION_FAILED, exc.model_call
                )
                raise
            except (GhimeraRefused, TimeoutError, ValidationError) as exc:
                code = (
                    exc.code
                    if isinstance(exc, GhimeraRefused)
                    else RefusalCode.SEMANTIC_EXTRACTION_FAILED
                    if isinstance(exc, ValidationError)
                    else RefusalCode.BUDGET_EXHAUSTED
                )
                if isinstance(exc, ModelFailure):
                    call = exc.model_call
                self._failure(ledger, document.url, code, call)
                raise GhimeraRefused(code) from None

    def _failure(
        self, ledger: Ledger, url: str, code: RefusalCode, call: ModelCallEvidence | None
    ) -> None:
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event="semantic",
                url=url,
                refusal=code,
                model=self._extractor.model,
                model_call=call,
                reason="semantic_window_refused",
            )
        )


def validate_rows(config: GhimeraConfig, ledger: tuple[LedgerRow, ...]) -> tuple[LedgerRow, ...]:
    rows = tuple(row for row in ledger if row.event == "semantic")
    if not rows:
        return rows
    policy = config.semantics
    if policy is None or len(rows) > policy.max_calls_per_run:
        raise ValueError("semantic calls exceed their configured run allowance")
    service = bound_service(config)
    identity = ModelIdentity(
        model_id=service.model_id, revision=service.revision, location="self_hosted"
    )
    for row in rows:
        if row.model != identity:
            raise ValueError("semantic calls require the bound self-hosted model")
        if row.model_call is not None and (
            row.model_call.service != service
            or row.model_call.task != "semantic_extract"
            or row.model_call.prompt_revision != policy.effective_prompt_revision
        ):
            raise ValueError("semantic call provenance differs from its configured service")
        if (
            row.semantic_window is not None
            and row.semantic_window.policy_digest != policy.content_digest()
        ):
            raise ValueError("semantic window must bind its effective extraction policy")
    return rows


def validate_harvest(harvest: Harvest) -> None:
    config, snapshot = harvest.receipt.effective_config, harvest.graph
    policy = config.semantics
    rows = validate_rows(config, harvest.ledger)
    marked = (
        tuple(edge for edge in snapshot.edges if edge.claim_status is not None) if snapshot else ()
    )
    if policy is None and not rows and not marked:
        return
    if (
        policy is None
        or config.graph is None
        or snapshot is None
        or len(rows) > policy.max_calls_per_run
    ):
        raise ValueError("semantic observations require their bounded configured graph")
    graph = ResearchGraph(config.graph, snapshot.run_id, MemoryGraphSink())
    sources = {(item.url, item.sha256): item for item in harvest.source_documents}
    nodes = {node.id: node for node in snapshot.nodes}
    edges = {edge.id: edge for edge in snapshot.edges}
    projected: set[str] = set()
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        observation = row.semantic_window
        if observation is None:
            continue
        key = (observation.source_url, observation.document_sha256)
        document = sources.get(key)
        node = nodes.get(observation.graph_document_id)
        counts[key] = counts.get(key, 0) + 1
        if (
            document is None
            or node is None
            or node.role != "document"
            or node.source_url != document.url
            or node.content_sha256 != document.sha256
            or node.text != document.extracted.text
            or counts[key] > policy.max_windows_per_document
            or observation.start != (counts[key] - 1) * policy.window_chars
            or observation.end
            != min(observation.start + policy.window_chars, len(document.extracted.text))
            or observation.omitted_chars
            != (
                len(document.extracted.text) - observation.end
                if counts[key] == policy.max_windows_per_document
                else 0
            )
        ):
            raise ValueError(
                "semantic windows must bind their retained native source representation"
            )
        expected = project(
            config,
            graph,
            node.id,
            document,
            observation.proposal,
            observation.start,
            observation.end,
            observation.omitted_chars,
            harvest.goal.text,
        )
        if (
            expected != observation
            or any(nodes.get(item.id) != item for item in observation.nodes)
            or any(edges.get(item.id) != item for item in observation.edges)
        ):
            raise ValueError(
                "semantic archive projections must match native observations and durable graph"
            )
        projected.update(item.id for item in observation.edges)
    for key, document in sources.items():
        expected_count = min(
            policy.max_windows_per_document,
            (len(document.extracted.text) + policy.window_chars - 1) // policy.window_chars,
        )
        if counts.get(key, 0) != expected_count and not any(
            row.url == document.url
            and row.refusal is not None
            and row.event in {"semantic", "refusal"}
            for row in harvest.ledger
        ):
            raise ValueError("missing semantic windows require an explicit source refusal")
    if {edge.id for edge in marked} != projected:
        raise ValueError("every model-asserted graph edge requires its semantic call observation")
