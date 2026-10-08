"""Sequential native extraction windows and deterministic graph projection.

The model proposes observations. This owner checks native spans, ontology and
policy before the existing graph owner acknowledges a transaction. No alias
merge, corroboration upgrade, model fallback or graph store lives here.
"""

import asyncio
import hashlib
import re
from functools import partial
from typing import Literal, Protocol, runtime_checkable

from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.graph import MemoryGraphSink, ResearchGraph
from ghimera.graph_types import GraphEdge, GraphEvidence, GraphNode
from ghimera.ledger import Ledger
from ghimera.model_citations import citation_id
from ghimera.model_config import ModelServiceConfig
from ghimera.model_types import ModelCallEvidence
from ghimera.model_work import ModelInvocation, port_input, record_output
from ghimera.models import Document, Harvest, LedgerRow, ModelIdentity
from ghimera.refusals import GhimeraRefused, ModelCancelled, ModelFailure, RefusalCode
from ghimera.semantic_batching import assemble_review, review_selections
from ghimera.semantic_recovery import SemanticRecoveryStopped, failed_window
from ghimera.semantic_types import (
    BatchedSemanticReview,
    ExclusionReason,
    GroundedSemanticReview,
    MentionExclusion,
    RelationExclusion,
    ReviewPart,
    ReviewSelection,
    SemanticConfig,
    SemanticEntity,
    SemanticProposal,
    SemanticRefusal,
    SemanticReview,
    SemanticWindow,
    restore_review,
    review_observations,
    review_profile_matches,
)
from ghimera.semantic_verification import (
    review_service,
    validate_proposal,
    validate_review,
    validate_review_part,
)

MENTION_REVISION = "ghimera-source-mention/1"


class SemanticExtractor(Protocol):
    @property
    def model(self) -> ModelIdentity: ...

    async def semantic_extract(
        self, intent: str, document: Document, start: int, end: int, policy: SemanticConfig
    ) -> SemanticProposal: ...


@runtime_checkable
class SemanticReviewPreflight(Protocol):
    """Optional local preparation capability; never a source/model request."""

    async def prepare_semantic_review(
        self,
        intent: str,
        document: Document,
        start: int,
        end: int,
        policy: SemanticConfig,
        proposal: SemanticProposal,
    ) -> None: ...


class SemanticReviewer(Protocol):
    @property
    def model(self) -> ModelIdentity: ...

    async def semantic_review(
        self,
        intent: str,
        document: Document,
        start: int,
        end: int,
        policy: SemanticConfig,
        proposal: SemanticProposal,
    ) -> SemanticReview: ...


