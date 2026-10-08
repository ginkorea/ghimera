"""Control for an atomic, fully completed serial source, not interrupted I/O."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.journal_types import Digest, JournalReport, RunId
from ghimera.models import ModelIdentity, Record, Scope
from ghimera.research_recovery_types import ResearchRecoveryModels
from ghimera.research_types import Assessment, ResearchPlan, ResearchRequest, ResearchResult
from ghimera.session_state import SessionState

Count = Annotated[int, Field(strict=True, ge=0)]


class SourceCompletionRuntime(Record):
    extractor_revision: Annotated[str, Field(min_length=1)]
    scorer_name: Annotated[str, Field(min_length=1)]
    scorer_cost: Count
    judge: ModelIdentity


class SourceCompletionSnapshot(Record):
    schema_version: Literal["ghimera.source-completion-control/1"] = Field(alias="schema")
    phase: Literal["collection"] = "collection"
    run_id: RunId
    saved_at: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_capsule_bytes: Annotated[int, Field(strict=True, gt=0)]
    request: ResearchRequest
    progress: ResearchResult
    runtime: SourceCompletionRuntime
    session: SessionState
    admitted_hosts: tuple[str, ...]
    round_number: Annotated[int, Field(strict=True, gt=0)]
    current_plan: ResearchPlan
    assessment: Assessment | None
    assessment_context: Literal["post_collection", "retained_first"]
    discovered_urls: tuple[str, ...]
    collection_stop: str
    before_documents: tuple[Digest, ...]
    before_answers: tuple[str, ...]
    allow_retained_completion: bool
    scope: Scope
    leg: Literal["primary", "cited_by"]
    quantum_start: Count
    starting_fetches: Count
    fetch_limit: Annotated[int, Field(strict=True, gt=0)]
    # Shared control accessors do not invent a pending model input/acknowledgement.
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
    def coherent(self) -> "SourceCompletionSnapshot":
        p, h = self.progress, self.progress.harvest
        cfg = h.receipt.effective_config
        policy = cfg.research_recovery
        if (
            cfg.research is None
            or policy is None
            or policy.source_completion is None
            or policy.source_completion.max_capsule_bytes != self.max_capsule_bytes
            or p.status != "partial"
            or p.answer is not None
            or p.review is not None
            or h.goal.text != self.request.intent
            or h.goal.seeds != self.request.seeds
            or self.round_number != len(p.rounds) + 1
            or self.round_number > cfg.research.max_rounds
            or tuple(r.number for r in p.rounds) != tuple(range(1, self.round_number))
            or self.current_plan.questions != p.questions
            or any(r.event == "stop" for r in h.ledger)
            or h.graph is not None
            and h.graph.run_id != self.run_id
        ):
            raise ValueError("source completion requires exact bounded original collection control")
        if not self.quantum_start <= self.starting_fetches <= h.receipt.fetches:
            raise ValueError("source completion cannot reset original collection cursor")
        if self.fetch_limit > cfg.research.max_pages_per_round:
            raise ValueError("source completion cannot expand original collection quantum")
        if (
            len(set(self.session.visited)) != len(self.session.visited)
            or len(set(self.admitted_hosts)) != len(self.admitted_hosts)
            or set(self.session.reference_scopes) != set(self.session.reference_hops)
            or set(self.session.reference_scopes) != set(self.session.reference_origins)
            or any(not scope.permits(url) for url, scope in self.session.reference_scopes.items())
        ):
            raise ValueError("source completion requires coherent original frontier and references")
        return self


class SourceCompletionRead(Record):
    snapshot: SourceCompletionSnapshot
    journal: JournalReport
    sha256: Digest
