"""Bounded native identity work; models propose/review, graph owner acknowledges.

No names are canonicalized and no source-local node or relationship is replaced.
The original journal owns call reservations and returns; the graph owns decisions.
"""

import asyncio
import hashlib
from collections import Counter
from collections.abc import Awaitable, Callable
from itertools import combinations
from time import monotonic
from typing import Literal, Protocol, TypeVar

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.graph import ResearchGraph
from ghimera.graph_types import (
    GraphBatch,
    GraphCheckpoint,
    GraphEvidence,
    GraphModelIdentityDecision,
    GraphSnapshot,
    IdentityDecision,
)
from ghimera.identity_automation_types import (
    IDENTITY_PROPOSAL_REVISION,
    IDENTITY_REVIEW_REVISION,
    IdentityAutomationConfig,
    IdentityHistory,
    IdentityMention,
    IdentityObservation,
    IdentityPair,
    IdentityProposal,
    IdentityProposalRequest,
    IdentityReview,
    IdentityReviewRequest,
)
from ghimera.ledger import Ledger
from ghimera.model_types import IdentityCallEvidence
from ghimera.model_work import ModelInvocation, port_input, record_output, uncertain_model_sequences
from ghimera.models import LedgerRow, ModelIdentity
from ghimera.refusals import GhimeraRefused, ModelCancelled, ModelFailure, RefusalCode

T = TypeVar("T", IdentityProposal, IdentityReview)


class IdentityProposer(Protocol):
    @property
    def model(self) -> ModelIdentity: ...

    async def identity_propose(self, request: IdentityProposalRequest) -> IdentityProposal: ...


class IdentityReviewer(Protocol):
    @property
    def model(self) -> ModelIdentity: ...

    async def identity_review(self, request: IdentityReviewRequest) -> IdentityReview: ...


def history_digest(history: tuple[IdentityDecision, ...]) -> str:
    return hashlib.sha256(
        b"[" + b",".join(item.canonical_bytes() for item in history) + b"]"
    ).hexdigest()


def pair_for(left: str, right: str) -> IdentityPair:
    members = tuple(sorted((left, right)))
    return IdentityPair(
        id=hashlib.sha256((members[0] + ":" + members[1]).encode()).hexdigest(),
        members=(members[0], members[1]),
    )