@runtime_checkable
class PartitionedSemanticReviewer(Protocol):
    async def semantic_review_part(
        self,
        intent: str,
        document: Document,
        start: int,
        end: int,
        policy: SemanticConfig,
        proposal: SemanticProposal,
        selection: ReviewSelection,
    ) -> GroundedSemanticReview: ...


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
    review: SemanticReview | None = None,
) -> SemanticWindow:
    proposal = SemanticProposal.model_validate(proposal.model_dump())
    policy, graph_policy, call = config.semantics, config.graph, proposal.model_call
    if (
        policy is None
        or graph_policy is None
        or call is None
        or omitted not in {0, len(document.extracted.text) - end}
    ):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    if (policy.verification is not None) != (review is not None):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    if review is not None:
        review = restore_review(review)
        validate_review(config, proposal, review, document, start, end, intent)
    citation = validate_proposal(config, proposal, document, start, end, intent)
    reference = citation_id(citation)
    reading = document.extracted.pdf_transcription
    graph_reading = reading.graph_reading() if reading is not None else None
    evidence = GraphEvidence.from_reading(
        document_id,
        document.sha256,
        document.extracted.text,
        start,
        end,
        pdf_reading=graph_reading,
    )
    revision = f"{policy.effective_prompt_revision}@{call.service.model_id}@{call.service.revision}"
    entities: list[SemanticEntity] = []
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    held: dict[str, GraphEdge] = {}
    excluded_mentions: list[MentionExclusion] = []
    excluded_relations: list[RelationExclusion] = []
    mention_reviews = {item.key: item.verdict for item in review.mentions} if review else {}
    relation_reviews = {item.index: item.verdict for item in review.relations} if review else {}
    for mention in proposal.mentions:
        matches = tuple(re.finditer(re.escape(mention.surface), citation.quote))
        reason: ExclusionReason | None = None
        if mention.role not in policy.entity_roles:
            reason = "unknown_role"
        elif mention.citation_id != reference:
            reason = "citation_mismatch"
        elif mention.occurrence >= len(matches):
            reason = "native_span_missing"
        elif mention_reviews.get(mention.key) == "unsupported":
            reason = "review_unsupported"
        elif mention_reviews.get(mention.key) == "ambiguous":
            reason = "review_ambiguous"
        if reason is not None:
            if review is None:
                raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
            excluded_mentions.append(MentionExclusion(key=mention.key, reason=reason))
            continue
        match = matches[mention.occurrence]
        absolute_start, absolute_end = start + match.start(), start + match.end()
        # Document representation and exact native occurrence, not name alone.
        identity = f"{document_id}:{absolute_start}:{absolute_end}"
        node = graph.node(mention.role, identity, mention.surface, MENTION_REVISION)
        span = GraphEvidence.from_reading(
            document_id,
            document.sha256,
            document.extracted.text,
            absolute_start,
            absolute_end,
            pdf_reading=graph_reading,
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
    for index, relation in enumerate(proposal.relations):
        source, target = by_key.get(relation.source), by_key.get(relation.target)
        rule = rules.get(relation.rule)
        reason = None
        if source is None or target is None:
            reason = "endpoint_quarantined"
        elif relation.rule not in policy.relation_rules or rule is None:
            reason = "unknown_relation"
        elif source.node.role not in rule.source_roles or target.node.role not in rule.target_roles:
            reason = "endpoint_role_mismatch"
        elif relation.citation_ids != (reference,):
            reason = "citation_mismatch"
        elif relation_reviews.get(index) == "unsupported":
            reason = "review_unsupported"
        elif relation_reviews.get(index) == "ambiguous":
            reason = "review_ambiguous"
        if reason is not None:
            if review is None:
                raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
            excluded_relations.append(RelationExclusion(index=index, reason=reason))
            continue
        if source is None or target is None:
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
        schema="ghimera.semantic-window/2" if review else "ghimera.semantic-window/1",
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
        review=review,
        excluded_mentions=tuple(excluded_mentions),
        excluded_relations=tuple(excluded_relations),
    )


