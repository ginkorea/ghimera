"""Exact acquired-source control in the existing SourceWork transaction."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.journal_types import Digest, JournalReport, RunId
from ghimera.models import Record, Scope
from ghimera.research_recovery_types import ResearchRecoveryModels
from ghimera.research_types import Assessment, ResearchPlan, ResearchRequest, ResearchResult
from ghimera.session_state import SessionState
from ghimera.source_completion import SourceCompletionRuntime
from ghimera.source_work_types import SourceOperation

Count = Annotated[int, Field(strict=True, ge=0)]


class SourceAcquisitionSnapshot(Record):
    schema_version: Literal["ghimera.source-acquisition-control/1"] = Field(alias="schema")
    run_id: RunId
    saved_at: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_capsule_bytes: Annotated[int, Field(strict=True, gt=0)]
    operation_id: Digest
    operation_sha256: Digest
    return_sequence: Count
    return_sha256: Digest
    request: ResearchRequest
    progress: ResearchResult
    runtime: SourceCompletionRuntime
    session: SessionState
    admitted_hosts: tuple[str, ...]
    phase: Literal["initial_local", "collection"]
    round_number: Annotated[int, Field(strict=True, gt=0)]
    current_plan: ResearchPlan | None
    assessment: Assessment | None
    assessment_context: Literal["post_collection", "retained_first"]
    discovered_urls: tuple[str, ...]
    collection_stop: str
    before_documents: tuple[Digest, ...]
    before_answers: tuple[str, ...]
    allow_retained_completion: bool
    scope: Scope | None
    leg: Literal["primary", "cited_by"]
    quantum_start: Count
    starting_fetches: Count
    fetch_limit: Annotated[int, Field(strict=True, gt=0)]
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
    def coherent(self) -> "SourceAcquisitionSnapshot":
        p, h = self.progress, self.progress.harvest
        cfg = h.receipt.effective_config
        recovery = cfg.research_recovery
        policy = recovery.source_acquisition if recovery is not None else None
        if (
            policy is None
            or cfg.research is None
            or policy.max_capsule_bytes != self.max_capsule_bytes
            or p.status != "partial"
            or p.answer is not None
            or p.review is not None
            or h.goal.text != self.request.intent
            or h.goal.seeds != self.request.seeds
            or self.runtime.judge != h.receipt.judge
            or self.round_number != len(p.rounds) + 1
            or self.round_number > cfg.research.max_rounds
            or tuple(r.number for r in p.rounds) != tuple(range(1, self.round_number))
            or any(row.event == "stop" for row in h.ledger)
            or h.graph is not None
            and h.graph.run_id != self.run_id
            or not self.quantum_start <= self.starting_fetches <= h.receipt.fetches
            or self.fetch_limit > cfg.research.max_pages_per_round
        ):
            raise ValueError("acquisition requires exact original research control and spend")
        if self.phase == "initial_local":
            if p.rounds or p.questions or self.current_plan is not None or self.scope is not None:
                raise ValueError("initial owned-source acquisition cannot fabricate a plan")
        elif (
            self.current_plan is None
            or self.current_plan.questions != p.questions
            or self.scope is None
        ):
            raise ValueError("collection acquisition requires its original plan/scope cursor")
        if (
            len(set(self.session.visited)) != len(self.session.visited)
            or len(set(self.admitted_hosts)) != len(self.admitted_hosts)
            or set(self.session.reference_scopes) != set(self.session.reference_hops)
            or set(self.session.reference_scopes) != set(self.session.reference_origins)
            or any(not scope.permits(url) for url, scope in self.session.reference_scopes.items())
        ):
            raise ValueError("acquisition requires coherent original frontier and references")
        return self


class SourceAcquisitionRead(Record):
    snapshot: SourceAcquisitionSnapshot
    operation: SourceOperation
    journal: JournalReport
    sha256: Digest