def request_for(
    config: GhimeraConfig,
    intent: str,
    rows: tuple[LedgerRow, ...],
    snapshot: GraphSnapshot,
) -> IdentityProposalRequest:
    policy = config.identity_automation
    if policy is None:
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    nodes = {node.id: node for node in snapshot.nodes}
    population: dict[str, IdentityMention] = {}
    for row in rows:
        window = row.semantic_window
        if window is None:
            continue
        doc = nodes.get(window.graph_document_id)
        if doc is None or doc.text is None or doc.content_sha256 is None:
            raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
        context = GraphEvidence.from_reading(
            doc.id,
            doc.content_sha256,
            doc.text,
            window.start,
            window.end,
            pdf_reading=doc.pdf_reading,
        )
        admitted = {node.id for node in window.nodes}
        for entity in window.entities:
            if entity.node.id in admitted and entity.node.role in policy.roles:
                population[entity.node.id] = IdentityMention(
                    node_id=entity.node.id,
                    role=entity.node.role,
                    surface=entity.node.label,
                    evidence=entity.evidence,
                    context=context,
                )
    all_mentions = tuple(population.values())
    population_hash = hashlib.sha256(
        b"[" + b",".join(m.canonical_bytes() for m in all_mentions) + b"]"
    ).hexdigest()
    inspected = {
        pair.id
        for row in rows
        if row.identity_observation is not None
        for pair in row.identity_observation.request.pairs
    }
    counts = Counter(m.role for m in all_mentions)
    prior_pairs = {
        pair.id: pair
        for row in rows
        if row.identity_observation is not None
        for pair in row.identity_observation.request.pairs
        if all(m in population for m in pair.members)
        and population[pair.members[0]].role == population[pair.members[1]].role
    }
    all_possible = sum(n * (n - 1) // 2 for n in counts.values()) - len(prior_pairs)
    degree = Counter(member for pair in prior_pairs.values() for member in pair.members)
    selected: list[IdentityMention] = []
    chars = 0
    for mention in reversed(all_mentions):
        # One role per bounded pass. Skip completed mentions so truncation can
        # progress to older uninspected pairs without an unbounded pair scan.
        if degree[mention.node_id] >= counts[mention.role] - 1:
            continue
        if selected and (
            mention.role != selected[0].role
            or not any(
                pair_for(mention.node_id, prior.node_id).id not in inspected for prior in selected
            )
        ):
            continue
        cost = len(mention.evidence.quote) + len(mention.context.quote)
        if len(selected) < policy.max_mentions and chars + cost <= policy.max_evidence_chars:
            selected.append(mention)
            chars += cost
    while True:
        possible = tuple(
            pair_for(left.node_id, right.node_id)
            for left, right in combinations(selected, 2)
            if left.role == right.role and pair_for(left.node_id, right.node_id).id not in inspected
        )
        request = IdentityProposalRequest(
            schema="ghimera.identity-proposal-request/1",
            intent=intent,
            run_id=snapshot.run_id,
            policy_digest=policy.content_digest(),
            population_digest=population_hash,
            history_digest=history_digest(snapshot.identity_decisions),
            history=snapshot.identity_decisions,
            checkpoint=snapshot.checkpoint,
            mentions=tuple(selected),
            pairs=possible[: policy.max_pairs_per_pass],
            omitted_mentions=len(all_mentions) - len(selected),
            omitted_pairs=all_possible - min(len(possible), policy.max_pairs_per_pass),
        )
        if len(request.model_dump_json()) <= policy.max_context_chars:
            return request
        if not selected:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        selected.pop()


def validate_request(config: GhimeraConfig, request: IdentityProposalRequest) -> None:
    policy = config.identity_automation
    if (
        policy is None
        or request.policy_digest != policy.content_digest()
        or (
            len(request.mentions) > policy.max_mentions
            or len(request.pairs) > policy.max_pairs_per_pass
            or len(request.model_dump_json()) > policy.max_context_chars
            or sum(len(m.evidence.quote) + len(m.context.quote) for m in request.mentions)
            > policy.max_evidence_chars
            or request.history_digest != history_digest(request.history)
        )
    ):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    mentions = {m.node_id: m for m in request.mentions}
    if len(mentions) != len(request.mentions) or len({p.id for p in request.pairs}) != len(
        request.pairs
    ):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    for mention in request.mentions:
        if (
            mention.role not in policy.roles
            or mention.evidence.quote != mention.surface
            or mention.evidence.document_id != mention.context.document_id
            or mention.evidence.document_sha256 != mention.context.document_sha256
            or mention.evidence.text_sha256 != mention.context.text_sha256
            or not mention.context.start
            <= mention.evidence.start
            < mention.evidence.end
            <= mention.context.end
            or mention.context.quote[
                mention.evidence.start - mention.context.start : mention.evidence.end
                - mention.context.start
            ]
            != mention.surface
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    for pair in request.pairs:
        if (
            not set(pair.members) <= mentions.keys()
            or pair != pair_for(*pair.members)
            or (mentions[pair.members[0]].role != mentions[pair.members[1]].role)
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)


def validate_call(
    config: GhimeraConfig,
    call: IdentityCallEvidence | None,
    phase: Literal["identity_propose", "identity_review"],
) -> None:
    policy, models = config.identity_automation, config.models
    if policy is None or models is None or call is None:
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    role = policy.proposer_role if phase == "identity_propose" else policy.reviewer_role
    revision = (
        IDENTITY_PROPOSAL_REVISION if phase == "identity_propose" else IDENTITY_REVIEW_REVISION
    )
    if (
        call.task != phase
        or call.service != models.service(role)
        or call.prompt_revision != revision
        or call.outcome != "success"
    ):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)


def validate_witnesses(
    policy: IdentityAutomationConfig,
    request: IdentityProposalRequest,
    pair: IdentityPair,
    evidence: tuple[GraphEvidence, ...],
    reason: str,
) -> None:
    members = tuple(m for m in request.mentions if m.node_id in pair.members)
    offered = {e.content_digest(): e for m in members for e in (m.evidence, m.context)}
    if (
        not reason.strip()
        or len(reason) > policy.max_reason_chars
        or len({e.content_digest() for e in evidence}) != len(evidence)
        or any(offered.get(e.content_digest()) != e for e in evidence)
        or not evidence
        or any(
            not any(
                e.document_id == m.evidence.document_id and m.surface in e.quote for e in evidence
            )
            for m in members
        )
    ):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)