class SemanticStage:
    def __init__(
        self,
        config: GhimeraConfig,
        extractor: SemanticExtractor,
        *,
        reviewer: SemanticReviewer | None = None,
    ) -> None:
        config = GhimeraConfig.model_validate(config.model_dump())
        service = bound_service(config)
        if extractor.model != ModelIdentity(
            model_id=service.model_id, revision=service.revision, location="self_hosted"
        ):
            raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
        if config.semantics is None:
            raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
        if (config.semantics.verification is not None) != (reviewer is not None):
            raise ValueError("semantic verification policy and reviewer must be supplied together")
        if reviewer is not None:
            independent = review_service(config)
            if reviewer.model != ModelIdentity(
                model_id=independent.model_id, revision=independent.revision, location="self_hosted"
            ):
                raise ValueError("semantic reviewer must bind its configured model")
            verification = config.semantics.verification
            if (
                verification is not None
                and verification.schema_version == "ghimera.semantic-verification/4"
                and not isinstance(reviewer, PartitionedSemanticReviewer)
            ):
                raise ValueError("version-4 semantic review needs its partitioned reviewer port")
        self._config, self._extractor, self._reviewer = config, extractor, reviewer

    async def extract(
        self,
        intent: str,
        document: Document,
        document_id: str,
        graph: ResearchGraph,
        budget: RunBudget,
        ledger: Ledger,
        *,
        record_terminal_refusal: bool = False,
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
            invocation = ModelInvocation(
                budget,
                ledger,
                phase="semantic_extract",
                model=self._extractor.model,
                url=document.url,
                request=port_input(budget, document, policy, intent=intent, start=start, end=end),
                reserve=budget.reserve_semantic,
            )
            call: ModelCallEvidence | None = None
            proposal: SemanticProposal | None = None
            phase: Literal["extract", "review", "projection"] = "extract"
            first_review_sequence = ledger.next_sequence
            committed = False
            try:
                async with asyncio.timeout(budget.remaining_seconds):
                    extracted = await invocation.invoke(
                        partial(
                            self._extractor.semantic_extract, intent, document, start, end, policy
                        ),
                        record_output,
                    )
                call = extracted.model_call
                validated = SemanticProposal.model_validate(extracted.model_dump())
                validate_proposal(self._config, validated, document, start, end, intent)
                proposal = validated
                phase = "review"
                review = await self._review(
                    intent, document, start, end, policy, proposal, budget, ledger
                )
                phase = "projection"
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
                    review,
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
                committed = True
                if cancelled:
                    raise asyncio.CancelledError
            except ModelCancelled as exc:
                self._failure(
                    ledger, document.url, RefusalCode.SEMANTIC_EXTRACTION_FAILED, exc.model_call
                )
                raise
            except asyncio.CancelledError:
                if not committed:
                    self._failure(
                        ledger, document.url, RefusalCode.SEMANTIC_EXTRACTION_FAILED, call
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
                refusal = failed_window(
                    self._config,
                    document,
                    document_id,
                    start,
                    end,
                    len(text) - end if last else 0,
                    phase,
                    proposal,
                    tuple(
                        row.sequence
                        for row in ledger.snapshot()
                        if row.event == "semantic_review"
                        and row.sequence >= first_review_sequence
                        and row.url == document.url
                    ),
                    ledger.snapshot(),
                    code,
                    allow_continue=not committed,
                    record_terminal=record_terminal_refusal,
                )
                self._failure(ledger, document.url, code, call, refusal)
                if refusal is not None and refusal.continued:
                    continue
                if refusal is not None:
                    raise SemanticRecoveryStopped(code) from None
                raise GhimeraRefused(code) from None

    async def _review(
        self,
        intent: str,
        document: Document,
        start: int,
        end: int,
        policy: SemanticConfig,
        proposal: SemanticProposal,
        budget: RunBudget,
        ledger: Ledger,
    ) -> SemanticReview | None:
        if self._reviewer is None:
            return None
        verification = policy.verification
        if (
            verification is not None
            and verification.schema_version == "ghimera.semantic-verification/4"
        ):
            selections = review_selections(verification, proposal)
            if len(selections) > min(
                verification.max_calls_per_run - budget.semantic_review_calls,
                budget.config.judge_budget - budget.judge_calls,
            ):
                raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            if isinstance(self._reviewer, SemanticReviewPreflight):
                await self._reviewer.prepare_semantic_review(
                    intent, document, start, end, policy, proposal
                )
            parts: list[ReviewPart] = []
            for selection in selections:
                review = await self._review_once(
                    intent, document, start, end, policy, proposal, budget, ledger, selection
                )
                if not isinstance(review, GroundedSemanticReview):
                    raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
                parts.append(ReviewPart(selection=selection, review=review))
            return assemble_review(proposal, tuple(parts))
        if isinstance(self._reviewer, SemanticReviewPreflight):
            await self._reviewer.prepare_semantic_review(
                intent, document, start, end, policy, proposal
            )
        return await self._review_once(
            intent, document, start, end, policy, proposal, budget, ledger
        )

    async def _review_once(
        self,
        intent: str,
        document: Document,
        start: int,
        end: int,
        policy: SemanticConfig,
        proposal: SemanticProposal,
        budget: RunBudget,
        ledger: Ledger,
        selection: ReviewSelection | None = None,
    ) -> SemanticReview:
        if self._reviewer is None:
            raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
        reviewer = self._reviewer

        async def review_call() -> SemanticReview:
            if selection is not None:
                if not isinstance(reviewer, PartitionedSemanticReviewer):
                    raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
                return await reviewer.semantic_review_part(
                    intent, document, start, end, policy, proposal, selection
                )
            return await reviewer.semantic_review(intent, document, start, end, policy, proposal)

        invocation = ModelInvocation(
            budget,
            ledger,
            phase="semantic_review",
            model=reviewer.model,
            url=document.url,
            request=port_input(
                budget,
                document,
                policy,
                proposal,
                *((selection,) if selection is not None else ()),
                intent=intent,
                start=start,
                end=end,
            ),
            reserve=budget.reserve_semantic_review,
        )
        call: ModelCallEvidence | None = None
        review: SemanticReview | None = None
        refusal: RefusalCode | None = None
        try:
            async with asyncio.timeout(budget.remaining_seconds):
                review = await invocation.invoke(review_call, record_output)
            call = review.model_call
            review = restore_review(review)
            if selection is not None:
                if not isinstance(review, GroundedSemanticReview):
                    raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
                validate_review_part(
                    self._config, proposal, review, selection, document, start, end, intent
                )
            else:
                validate_review(self._config, proposal, review, document, start, end, intent)
            return review
        except ModelCancelled as exc:
            call, refusal = exc.model_call, RefusalCode.SEMANTIC_EXTRACTION_FAILED
            raise asyncio.CancelledError from None
        except asyncio.CancelledError:
            refusal = RefusalCode.SEMANTIC_EXTRACTION_FAILED
            raise
        except (GhimeraRefused, ValidationError, TimeoutError) as exc:
            refusal = (
                exc.code
                if isinstance(exc, GhimeraRefused)
                else RefusalCode.SEMANTIC_EXTRACTION_FAILED
            )
            if isinstance(exc, ModelFailure):
                call = exc.model_call
            # Do not replace the extractor call with reviewer provenance.
            raise GhimeraRefused(refusal) from None
        finally:
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="semantic_review",
                    url=document.url,
                    model=self._reviewer.model,
                    model_call=call,
                    semantic_review=review if refusal is None else None,
                    semantic_review_selection=selection,
                    refusal=refusal,
                    reason="independent_source_check"
                    if refusal is None
                    else "semantic_review_refused",
                )
            )

    def _failure(
        self,
        ledger: Ledger,
        url: str,
        code: RefusalCode,
        call: ModelCallEvidence | None,
        observation: SemanticRefusal | None = None,
    ) -> None:
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event="semantic",
                url=url,
                refusal=code,
                model=self._extractor.model,
                model_call=call,
                semantic_refusal=observation,
                reason="semantic_window_refused",
            )
        )


