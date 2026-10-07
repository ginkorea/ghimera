"""Configured source-local semantic observations, independent of crawler state."""

from datetime import date
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from ghimera.graph_types import (
    Confidence,
    Count,
    Digest,
    GraphEdge,
    GraphEvidence,
    GraphNode,
    GraphRecord,
    Name,
    Positive,
    Text,
)
from ghimera.model_types import ModelCallEvidence

CitationId = Annotated[str, Field(pattern=r"^cite:[0-9a-f]{64}$")]
MentionKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
SEMANTIC_PROMPT_REVISION = "ghimera-semantic-extraction/1"
MENTION_KEY_PROMPT_REVISION = "ghimera-semantic-extraction/2"
NATIVE_SPAN_PROMPT_REVISION = "ghimera-semantic-extraction/3"
DEFINED_ONTOLOGY_PROMPT_REVISION = "ghimera-semantic-extraction/4"
SEMANTIC_REVIEW_REVISION = "ghimera-semantic-verification/1"
FACTORIZED_REVIEW_REVISION = "ghimera-semantic-verification/2"
GROUNDED_REVIEW_REVISION = "ghimera-semantic-verification/3"
BATCHED_REVIEW_REVISION = "ghimera-semantic-verification/4"
ASSIGNED_ROLE_REVIEW_REVISION = "ghimera-semantic-verification/5"
PROPOSAL_DATE_REVIEW_REVISION = "ghimera-semantic-verification/6"
INDEPENDENT_REVIEW_REVISION = "ghimera-semantic-verification/7"
NATIVE_QUOTE_REVIEW_REVISION = "ghimera-semantic-verification/8"
SEMANTIC_PROFILES = MappingProxyType(
    {
        "ghimera.semantics/1": (None, SEMANTIC_PROMPT_REVISION),
        "ghimera.semantics/2": ("explicit_mention_keys", MENTION_KEY_PROMPT_REVISION),
        "ghimera.semantics/3": ("native_span_keys", NATIVE_SPAN_PROMPT_REVISION),
        "ghimera.semantics/4": ("defined_ontology", DEFINED_ONTOLOGY_PROMPT_REVISION),
    }
)