def validate_proposal(
    config: GhimeraConfig, request: IdentityProposalRequest, proposal: IdentityProposal
) -> None:
    validate_request(config, request)
    policy = config.identity_automation
    if policy is None:
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    validate_call(config, proposal.model_call, "identity_propose")
    pairs = {p.id: p for p in request.pairs}
    if proposal.request_digest != request.content_digest() or (
        len(proposal.hypotheses) != len(pairs)
        or {h.pair_id for h in proposal.hypotheses} != pairs.keys()
    ):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    for hypothesis in proposal.hypotheses:
        if hypothesis.operation != "unresolved":
            validate_witnesses(
                policy, request, pairs[hypothesis.pair_id], hypothesis.evidence, hypothesis.reason
            )
            for boundary in (hypothesis.valid_from, hypothesis.valid_to):
                if boundary is not None and not any(
                    boundary.isoformat() in e.quote for e in hypothesis.evidence
                ):
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        elif not hypothesis.reason.strip() or len(hypothesis.reason) > policy.max_reason_chars:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)


def decisions_for(
    config: GhimeraConfig,
    request: IdentityProposalRequest,
    proposal: IdentityProposal,
    review: IdentityReview,
) -> tuple[GraphModelIdentityDecision, ...]:
    validate_proposal(config, request, proposal)
    policy = config.identity_automation
    if policy is None or config.graph is None or config.graph.identity_resolution is None:
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    validate_call(config, review.model_call, "identity_review")
    if review.proposal_digest != proposal.content_digest() or (
        len(review.assessments) != len(proposal.hypotheses)
        or {a.pair_id for a in review.assessments} != {h.pair_id for h in proposal.hypotheses}
    ):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    pairs = {p.id: p for p in request.pairs}
    hypotheses = {h.pair_id: h for h in proposal.hypotheses}
    result = []
    for assessment in review.assessments:
        hypothesis = hypotheses[assessment.pair_id]
        if not assessment.reason.strip() or len(assessment.reason) > policy.max_reason_chars:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if not assessment.supported or hypothesis.operation == "unresolved":
            continue
        validate_witnesses(
            policy, request, pairs[assessment.pair_id], assessment.evidence, assessment.reason
        )
        for boundary in (hypothesis.valid_from, hypothesis.valid_to):
            if boundary is not None and not any(
                boundary.isoformat() in e.quote for e in assessment.evidence
            ):
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if proposal.model_call is None or review.model_call is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        decision = GraphModelIdentityDecision(
            schema="ghimera.identity-decision/2",
            id="resolution:" + "0" * 64,
            operation=hypothesis.operation,
            members=pairs[assessment.pair_id].members,
            retracts=(),
            evidence=assessment.evidence,
            authority=review.model_call.service.model_id,
            revision=IDENTITY_REVIEW_REVISION,
            reason=assessment.reason,
            basis="model_reviewed",
            valid_from=hypothesis.valid_from,
            valid_to=hypothesis.valid_to,
            population_digest=request.population_digest,
            proposal_digest=proposal.content_digest(),
            review_digest=review.content_digest(),
            proposal_call=proposal.model_call,
            review_call=review.model_call,
        )
        result.append(decision.model_copy(update={"id": "resolution:" + decision.content_digest()}))
    return tuple(result)