def validate_rows(config: GhimeraConfig, ledger: tuple[LedgerRow, ...]) -> tuple[LedgerRow, ...]:
    rows = tuple(row for row in ledger if row.event == "semantic")
    reviews = tuple(row for row in ledger if row.event == "semantic_review")
    policy = config.semantics
    if reviews:
        if (
            policy is None
            or policy.verification is None
            or len(reviews) > policy.verification.max_calls_per_run
        ):
            raise ValueError("semantic reviews exceed their configured allowance")
        service = review_service(config)
        identity = ModelIdentity(
            model_id=service.model_id, revision=service.revision, location="self_hosted"
        )
        for row in reviews:
            if (
                row.model != identity
                or row.url is None
                or (policy.verification.schema_version == "ghimera.semantic-verification/4")
                != (row.semantic_review_selection is not None)
                or (
                    row.semantic_review is not None
                    and not review_profile_matches(policy.verification, row.semantic_review)
                )
                or (
                    row.model_call is not None
                    and (
                        row.model_call.service != service
                        or row.model_call.task != "semantic_review"
                        or row.model_call.prompt_revision
                        != policy.verification.effective_prompt_revision
                    )
                )
            ):
                raise ValueError("semantic review must bind its independent configured service")
    if not rows:
        if reviews:
            raise ValueError("semantic review requires its extraction observation")
        return rows
    if policy is None or len(rows) > policy.max_calls_per_run:
        raise ValueError("semantic calls exceed their configured run allowance")
    service = bound_service(config)
    identity = ModelIdentity(
        model_id=service.model_id, revision=service.revision, location="self_hosted"
    )
    used_reviews: set[int] = set()
    continued_failures = 0
    terminal_refusal: int | None = None
    for row in rows:
        if terminal_refusal is not None:
            raise ValueError("semantic work cannot continue beyond a terminal recovery refusal")
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
        window = row.semantic_window
        failed = row.semantic_refusal
        if failed is not None:
            failure_policy = policy.failure
            if failed.policy_digest != policy.content_digest():
                raise ValueError("semantic refusal differs from its extraction/failure policy")
            if failed.proposal is not None and (
                failed.proposal.model_call != row.model_call
                or failed.proposal.model_call is None
                or failed.proposal.model_call.outcome != "success"
            ):
                raise ValueError("failed review retains its unchanged successful extractor call")
            expected_reviews = tuple(
                item.sequence
                for item in reviews
                if item.sequence < row.sequence and item.sequence not in used_reviews
            )
            if failed.review_sequences != expected_reviews:
                raise ValueError("failed review requires each exact earlier unused review record")
            for sequence in failed.review_sequences:
                observed_review = next(item for item in reviews if item.sequence == sequence)
                if observed_review.url != row.url:
                    raise ValueError("failed review cannot borrow a different source observation")
                used_reviews.add(sequence)
            if failed.continued:
                continued_failures += 1
                if (
                    failure_policy is None
                    or failed.phase not in {"extract", "review"}
                    or row.refusal is None
                    or row.refusal.value not in failure_policy.allowed_refusals
                    or continued_failures > failure_policy.max_failed_windows_per_run
                ):
                    raise ValueError("semantic continuation exceeds its explicit failure allowance")
            else:
                terminal_refusal = row.sequence
        if window is not None:
            if (policy.verification is not None) != (window.review is not None):
                raise ValueError("semantic window cannot omit its required independent check")
            if window.review is not None:
                observations = review_observations(window.review)
                selections = (
                    tuple(part.selection for part in window.review.parts)
                    if isinstance(window.review, BatchedSemanticReview)
                    else (None,)
                )
                for observation, selection in zip(observations, selections, strict=True):
                    matches = tuple(
                        item
                        for item in reviews
                        if item.sequence < row.sequence
                        and item.url == row.url
                        and item.semantic_review == observation
                        and item.semantic_review_selection == selection
                    )
                    if len(matches) != 1 or matches[0].sequence in used_reviews:
                        raise ValueError(
                            "reviewed projection requires each exact earlier review observation"
                        )
                    used_reviews.add(matches[0].sequence)
    if terminal_refusal is not None and any(item.sequence > terminal_refusal for item in reviews):
        raise ValueError("semantic reviews cannot continue beyond a terminal recovery refusal")
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
    sources = harvest.graph_source_documents
    nodes = {node.id: node for node in snapshot.nodes}
    edges = {edge.id: edge for edge in snapshot.edges}
    projected: set[str] = set()
    counts: dict[str, int] = {}

    def readings(node: GraphNode) -> tuple[Document, ...]:
        retained = node.retained_source
        candidates = (
            tuple(item.document for item in harvest.retained_sources if item.origin == retained)
            if retained is not None
            else harvest.source_documents
        )
        return tuple(
            item
            for item in candidates
            if (
                node.source_url == item.url
                and node.content_sha256 == item.sha256
                and node.text == item.extracted.text
                and node.pdf_reading
                == (
                    item.extracted.pdf_transcription.graph_reading()
                    if item.extracted.pdf_transcription is not None
                    else None
                )
            )
        )

    for row in rows:
        observation = row.semantic_window or row.semantic_refusal
        if observation is None:
            continue
        node = nodes.get(observation.graph_document_id)
        matches = readings(node) if node is not None else ()
        document = matches[0] if matches else None
        key = observation.graph_document_id
        counts[key] = counts.get(key, 0) + 1
        if (
            document is None
            or node is None
            or node.role != "document"
            or observation.document_sha256 != document.sha256
            or observation.source_url != document.url
            or row.url != document.url
            or node.source_url != document.url
            or node.content_sha256 != document.sha256
            or node.text != document.extracted.text
            or node.pdf_reading
            != (
                document.extracted.pdf_transcription.graph_reading()
                if document.extracted.pdf_transcription is not None
                else None
            )
            or observation.text_sha256
            != hashlib.sha256(document.extracted.text.encode()).hexdigest()
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
        if row.semantic_refusal is not None:
            failed = row.semantic_refusal
            if failed.proposal is not None:
                validate_proposal(
                    config, failed.proposal, document, failed.start, failed.end, harvest.goal.text
                )
            continue
        # Narrow after handling refused windows; only accepted windows project.
        observation = row.semantic_window
        if observation is None:
            raise ValueError("semantic attempt requires its accepted or refused native window")
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
            observation.review,
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
    for document in sources:
        expected_count = min(
            policy.max_windows_per_document,
            (len(document.extracted.text) + policy.window_chars - 1) // policy.window_chars,
        )
        matching_nodes = tuple(
            node
            for node in nodes.values()
            if node.role == "document" and document in readings(node)
        )
        retained_nodes = tuple(node for node in matching_nodes if node.retained_source is not None)
        for node in retained_nodes:
            if counts.get(node.id, 0) != expected_count and not any(
                row.refusal is not None
                and (
                    (
                        row.semantic_refusal is not None
                        and row.semantic_refusal.graph_document_id == node.id
                    )
                    or row.retained_failure == node.retained_source
                )
                for row in harvest.ledger
            ):
                raise ValueError(
                    "retained graph originals require their own semantic coverage or refusal"
                )
        if not any(counts.get(node.id, 0) == expected_count for node in matching_nodes) and not any(
            row.url == document.url
            and row.refusal is not None
            and row.event in {"semantic", "refusal"}
            for row in harvest.ledger
        ):
            raise ValueError("missing semantic windows require an explicit source refusal")
    if {edge.id for edge in marked} != projected:
        raise ValueError("every model-asserted graph edge requires its semantic call observation")
