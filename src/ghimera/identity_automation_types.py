"""Opt-in evidence-bound identity proposals and independent review contracts."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.graph_types import (
    Count,
    Digest,
    GraphCheckpoint,
    GraphEvidence,
    GraphModelIdentityDecision,
    GraphRecord,
    IdentityDecision,
    Name,
    Positive,
    Text,
)
from ghimera.model_types import IdentityCallEvidence


class IdentityAutomationConfig(GraphRecord):
    schema_version: Literal["ghimera.identity-automation/1"] = Field(alias="schema")
    roles: Annotated[tuple[Name, ...], Field(min_length=1)]
    proposer_role: Literal["analyst", "planner"]
    reviewer_role: Literal["reviewer"]
    require_distinct_models: bool
    max_mentions: Annotated[int, Field(strict=True, ge=2)]
    max_pairs_per_pass: Positive
    max_proposal_calls: Positive
    max_review_calls: Positive
    max_context_chars: Positive
    max_evidence_chars: Positive
    max_reason_chars: Positive
    as_of: date | None

    @model_validator(mode="after")
    def distinct(self) -> "IdentityAutomationConfig":
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("identity automation roles must be distinct")
        return self


class IdentityMention(GraphRecord):
    node_id: Text
    role: Name
    surface: Text
    evidence: GraphEvidence
    context: GraphEvidence


class IdentityPair(GraphRecord):
    id: Digest
    members: Annotated[tuple[Text, Text], Field(min_length=2, max_length=2)]

    @model_validator(mode="after")
    def ordered(self) -> "IdentityPair":
        if tuple(sorted(set(self.members))) != self.members:
            raise ValueError("identity pair requires two ordered original members")
        return self


class IdentityProposalRequest(GraphRecord):
    schema_version: Literal["ghimera.identity-proposal-request/1"] = Field(alias="schema")
    intent: Text
    run_id: Text
    policy_digest: Digest
    population_digest: Digest
    history_digest: Digest
    history: tuple[IdentityDecision, ...]
    checkpoint: GraphCheckpoint | None
    mentions: tuple[IdentityMention, ...]
    pairs: tuple[IdentityPair, ...]
    omitted_mentions: Count
    omitted_pairs: Count


class IdentityHypothesis(GraphRecord):
    pair_id: Digest
    operation: Literal["merge", "split", "unresolved"]
    evidence: tuple[GraphEvidence, ...]
    reason: Text
    valid_from: date | None
    valid_to: date | None

    @model_validator(mode="after")
    def temporal(self) -> "IdentityHypothesis":
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_from > self.valid_to
        ):
            raise ValueError("identity hypothesis dates are reversed")
        return self


class IdentityProposal(GraphRecord):
    schema_version: Literal["ghimera.identity-proposal/1"] = Field(alias="schema")
    request_digest: Digest
    hypotheses: tuple[IdentityHypothesis, ...]
    model_call: IdentityCallEvidence | None = None


class IdentityReviewRequest(GraphRecord):
    schema_version: Literal["ghimera.identity-review-request/1"] = Field(alias="schema")
    request: IdentityProposalRequest
    proposal: IdentityProposal


class IdentityAssessment(GraphRecord):
    pair_id: Digest
    identity: Literal["supported", "unsupported", "ambiguous"]
    role_compatibility: Literal["supported", "unsupported", "ambiguous"]
    temporal: Literal["supported", "unsupported", "ambiguous"]
    evidence: tuple[GraphEvidence, ...]
    reason: Text

    @property
    def supported(self) -> bool:
        return self.identity == self.role_compatibility == self.temporal == "supported"


class IdentityReview(GraphRecord):
    schema_version: Literal["ghimera.identity-review/1"] = Field(alias="schema")
    proposal_digest: Digest
    assessments: tuple[IdentityAssessment, ...]
    model_call: IdentityCallEvidence | None = None


class IdentityObservation(GraphRecord):
    """Only acknowledged graph decisions enter later planning."""

    schema_version: Literal["ghimera.identity-observation/1"] = Field(alias="schema")
    request: IdentityProposalRequest
    proposal: IdentityProposal
    review: IdentityReview
    decisions: tuple[GraphModelIdentityDecision, ...]
    checkpoint: GraphCheckpoint | None
    withheld_pairs: Count


class IdentityHistory(GraphRecord):
    """Projection of an already acknowledged graph, never a decision command."""

    schema_version: Literal["ghimera.identity-history/1"] = Field(alias="schema")
    run_id: Text
    policy_digest: Digest
    graph_config_digest: Digest
    checkpoint: GraphCheckpoint | None
    decisions: tuple[IdentityDecision, ...]


IDENTITY_PROPOSAL_REVISION = "ghimera-identity-propose/1"
IDENTITY_REVIEW_REVISION = "ghimera-identity-review/1"