class IdentityStage:
    def __init__(
        self, config: GhimeraConfig, proposer: IdentityProposer, reviewer: IdentityReviewer
    ) -> None:
        policy, models = config.identity_automation, config.models
        if policy is None or models is None:
            raise ValueError("identity stage requires its exact effective policy")
        for port, role in ((proposer, policy.proposer_role), (reviewer, policy.reviewer_role)):
            service = models.service(role)
            if port.model != ModelIdentity(
                model_id=service.model_id, revision=service.revision, location="self_hosted"
            ):
                raise ValueError("identity stage ports must bind their configured models")
        self.config, self.proposer, self.reviewer = config, proposer, reviewer

    async def _call(
        self,
        budget: RunBudget,
        ledger: Ledger,
        request: IdentityProposalRequest,
        phase: Literal["identity_propose", "identity_review"],
        model: ModelIdentity,
        payload: IdentityProposalRequest | IdentityReviewRequest,
        invoke: Callable[[], Awaitable[T]],
        output: type[T],
        validate: Callable[[T], object],
    ) -> T:
        data = port_input(budget, payload)
        previous = tuple(
            row
            for row in ledger.snapshot()
            if row.model_intent is not None
            and (
                row.model_intent.phase == phase
                and row.model_intent.input_sha256 == hashlib.sha256(data).hexdigest()
            )
        )
        if len(previous) > 1:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if previous:
            for row in ledger.snapshot():
                if (
                    row.event == phase
                    and row.identity_request == request
                    and row.refusal is not None
                ):
                    raise GhimeraRefused(row.refusal)
                cached = (
                    row.identity_proposal if output is IdentityProposal else row.identity_review
                )
                if cached is not None and row.identity_request == request and row.event == phase:
                    validate_result_binding(self.config, ledger.snapshot(), phase, payload, cached)
                    return output.model_validate(cached.model_dump())
        invocation = ModelInvocation(
            budget,
            ledger,
            phase=phase,
            model=model,
            request=data,
            reserve=budget.reserve_identity_proposal
            if phase == "identity_propose"
            else budget.reserve_identity_review,
            replay_intent_sequence=previous[0].sequence if previous else None,
        )
        result: T | None = None
        try:
            async with asyncio.timeout(budget.remaining_seconds):
                result = (
                    invocation.replay(lambda stored: output.model_validate_json(stored.body()))
                    if previous
                    else await invocation.invoke(invoke, record_output)
                )
            validate(result)
        except (GhimeraRefused, ModelCancelled, TimeoutError, asyncio.CancelledError) as exc:
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event=phase,
                    model=model,
                    identity_request=request,
                    model_call=result.model_call
                    if result is not None
                    else exc.model_call
                    if isinstance(exc, (ModelFailure, ModelCancelled))
                    else None,
                    refusal=exc.code
                    if isinstance(exc, GhimeraRefused)
                    else RefusalCode.MODEL_UNAVAILABLE
                    if isinstance(exc, asyncio.CancelledError)
                    else RefusalCode.BUDGET_EXHAUSTED,
                    reason="identity_call_refused_or_cancelled",
                )
            )
            raise
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event=phase,
                model=model,
                identity_request=request,
                identity_proposal=result if isinstance(result, IdentityProposal) else None,
                identity_review=result if isinstance(result, IdentityReview) else None,
                model_call=result.model_call,
                reason="identity_model_return_observed",
            )
        )
        return result

    async def run(
        self, intent: str, graph: ResearchGraph, budget: RunBudget, ledger: Ledger
    ) -> None:
        validate_identity_rows(self.config, ledger.snapshot(), graph.snapshot())
        unknown = set(uncertain_model_sequences(ledger.snapshot()))
        if any(
            row.sequence in unknown
            and row.model_intent is not None
            and row.model_intent.phase in {"identity_propose", "identity_review"}
            for row in ledger.snapshot()
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        snapshot = graph.snapshot()
        if snapshot.identity_decisions != acknowledged_history(ledger.snapshot()):
            policy = self.config.identity_automation
            if policy is None:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="identity_history",
                    reason="native_reversible_graph_history_observed",
                    identity_history=IdentityHistory(
                        schema="ghimera.identity-history/1",
                        run_id=snapshot.run_id,
                        policy_digest=policy.content_digest(),
                        graph_config_digest=graph.config_digest,
                        checkpoint=snapshot.checkpoint,
                        decisions=snapshot.identity_decisions,
                    ),
                )
            )
        request = request_for(self.config, intent, ledger.snapshot(), graph.snapshot())
        if not request.pairs:
            return
        proposal = await self._call(
            budget,
            ledger,
            request,
            "identity_propose",
            self.proposer.model,
            request,
            lambda: self.proposer.identity_propose(request),
            IdentityProposal,
            lambda result: validate_proposal(self.config, request, result),
        )
        validate_proposal(self.config, request, proposal)
        review_request = IdentityReviewRequest(
            schema="ghimera.identity-review-request/1", request=request, proposal=proposal
        )
        policy = self.config.identity_automation
        if policy is None or len(review_request.model_dump_json()) > policy.max_context_chars:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        review = await self._call(
            budget,
            ledger,
            request,
            "identity_review",
            self.reviewer.model,
            review_request,
            lambda: self.reviewer.identity_review(review_request),
            IdentityReview,
            lambda result: decisions_for(self.config, request, proposal, result),
        )
        decisions = decisions_for(self.config, request, proposal, review)
        if (
            graph.snapshot().checkpoint != request.checkpoint
            or graph.snapshot().identity_decisions != request.history
        ):
            raise GhimeraRefused(RefusalCode.GRAPH_CONTRACT)
        await graph.append(identity_decisions=decisions)
        observation = IdentityObservation(
            schema="ghimera.identity-observation/1",
            request=request,
            proposal=proposal,
            review=review,
            decisions=decisions,
            checkpoint=graph.snapshot().checkpoint,
            withheld_pairs=len(request.pairs) - len(decisions),
        )
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event="identity_resolution",
                reason="reviewed_identity_graph_acknowledged",
                identity_observation=observation,
            )
        )


