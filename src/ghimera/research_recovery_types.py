"""Versioned native research control state, separate from round checkpoints."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.journal_types import Digest, JournalReport, RunId
from ghimera.model_reconciliation_types import (
    ModelAttemptAuthorization,
    ModelReconciliationDecision,
    ModelUnknownObservation,
)
from ghimera.models import ModelIdentity, Record
from ghimera.query_work_types import QueryReservation
from ghimera.research_types import (
    AnswerDraft,
    AnswerRequest,
    AnswerReview,
    Assessment,
    EvidenceRequest,
    PlanningRequest,
    ResearchPlan,
    ResearchRequest,
    ResearchResult,
    ReviewRequest,
    SearchRequest,
)
from ghimera.session_state import SessionState

Count = Annotated[int, Field(strict=True, ge=0)]
ResearchPhase = Literal["plan", "assessment", "answer", "review"]
ResearchPhaseRequest = PlanningRequest | AnswerRequest | ReviewRequest | EvidenceRequest
ResearchPhaseResult = ResearchPlan | Assessment | AnswerDraft | AnswerReview


class ResearchRecoveryModels(Record):
    planner: ModelIdentity
    analyst: ModelIdentity
    reviewer: ModelIdentity
    search_provider: Annotated[str, Field(min_length=1)]
    search_revision: Annotated[str, Field(min_length=1)]


class ResearchPendingModel(Record):
    """The exact native port input recorded before invoking a research phase."""

    phase: ResearchPhase
    model: ModelIdentity
    input_sha256: Digest
    input_bytes: Annotated[int, Field(strict=True, gt=0)]


class ResearchControlSnapshot(Record):
    schema_version: Literal["ghimera.research-control-snapshot/1"] = Field(alias="schema")
    run_id: RunId
    saved_at: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_snapshot_bytes: Annotated[int, Field(strict=True, gt=0)]
    request: ResearchRequest
    progress: ResearchResult
    session: SessionState
    admitted_hosts: tuple[str, ...]
    phase: ResearchPhase
    round_number: Annotated[int, Field(strict=True, gt=0)]
    model_request: ResearchPhaseRequest
    pending_model: ResearchPendingModel
    current_plan: ResearchPlan | None = None
    assessment: Assessment | None = None
    current_answer: AnswerDraft | None = None
    current_review: AnswerReview | None = None
    assessment_context: Literal["post_collection", "retained_first"] = "post_collection"
    discovered_urls: tuple[str, ...] = ()
    collection_stop: str = "frontier_empty"
    before_documents: tuple[Digest, ...] = ()
    before_answers: tuple[str, ...] = ()
    allow_retained_completion: bool = True

    @property
    def models(self) -> ResearchRecoveryModels:
        progress = self.progress
        return ResearchRecoveryModels(
            planner=progress.planner,
            analyst=progress.analyst,
            reviewer=progress.reviewer,
            search_provider=progress.search_provider,
            search_revision=progress.search_revision,
        )

    @model_validator(mode="after")
    def coherent(self) -> "ResearchControlSnapshot":
        progress, harvest = self.progress, self.progress.harvest
        policy = harvest.receipt.effective_config.research
        expected_type = {
            "plan": PlanningRequest,
            "assessment": EvidenceRequest,
            "answer": AnswerRequest,
            "review": ReviewRequest,
        }[self.phase]
        expected_model = (
            progress.planner
            if self.phase == "plan"
            else progress.reviewer
            if self.phase == "review"
            else progress.analyst
        )
        if (
            policy is None
            or progress.status != "partial"
            or progress.answer is not None
            or progress.review is not None
            or harvest.goal.text != self.request.intent
            or harvest.goal.seeds != self.request.seeds
            or self.round_number > policy.max_rounds
            or len(progress.rounds) > policy.max_rounds
            or self.round_number
            != len(progress.rounds) + (1 if self.phase in {"plan", "assessment"} else 0)
            or tuple(item.number for item in progress.rounds)
            != tuple(range(1, len(progress.rounds) + 1))
            or any(row.event == "stop" for row in harvest.ledger)
            or self.pending_model.phase != self.phase
            or self.pending_model.model != expected_model
            or type(self.model_request) is not expected_type
            or self.model_request.intent != self.request.intent
            or self.model_request.questions != progress.questions
            or self.model_request.documents != progress.evidence_documents
        ):
            raise ValueError("recovery requires exact unsealed research phase control and input")
        if self.phase == "assessment" and (
            self.current_plan is None or self.current_plan.questions != progress.questions
        ):
            raise ValueError("assessment recovery must preserve its current round plan")
        if self.phase == "answer" and (
            self.assessment is None
            or not isinstance(self.model_request, AnswerRequest)
            or self.model_request.assessment != self.assessment
        ):
            raise ValueError("answer recovery must preserve its exact assessment")
        if self.phase == "review" and (
            self.current_answer is None
            or not isinstance(self.model_request, ReviewRequest)
            or self.model_request.answer != self.current_answer
        ):
            raise ValueError("review recovery must preserve its exact answer")
        if self.phase == "plan" and (
            not isinstance(self.model_request, PlanningRequest)
            or self.model_request.assessment != self.assessment
        ):
            raise ValueError("planning recovery must preserve its prior assessment")
        if harvest.graph is not None and harvest.graph.run_id != self.run_id:
            raise ValueError("recovery graph belongs to another run")
        if (
            len(set(self.session.visited)) != len(self.session.visited)
            or len(set(self.admitted_hosts)) != len(self.admitted_hosts)
            or set(self.session.reference_scopes) != set(self.session.reference_hops)
            or set(self.session.reference_scopes) != set(self.session.reference_origins)
            or any(not scope.permits(url) for url, scope in self.session.reference_scopes.items())
        ):
            raise ValueError("recovery must preserve coherent collection frontier and scope")
        return self


class ResearchRecoveryReceipt(Record):
    schema_version: Literal["ghimera.research-recovery-receipt/1"] = Field(alias="schema")
    run_id: RunId
    sha256: Digest
    size_bytes: Annotated[int, Field(strict=True, gt=0)]
    phase: ResearchPhase | Literal["query"]
    ledger_rows: Count


class ResearchQueryControlSnapshot(Record):
    """Original serial outer query cursor, saved while all native owners are quiescent."""

    schema_version: Literal["ghimera.research-control-snapshot/2"] = Field(alias="schema")
    phase: Literal["query"] = "query"
    run_id: RunId
    saved_at: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_snapshot_bytes: Annotated[int, Field(strict=True, gt=0)]
    request: ResearchRequest
    progress: ResearchResult
    session: SessionState
    admitted_hosts: tuple[str, ...]
    pending_query: QueryReservation
    runtime_json: bytes
    round_number: Annotated[int, Field(strict=True, gt=0)]
    current_plan: ResearchPlan | None = None
    assessment: Assessment | None = None
    assessment_context: Literal["post_collection", "retained_first"] = "post_collection"
    discovered_urls: tuple[str, ...] = ()
    collection_stop: str = "frontier_empty"
    before_documents: tuple[Digest, ...] = ()
    before_answers: tuple[str, ...] = ()
    allow_retained_completion: bool = True
    quantum_start: Count = 0
    model_request: None = None
    current_answer: None = None

    @property
    def models(self) -> ResearchRecoveryModels:
        p = self.progress
        return ResearchRecoveryModels(
            planner=p.planner,
            analyst=p.analyst,
            reviewer=p.reviewer,
            search_provider=p.search_provider,
            search_revision=p.search_revision,
        )

    @model_validator(mode="after")
    def coherent(self) -> "ResearchQueryControlSnapshot":
        p = self.progress
        cfg = p.harvest.receipt.effective_config
        if (
            cfg.research is None
            or cfg.research_recovery is None
            or cfg.research_recovery.query_control != "serial_acknowledged"
            or self.max_snapshot_bytes != cfg.research_recovery.max_snapshot_bytes
            or p.status != "partial"
            or p.answer is not None
            or p.review is not None
            or p.harvest.goal.text != self.request.intent
            or p.harvest.goal.seeds != self.request.seeds
            or self.round_number != len(p.rounds) + 1
            or self.round_number > cfg.research.max_rounds
            or self.pending_query.cursor.round_number != self.round_number
            or any(row.event == "stop" for row in p.harvest.ledger)
            or self.pending_query.cursor.stage != "initial_retained"
            and (self.current_plan is None or self.current_plan.questions != p.questions)
            or p.harvest.graph is not None
            and p.harvest.graph.run_id != self.run_id
        ):
            raise ValueError(
                "query control requires its exact original unsealed bounded research state"
            )
        reservation, cursor = self.pending_query, self.pending_query.cursor
        from ghimera.models import LedgerRow
        from ghimera.query_work import validate_query_rows

        validate_query_rows(
            cfg,
            p.harvest.ledger
            + (
                LedgerRow(
                    sequence=len(p.harvest.ledger),
                    event="query_intent",
                    reason="query_reserved",
                    query_reservation=reservation,
                ),
            ),
        )
        if (
            len(set(self.session.visited)) != len(self.session.visited)
            or len(set(self.admitted_hosts)) != len(self.admitted_hosts)
            or set(self.session.reference_scopes) != set(self.session.reference_hops)
            or set(self.session.reference_scopes) != set(self.session.reference_origins)
            or any(not scope.permits(url) for url, scope in self.session.reference_scopes.items())
            or self.quantum_start > p.harvest.receipt.fetches
        ):
            raise ValueError("query control changed original native session/cursor ownership")
        if reservation.channel == "discovery":
            request = SearchRequest.model_validate_json(reservation.request_json)
            if (
                reservation.search_reservation != p.search_calls + 1
                or reservation.fetch_reservation != p.harvest.receipt.fetches + 1
                or request.max_bytes > cfg.byte_budget - p.harvest.receipt.bytes_read
                or request.timeout_seconds > cfg.wall_seconds - p.harvest.receipt.elapsed_seconds
            ):
                raise ValueError("query control changed original remaining budget")
            if cursor.stage == "discovery":
                if (
                    self.current_plan is None
                    or cursor.query_index >= len(self.current_plan.queries)
                    or request.query != self.current_plan.queries[cursor.query_index]
                ):
                    raise ValueError("query cursor changed original planned discovery input")
            else:
                parent = (
                    p.harvest.ledger[cursor.parent_sequence]
                    if cursor.parent_sequence is not None
                    and cursor.parent_sequence < len(p.harvest.ledger)
                    else None
                )
                if (
                    parent is None
                    or parent.reference_query is None
                    or not any(
                        doc.url == parent.reference_query.source.url
                        and doc.sha256 == parent.reference_query.source.sha256
                        for doc in p.harvest.documents
                    )
                ):
                    raise ValueError("query cursor lost its original cited-by source")
        else:
            text = reservation.request_json.decode()
            expected = (
                self.request.intent
                if cursor.stage == "initial_retained"
                else (
                    self.current_plan.queries[cursor.query_index].text
                    if self.current_plan is not None
                    and cursor.query_index < len(self.current_plan.queries)
                    else None
                )
            )
            if (
                text != expected
                or cursor.stage == "initial_retained"
                and (
                    cursor.query_index != 0
                    or cursor.provider_index != 0
                    or self.current_plan is not None
                )
                or reservation.retained_reservation
                != (len(p.retrieval.observations) if p.retrieval is not None else 0) + 1
            ):
                raise ValueError("retained cursor changed original input or allowance")
        return self


class ResearchRecoveryRead(Record):
    """Verified original state plus journal evidence; admission performs no replay."""

    snapshot: ResearchControlSnapshot | ResearchQueryControlSnapshot
    journal: JournalReport
    intent_sequence: Count | None
    query_sequence: Count | None = Field(default=None, exclude_if=lambda v: v is None)
    query_ack_sequence: Count | None = Field(default=None, exclude_if=lambda v: v is None)
    query_rerank_sequence: Count | None = Field(default=None, exclude_if=lambda v: v is None)
    decision: ModelReconciliationDecision | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    attempt: ModelAttemptAuthorization | None = Field(default=None, exclude_if=lambda v: v is None)
    observation: ModelUnknownObservation | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