class SemanticDefinition(GraphRecord):
    name: Name
    definition: Annotated[str, Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def nonblank(self) -> "SemanticDefinition":
        if not self.definition.strip():
            raise ValueError("semantic definitions cannot be blank")
        return self


class SemanticVerificationConfig(GraphRecord):
    schema_version: Literal[
        "ghimera.semantic-verification/1",
        "ghimera.semantic-verification/2",
        "ghimera.semantic-verification/3",
        "ghimera.semantic-verification/4",
    ] = Field(alias="schema")
    model_role: Literal["analyst", "reviewer", "judge"]
    max_calls_per_run: Positive
    max_coverage_findings: Positive | None = Field(default=None, exclude_if=lambda v: v is None)
    max_mentions_per_call: Positive | None = Field(default=None, exclude_if=lambda v: v is None)
    max_relations_per_call: Positive | None = Field(default=None, exclude_if=lambda v: v is None)
    prompt_profile: (
        Literal[
            "assigned_role_checks",
            "proposal_date_checks",
            "independent_dimension_checks",
            "native_quote_checks",
        ]
        | None
    ) = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def coverage_bound(self) -> "SemanticVerificationConfig":
        if (
            self.schema_version
            in {"ghimera.semantic-verification/3", "ghimera.semantic-verification/4"}
        ) != (self.max_coverage_findings is not None):
            raise ValueError("grounded verification requires an explicit coverage finding limit")
        batching = self.schema_version == "ghimera.semantic-verification/4"
        if batching != (self.max_mentions_per_call is not None) or batching != (
            self.max_relations_per_call is not None
        ):
            raise ValueError("only verification/4 requires both explicit batch item limits")
        if self.prompt_profile is not None and not batching:
            raise ValueError("the selected review profile requires explicit batching")
        return self

    @property
    def effective_prompt_revision(self) -> str:
        if self.schema_version == "ghimera.semantic-verification/4":
            if self.prompt_profile == "native_quote_checks":
                return NATIVE_QUOTE_REVIEW_REVISION
            if self.prompt_profile == "independent_dimension_checks":
                return INDEPENDENT_REVIEW_REVISION
            if self.prompt_profile == "proposal_date_checks":
                return PROPOSAL_DATE_REVIEW_REVISION
            return (
                ASSIGNED_ROLE_REVIEW_REVISION
                if self.prompt_profile == "assigned_role_checks"
                else BATCHED_REVIEW_REVISION
            )
        if self.schema_version == "ghimera.semantic-verification/3":
            return GROUNDED_REVIEW_REVISION
        return (
            FACTORIZED_REVIEW_REVISION
            if self.schema_version == "ghimera.semantic-verification/2"
            else SEMANTIC_REVIEW_REVISION
        )


class SemanticConfig(GraphRecord):
    schema_version: Literal[
        "ghimera.semantics/1", "ghimera.semantics/2", "ghimera.semantics/3", "ghimera.semantics/4"
    ] = Field(alias="schema")
    prompt_profile: (
        Literal["explicit_mention_keys", "native_span_keys", "defined_ontology"] | None
    ) = Field(default=None, exclude_if=lambda value: value is None)
    model_role: Literal["analyst", "reviewer", "judge"]
    window_chars: Positive
    max_windows_per_document: Positive
    max_calls_per_run: Positive
    max_mentions_per_window: Positive
    max_relations_per_window: Positive
    entity_roles: Annotated[tuple[Name, ...], Field(min_length=1)]
    relation_rules: tuple[Name, ...]
    mention_rule: Name
    role_definitions: tuple[SemanticDefinition, ...] = Field(default=(), exclude_if=lambda v: not v)
    relation_definitions: tuple[SemanticDefinition, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )
    verification: SemanticVerificationConfig | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def distinct(self) -> "SemanticConfig":
        if self.prompt_profile != SEMANTIC_PROFILES[self.schema_version][0]:
            raise ValueError("semantic schema requires its exact versioned prompt profile")
        if (
            len(set(self.entity_roles)) != len(self.entity_roles)
            or len(set(self.relation_rules)) != len(self.relation_rules)
            or self.mention_rule in self.relation_rules
        ):
            raise ValueError("semantic roles and relation rules must be distinct")
        defined = self.schema_version == "ghimera.semantics/4"
        if defined:
            roles = {item.name for item in self.role_definitions}
            relations = {item.name for item in self.relation_definitions}
            if (
                roles != set(self.entity_roles)
                or relations != set(self.relation_rules)
                or len(roles) != len(self.role_definitions)
                or len(relations) != len(self.relation_definitions)
                or self.verification is None
            ):
                raise ValueError(
                    "defined ontology requires complete unique definitions and verification"
                )
        elif self.role_definitions or self.relation_definitions or self.verification is not None:
            raise ValueError("definitions and verification require semantics/4")
        return self

    @property
    def effective_prompt_revision(self) -> str:
        return SEMANTIC_PROFILES[self.schema_version][1]


class ProposedMention(GraphRecord):
    key: MentionKey
    role: Name
    surface: Text
    citation_id: CitationId
    occurrence: Count
    confidence: Confidence

    @model_validator(mode="after")
    def named(self) -> "ProposedMention":
        if not self.surface.strip():
            raise ValueError("named entity mentions cannot be blank")
        return self


class ProposedRelation(GraphRecord):
    rule: Name
    source: MentionKey
    target: MentionKey
    confidence: Confidence
    citation_ids: Annotated[tuple[CitationId, ...], Field(min_length=1)]
    valid_from: date | None
    valid_to: date | None

    @model_validator(mode="after")
    def temporal(self) -> "ProposedRelation":
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("asserted validity dates cannot run backwards")
        if len(set(self.citation_ids)) != len(self.citation_ids):
            raise ValueError("relation citations must be distinct")
        return self


class SemanticProposal(GraphRecord):
    mentions: tuple[ProposedMention, ...]
    relations: tuple[ProposedRelation, ...]
    model_call: ModelCallEvidence | None = None

    @model_validator(mode="after")
    def keys(self) -> "SemanticProposal":
        names = {item.key for item in self.mentions}
        if len(names) != len(self.mentions) or any(
            item.source not in names or item.target not in names for item in self.relations
        ):
            raise ValueError("relations require unique, observed mention keys")
        return self


class SemanticEntity(GraphRecord):
    key: MentionKey
    node: GraphNode
    evidence: GraphEvidence
    confidence: Confidence


ReviewVerdict = Literal["supported", "unsupported", "ambiguous"]


class MentionAssessment(GraphRecord):
    key: MentionKey
    verdict: ReviewVerdict
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class RelationAssessment(GraphRecord):
    index: Count
    verdict: ReviewVerdict
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class SemanticReview(GraphRecord):
    proposal_digest: Digest
    mentions: tuple[MentionAssessment, ...]
    relations: tuple[RelationAssessment, ...]
    coverage: Literal["adequate", "incomplete", "uncertain"]
    coverage_reason: Annotated[str, Field(min_length=1, max_length=4096)]
    model_call: ModelCallEvidence | None = None

    @model_validator(mode="after")
    def unique_assessments(self) -> "SemanticReview":
        if len({item.key for item in self.mentions}) != len(self.mentions) or len(
            {item.index for item in self.relations}
        ) != len(self.relations):
            raise ValueError("each proposed item requires one distinct assessment")
        return self


class SemanticCheck(GraphRecord):
    verdict: ReviewVerdict
    reason: Annotated[str, Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def explained(self) -> "SemanticCheck":
        if not self.reason.strip():
            raise ValueError("semantic check requires a nonblank dimension-specific reason")
        return self


class MentionChecks(GraphRecord):
    named_entity: SemanticCheck
    role: SemanticCheck


class RelationChecks(GraphRecord):
    entailment: SemanticCheck
    direction: SemanticCheck
    validity: SemanticCheck


class FactorizedMentionAssessment(MentionAssessment):
    checks: MentionChecks


class FactorizedRelationAssessment(RelationAssessment):
    checks: RelationChecks


def combined_verdict(checks: tuple[SemanticCheck, ...]) -> ReviewVerdict:
    """A failed dimension dominates; uncertainty can never become support."""
    if any(check.verdict == "unsupported" for check in checks):
        return "unsupported"
    if any(check.verdict == "ambiguous" for check in checks):
        return "ambiguous"
    return "supported"


class FactorizedSemanticReview(SemanticReview):
    schema_version: Literal["ghimera.semantic-review/2"] = Field(alias="schema")
    mentions: tuple[FactorizedMentionAssessment, ...]
    relations: tuple[FactorizedRelationAssessment, ...]

    @model_validator(mode="after")
    def consistent_dimensions(self) -> "FactorizedSemanticReview":
        for mention in self.mentions:
            if mention.verdict != combined_verdict(
                (mention.checks.named_entity, mention.checks.role)
            ):
                raise ValueError("mention summary must match every explicit review dimension")
        for relation in self.relations:
            if relation.verdict != combined_verdict(
                (relation.checks.entailment, relation.checks.direction, relation.checks.validity)
            ):
                raise ValueError("relation summary must match every explicit review dimension")
        return self


class NativeReviewWitness(GraphRecord):
    """An exact occurrence in the selected source, not a verified graph fact."""

    surface: Text
    citation_id: CitationId
    occurrence: Count

    @model_validator(mode="after")
    def nonblank(self) -> "NativeReviewWitness":
        if not self.surface.strip():
            raise ValueError("native review witnesses cannot be blank")
        return self


class OmittedMention(NativeReviewWitness):
    kind: Literal["mention"]
    role: Name
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class OmittedRelation(GraphRecord):
    kind: Literal["relation"]
    rule: Name
    source: NativeReviewWitness
    target: NativeReviewWitness
    evidence: NativeReviewWitness
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


CoverageFinding = Annotated[OmittedMention | OmittedRelation, Field(discriminator="kind")]


class DateAssertionCheck(GraphRecord):
    """Absence of an assertion is neutral, not a claim of timeless validity."""

    asserted: bool = Field(strict=True)
    assessment: SemanticCheck | None

    @model_validator(mode="after")
    def explicit(self) -> "DateAssertionCheck":
        if self.asserted != (self.assessment is not None):
            raise ValueError("asserted dates require an assessment; unasserted dates have none")
        return self


class GroundedRelationChecks(GraphRecord):
    entailment: SemanticCheck
    direction: SemanticCheck
    validity: DateAssertionCheck

    @property
    def verdict(self) -> ReviewVerdict:
        checks: tuple[SemanticCheck, ...] = (self.entailment, self.direction)
        if self.validity.assessment is not None:
            checks += (self.validity.assessment,)
        return combined_verdict(checks)


class GroundedRelationAssessment(RelationAssessment):
    checks: GroundedRelationChecks


class IndependentMentionAssessment(GraphRecord):
    key: MentionKey
    reason: Annotated[str, Field(min_length=1, max_length=4096)]
    checks: MentionChecks


class IndependentRelationAssessment(GraphRecord):
    index: Count
    reason: Annotated[str, Field(min_length=1, max_length=4096)]
    checks: GroundedRelationChecks


class IndependentSemanticReview(GraphRecord):
    """Model-facing independent judgments; no generated aggregate verdict."""

    schema_version: Literal["ghimera.semantic-review/5"] = Field(alias="schema")
    proposal_digest: Digest
    mentions: tuple[IndependentMentionAssessment, ...]
    relations: tuple[IndependentRelationAssessment, ...]
    coverage: Literal["adequate", "incomplete", "uncertain"]
    coverage_reason: Annotated[str, Field(min_length=1, max_length=4096)]
    coverage_findings: tuple[CoverageFinding, ...]
    model_call: ModelCallEvidence | None = None


class NativeQuoteTemplate(GraphRecord):
    """Client-owned exact source slice, not a model-authored quotation."""

    quote_id: Digest
    citation_id: CitationId
    start: Count
    end: Positive
    quote: Text
    occurrence: Count

    @model_validator(mode="after")
    def exact(self) -> "NativeQuoteTemplate":
        from ghimera.semantic_quotes import quote_identifier

        if (
            self.end - self.start != len(self.quote)
            or not self.quote.strip()
            or self.quote_id != quote_identifier(self.citation_id, self.start, self.end, self.quote)
        ):
            raise ValueError("native quote template must bind its exact source slice")
        return self


class NativeQuoteReference(GraphRecord):
    quote_id: Digest


class QuotedOmittedRelation(GraphRecord):
    kind: Literal["relation"]
    rule: Name
    source: NativeReviewWitness
    target: NativeReviewWitness
    evidence: NativeQuoteReference
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class NativeQuotedSemanticReview(GraphRecord):
    """Independent checks; relationship omission evidence selects a source quote."""

    schema_version: Literal["ghimera.semantic-review/6"] = Field(alias="schema")
    proposal_digest: Digest
    mentions: tuple[IndependentMentionAssessment, ...]
    relations: tuple[IndependentRelationAssessment, ...]
    coverage: Literal["adequate", "incomplete", "uncertain"]
    coverage_reason: Annotated[str, Field(min_length=1, max_length=4096)]
    coverage_findings: tuple[
        Annotated[OmittedMention | QuotedOmittedRelation, Field(discriminator="kind")], ...
    ]
    model_call: ModelCallEvidence | None = None


class NativeQuoteReviewEvidence(GraphRecord):
    response: NativeQuotedSemanticReview
    templates: Annotated[tuple[NativeQuoteTemplate, ...], Field(min_length=1)]


class GroundedSemanticReview(SemanticReview):
    schema_version: Literal["ghimera.semantic-review/3"] = Field(alias="schema")
    mentions: tuple[FactorizedMentionAssessment, ...]
    relations: tuple[GroundedRelationAssessment, ...]
    coverage_findings: tuple[CoverageFinding, ...]
    # Client-owned provenance, never requested from a model. Absent for every
    # old profile, preserving its JSON schema, serialization and prompt identity.
    dimension_response: SkipJsonSchema[IndependentSemanticReview | None] = Field(
        default=None, exclude_if=lambda v: v is None
    )
    quote_response: SkipJsonSchema[NativeQuoteReviewEvidence | None] = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def grounded_dimensions(self) -> "GroundedSemanticReview":
        for mention in self.mentions:
            if mention.verdict != combined_verdict(
                (mention.checks.named_entity, mention.checks.role)
            ):
                raise ValueError("mention summary must match every explicit review dimension")
        if any(item.verdict != item.checks.verdict for item in self.relations):
            raise ValueError("relation summary must match asserted review dimensions")
        if (self.coverage == "incomplete") != bool(self.coverage_findings):
            raise ValueError("incomplete coverage requires concrete native omission witnesses")
        if len({item.content_digest() for item in self.coverage_findings}) != len(
            self.coverage_findings
        ):
            raise ValueError("coverage witnesses must be distinct")
        if self.dimension_response is not None and self.quote_response is not None:
            raise ValueError("only one actual model-facing review payload may be retained")
        observed: IndependentSemanticReview | NativeQuotedSemanticReview | None = (
            self.dimension_response
        )
        findings = self.coverage_findings
        if self.quote_response is not None:
            from ghimera.semantic_quotes import resolve_quote_findings

            observed = self.quote_response.response
            findings = resolve_quote_findings(observed, self.quote_response.templates)
        if observed is not None and (
            observed.model_call is not None
            or observed.proposal_digest != self.proposal_digest
            or observed.coverage != self.coverage
            or observed.coverage_reason != self.coverage_reason
            or (
                self.dimension_response is not None
                and self.dimension_response.coverage_findings != self.coverage_findings
            )
            or findings != self.coverage_findings
            or tuple((item.key, item.reason, item.checks) for item in observed.mentions)
            != tuple((item.key, item.reason, item.checks) for item in self.mentions)
            or tuple((item.index, item.reason, item.checks) for item in observed.relations)
            != tuple((item.index, item.reason, item.checks) for item in self.relations)
        ):
            raise ValueError("derived review must preserve every independent model judgment")
        return self


class ReviewSelection(GraphRecord):
    """Original proposal keys/indices, never renumbered or a reduced proposal."""

    schema_version: Literal["ghimera.review-selection/1"] = Field(alias="schema")
    mention_keys: tuple[MentionKey, ...]
    relation_indices: tuple[Count, ...]
    coverage: bool = Field(strict=True)

    @model_validator(mode="after")
    def distinct(self) -> "ReviewSelection":
        if len(set(self.mention_keys)) != len(self.mention_keys) or len(
            set(self.relation_indices)
        ) != len(self.relation_indices):
            raise ValueError("review selection items must be distinct")
        if self.coverage == bool(self.mention_keys or self.relation_indices):
            raise ValueError("whole-window coverage is a separate call from selected assessments")
        return self


class ReviewPart(GraphRecord):
    selection: ReviewSelection
    review: GroundedSemanticReview

    @model_validator(mode="after")
    def selected(self) -> "ReviewPart":
        selected, review = self.selection, self.review
        if (
            {item.key for item in review.mentions} != set(selected.mention_keys)
            or {item.index for item in review.relations} != set(selected.relation_indices)
            or (
                not selected.coverage
                and (review.coverage != "uncertain" or review.coverage_findings)
            )
            or review.model_call is None
            or review.model_call.task != "semantic_review"
            or review.model_call.outcome != "success"
        ):
            raise ValueError("review part requires exactly its selected successful assessments")
        return self


class BatchedSemanticReview(SemanticReview):
    """Deterministic assembly; model_call is the actual coverage call, not a synthetic call."""

    schema_version: Literal["ghimera.semantic-review/4"] = Field(alias="schema")
    mentions: tuple[FactorizedMentionAssessment, ...]
    relations: tuple[GroundedRelationAssessment, ...]
    coverage_findings: tuple[CoverageFinding, ...]
    parts: Annotated[tuple[ReviewPart, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def complete_parts(self) -> "BatchedSemanticReview":
        coverage = self.parts[-1]
        if (
            not coverage.selection.coverage
            or any(part.selection.coverage for part in self.parts[:-1])
            or any(part.review.proposal_digest != self.proposal_digest for part in self.parts)
            or self.mentions != tuple(item for part in self.parts for item in part.review.mentions)
            or self.relations
            != tuple(item for part in self.parts for item in part.review.relations)
            or self.coverage != coverage.review.coverage
            or self.coverage_reason != coverage.review.coverage_reason
            or self.coverage_findings != coverage.review.coverage_findings
            or self.model_call != coverage.review.model_call
        ):
            raise ValueError(
                "assembled review must preserve its exact parts and final coverage call"
            )
        return self


def review_observations(review: SemanticReview) -> tuple[SemanticReview, ...]:
    return (
        tuple(part.review for part in review.parts)
        if isinstance(review, BatchedSemanticReview)
        else (review,)
    )


def restore_review(review: SemanticReview) -> SemanticReview:
    """Revalidate known versioned data without erasing subclass evidence."""
    if isinstance(review, BatchedSemanticReview):
        return BatchedSemanticReview.model_validate(review.model_dump())
    if isinstance(review, GroundedSemanticReview):
        return GroundedSemanticReview.model_validate(review.model_dump())
    if isinstance(review, FactorizedSemanticReview):
        return FactorizedSemanticReview.model_validate(review.model_dump())
    return SemanticReview.model_validate(review.model_dump())


def review_profile_matches(
    verification: SemanticVerificationConfig, review: SemanticReview
) -> bool:
    if isinstance(review, BatchedSemanticReview):
        return verification.schema_version == "ghimera.semantic-verification/4"
    if isinstance(review, GroundedSemanticReview):
        return (
            (verification.prompt_profile == "native_quote_checks")
            == (review.quote_response is not None)
            and (verification.prompt_profile == "independent_dimension_checks")
            == (review.dimension_response is not None)
            and verification.schema_version
            in {
                "ghimera.semantic-verification/3",
                "ghimera.semantic-verification/4",
            }
        )
    if isinstance(review, FactorizedSemanticReview):
        return verification.schema_version == "ghimera.semantic-verification/2"
    return verification.schema_version == "ghimera.semantic-verification/1"


ExclusionReason = Literal[
    "native_span_missing",
    "unknown_role",
    "citation_mismatch",
    "review_unsupported",
    "review_ambiguous",
    "endpoint_quarantined",
    "unknown_relation",
    "endpoint_role_mismatch",
]


class MentionExclusion(GraphRecord):
    key: MentionKey
    reason: ExclusionReason


class RelationExclusion(GraphRecord):
    index: Count
    reason: ExclusionReason


class SemanticWindow(GraphRecord):
    schema_version: Literal["ghimera.semantic-window/1", "ghimera.semantic-window/2"] = Field(
        alias="schema"
    )
    policy_digest: Digest
    source_url: Text
    document_sha256: Digest
    text_sha256: Digest
    graph_document_id: Text
    start: Count
    end: Positive
    omitted_chars: Count
    proposal: SemanticProposal
    entities: tuple[SemanticEntity, ...]
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    held_edges: tuple[GraphEdge, ...]
    review: (
        BatchedSemanticReview
        | GroundedSemanticReview
        | FactorizedSemanticReview
        | SemanticReview
        | None
    ) = Field(default=None, exclude_if=lambda v: v is None)
    excluded_mentions: tuple[MentionExclusion, ...] = Field(default=(), exclude_if=lambda v: not v)
    excluded_relations: tuple[RelationExclusion, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )

    @model_validator(mode="after")
    def evidence_bound(self) -> "SemanticWindow":
        call = self.proposal.model_call
        if (
            self.end <= self.start
            or call is None
            or call.task != "semantic_extract"
            or call.outcome != "success"
        ):
            raise ValueError("semantic windows require a successful bounded source call")
        if (self.schema_version == "ghimera.semantic-window/2") != (self.review is not None):
            raise ValueError(
                "reviewed windows require semantic-window/2 and actual review evidence"
            )
        if self.review is None and (self.excluded_mentions or self.excluded_relations):
            raise ValueError("legacy windows cannot silently quarantine proposed items")
        if self.review is not None:
            review_call = self.review.model_call
            if (
                review_call is None
                or review_call.task != "semantic_review"
                or review_call.outcome != "success"
                or self.review.proposal_digest != self.proposal.content_digest()
                or {item.key for item in self.review.mentions}
                != {item.key for item in self.proposal.mentions}
                or {item.index for item in self.review.relations}
                != set(range(len(self.proposal.relations)))
                or len({item.key for item in self.excluded_mentions}) != len(self.excluded_mentions)
                or len({item.index for item in self.excluded_relations})
                != len(self.excluded_relations)
                or {item.key for item in self.entities}
                | {item.key for item in self.excluded_mentions}
                != {item.key for item in self.proposal.mentions}
                or bool(
                    {item.key for item in self.entities}
                    & {item.key for item in self.excluded_mentions}
                )
                or not {item.index for item in self.excluded_relations}
                <= set(range(len(self.proposal.relations)))
            ):
                raise ValueError("independent review must bind the complete original proposal")
        for entity in self.entities:
            evidence = entity.evidence
            if (
                evidence.document_id != self.graph_document_id
                or evidence.document_sha256 != self.document_sha256
                or evidence.text_sha256 != self.text_sha256
                or not self.start <= evidence.start < evidence.end <= self.end
                or entity.node.label != evidence.quote
            ):
                raise ValueError("semantic mentions require native evidence in this window")
        for edge in self.edges + self.held_edges:
            if (
                edge.claim_status != "model_asserted"
                or edge.model_request_sha256 != call.request_sha256
            ):
                raise ValueError(
                    "extracted edges are versioned model assertions, never corroborated facts"
                )
        return self