def acknowledged_history(rows: tuple[LedgerRow, ...]) -> tuple[IdentityDecision, ...]:
    history: tuple[IdentityDecision, ...] = ()
    for row in rows:
        if row.identity_history is not None:
            history = row.identity_history.decisions
        observation = row.identity_observation
        if observation is not None:
            history = observation.request.history + observation.decisions
    return history


def validate_result_binding(
    config: GhimeraConfig,
    rows: tuple[LedgerRow, ...],
    phase: Literal["identity_propose", "identity_review"],
    payload: IdentityProposalRequest | IdentityReviewRequest,
    result: IdentityProposal | IdentityReview,
) -> None:
    """A supplied call-shaped record is not an original native model acknowledgement."""
    validate_call(config, result.model_call, phase)
    call = result.model_call
    if call is None:
        raise ValueError("identity result has no original model call")
    input_hash = hashlib.sha256(port_input(RunBudget(config, monotonic), payload)).hexdigest()
    intents = tuple(
        row
        for row in rows
        if row.model_intent is not None
        and (row.model_intent.phase == phase and row.model_intent.input_sha256 == input_hash)
    )
    if len(intents) != 1 or intents[0].model != ModelIdentity(
        model_id=call.service.model_id, revision=call.service.revision, location="self_hosted"
    ):
        raise ValueError("identity result requires exactly one original matching invocation")
    acks = tuple(
        row.model_ack
        for row in rows
        if row.model_ack is not None and row.model_ack.intent_sequence == intents[0].sequence
    )
    body = record_output(result)
    if len(acks) != 1 or (
        acks[0].outcome != "returned"
        or acks[0].output_scope != "port_output"
        or acks[0].stored_output is None
        or acks[0].stored_output.body() != body
        or acks[0].output_sha256 != hashlib.sha256(body).hexdigest()
    ):
        raise ValueError("identity result must retain exact original acknowledged output bytes")


def validate_model_decision_bindings(
    config: GhimeraConfig,
    rows: tuple[LedgerRow, ...],
    added: tuple[IdentityDecision, ...],
    checkpoint: GraphCheckpoint | None,
    history: tuple[IdentityDecision, ...],
) -> None:
    for row in reversed(rows):
        request, review = row.identity_request, row.identity_review
        if (
            request is None
            or review is None
            or request.checkpoint != checkpoint
            or request.history != history
        ):
            continue
        proposal = next(
            (
                prior.identity_proposal
                for prior in rows[: row.sequence]
                if prior.identity_request == request and prior.identity_proposal is not None
            ),
            None,
        )
        if proposal is None:
            continue
        validate_result_binding(config, rows[: row.sequence], "identity_propose", request, proposal)
        validate_result_binding(
            config,
            rows[: row.sequence],
            "identity_review",
            IdentityReviewRequest(
                schema="ghimera.identity-review-request/1", request=request, proposal=proposal
            ),
            review,
        )
        if decisions_for(config, request, proposal, review) == added:
            return
    raise ValueError(
        "model decisions require exact prior native proposal/review and graph checkpoint"
    )


def validate_identity_rows(
    config: GhimeraConfig, rows: tuple[LedgerRow, ...], graph: GraphSnapshot | None = None
) -> None:
    observed = [
        row
        for row in rows
        if row.identity_request is not None
        or row.identity_observation is not None
        or row.identity_history is not None
    ]
    phases = Counter(
        row.model_intent.phase
        for row in rows
        if row.model_intent is not None
        and row.model_intent.phase in {"identity_propose", "identity_review"}
    )
    policy = config.identity_automation
    if phases and (
        policy is None
        or phases["identity_propose"] > policy.max_proposal_calls
        or phases["identity_review"] > policy.max_review_calls
    ):
        raise ValueError("identity original reservations exceed their explicit phase quotas")
    if not observed:
        if graph is not None and any(d.basis == "model_reviewed" for d in graph.identity_decisions):
            raise ValueError("unacknowledged identity graph effects remain held")
        return
    if config.identity_automation is None:
        raise ValueError("identity observations require their original opt-in policy")
    entities = {
        item.node.id: item
        for row in rows
        if row.semantic_window is not None
        for item in row.semantic_window.entities
        if item.node in row.semantic_window.nodes
    }
    prefix: list[LedgerRow] = []
    for row in rows:
        history = row.identity_history
        if history is not None:
            if config.graph is None or (
                history.policy_digest != config.identity_automation.content_digest()
                or history.graph_config_digest != config.graph.content_digest()
                or history.decisions[: len(acknowledged_history(tuple(prefix)))]
                != acknowledged_history(tuple(prefix))
                or any(
                    d.basis == "model_reviewed" and d not in acknowledged_history(tuple(prefix))
                    for d in history.decisions
                )
            ):
                raise ValueError(
                    "identity history may observe native manual additions, "
                    "never adopt model effects"
                )
            if graph is not None and (
                history.run_id != graph.run_id
                or graph.identity_decisions[: len(history.decisions)] != history.decisions
                or history.checkpoint is None
                or graph.checkpoint is None
                or history.checkpoint.sequence > graph.checkpoint.sequence
                or (
                    history.checkpoint.sequence == graph.checkpoint.sequence
                    and history.checkpoint != graph.checkpoint
                )
            ):
                raise ValueError("identity history must bind the original native graph checkpoint")
        request = row.identity_request or (
            row.identity_observation.request if row.identity_observation else None
        )
        if request is not None:
            validate_request(config, request)
            if request.history[: len(acknowledged_history(tuple(prefix)))] != acknowledged_history(
                tuple(prefix)
            ):
                raise ValueError("identity requests cannot erase reversible acknowledged history")
            prior_entities = {
                item.node.id: item
                for prior in prefix
                if prior.semantic_window is not None
                for item in prior.semantic_window.entities
                if item.node in prior.semantic_window.nodes
            }
            if any(
                m.node_id not in prior_entities
                or (
                    m.surface != prior_entities[m.node_id].node.label
                    or m.role != prior_entities[m.node_id].node.role
                    or m.evidence != prior_entities[m.node_id].evidence
                )
                for m in request.mentions
            ):
                raise ValueError(
                    "identity proposal must bind earlier acknowledged source-local mentions"
                )
            for mention in request.mentions:
                windows = tuple(
                    prior.semantic_window
                    for prior in prefix
                    if prior.semantic_window is not None
                    and any(e.node.id == mention.node_id for e in prior.semantic_window.entities)
                )
                if len(windows) != 1 or (
                    mention.context.document_id != windows[0].graph_document_id
                    or mention.context.start != windows[0].start
                    or mention.context.end != windows[0].end
                    or mention.context.document_sha256 != windows[0].document_sha256
                    or mention.context.text_sha256 != windows[0].text_sha256
                ):
                    raise ValueError(
                        "identity context must be the exact previously admitted native window"
                    )
                if graph is not None:
                    doc = next(
                        (n for n in graph.nodes if n.id == mention.context.document_id), None
                    )
                    if (
                        doc is None
                        or doc.text is None
                        or mention.context
                        != GraphEvidence.from_reading(
                            doc.id,
                            doc.content_sha256 or "",
                            doc.text,
                            windows[0].start,
                            windows[0].end,
                            pdf_reading=doc.pdf_reading,
                        )
                    ):
                        raise ValueError(
                            "identity context must preserve exact native graph text bytes"
                        )
        if row.identity_proposal is not None and request is not None:
            validate_proposal(config, request, row.identity_proposal)
            validate_result_binding(
                config, tuple(prefix), "identity_propose", request, row.identity_proposal
            )
        if row.identity_review is not None and request is not None:
            proposal = next(
                (
                    prior.identity_proposal
                    for prior in prefix
                    if prior.identity_request == request and prior.identity_proposal is not None
                ),
                None,
            )
            if proposal is None:
                raise ValueError("identity review cannot precede its original proposal")
            validate_result_binding(
                config,
                tuple(prefix),
                "identity_review",
                IdentityReviewRequest(
                    schema="ghimera.identity-review-request/1", request=request, proposal=proposal
                ),
                row.identity_review,
            )
        observation = row.identity_observation
        if observation is not None:
            expected = decisions_for(
                config, observation.request, observation.proposal, observation.review
            )
            if observation.decisions != expected or observation.withheld_pairs != len(
                observation.request.pairs
            ) - len(expected):
                raise ValueError(
                    "identity decisions must derive exactly from separate reviewed proposals"
                )
            if not any(
                prior.identity_proposal == observation.proposal
                and prior.identity_request == observation.request
                for prior in prefix
            ) or not any(
                prior.identity_review == observation.review
                and prior.identity_request == observation.request
                for prior in prefix
            ):
                raise ValueError(
                    "identity graph acknowledgement requires preceding actual "
                    "proposal and review observations"
                )
            if graph is not None and any(item not in graph.identity_decisions for item in expected):
                raise ValueError(
                    "identity decisions must be retained in the original graph history"
                )
            expected_checkpoint = observation.request.checkpoint
            if expected:
                if config.graph is None:
                    raise ValueError("identity graph acknowledgement lacks its graph policy")
                batch = GraphBatch(
                    schema="chimera.graph-batch/1",
                    run_id=observation.request.run_id,
                    config_digest=config.graph.content_digest(),
                    sequence=expected_checkpoint.sequence + 1
                    if expected_checkpoint is not None
                    else 0,
                    previous_digest=expected_checkpoint.digest
                    if expected_checkpoint is not None
                    else None,
                    nodes=(),
                    edges=(),
                    identity_decisions=expected,
                )
                expected_checkpoint = GraphCheckpoint(
                    sequence=batch.sequence, digest=batch.content_digest()
                )
            if observation.checkpoint != expected_checkpoint:
                raise ValueError(
                    "identity observation must preserve the exact graph batch acknowledgement"
                )
        prefix.append(row)
    if graph is not None:
        if any(
            d.basis == "model_reviewed" and d not in acknowledged_history(rows)
            for d in graph.identity_decisions
        ):
            raise ValueError("unacknowledged identity graph effects remain held")
        if any(
            m.node_id not in entities
            for row in observed
            if row.identity_request is not None
            for m in row.identity_request.mentions
        ):
            raise ValueError("identity member is outside the acknowledged population")
