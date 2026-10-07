"""Partition all original assessments and assemble actual observations, never retries."""

from pydantic import JsonValue

from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.semantic_types import (
    BatchedSemanticReview,
    ReviewPart,
    ReviewSelection,
    SemanticProposal,
    SemanticVerificationConfig,
)


def validate_selection(
    policy: SemanticVerificationConfig, proposal: SemanticProposal, selection: ReviewSelection
) -> None:
    if (
        policy.schema_version != "ghimera.semantic-verification/4"
        or policy.max_mentions_per_call is None
        or policy.max_relations_per_call is None
        or len(selection.mention_keys) > policy.max_mentions_per_call
        or len(selection.relation_indices) > policy.max_relations_per_call
        or not set(selection.mention_keys) <= {item.key for item in proposal.mentions}
        or not set(selection.relation_indices) <= set(range(len(proposal.relations)))
    ):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)


def review_selections(
    policy: SemanticVerificationConfig, proposal: SemanticProposal
) -> tuple[ReviewSelection, ...]:
    mentions, relations = policy.max_mentions_per_call, policy.max_relations_per_call
    if (
        policy.schema_version != "ghimera.semantic-verification/4"
        or mentions is None
        or relations is None
    ):
        raise ValueError("review partitioning requires its explicit version-4 policy")
    selections = tuple(
        ReviewSelection(
            schema="ghimera.review-selection/1",
            mention_keys=tuple(item.key for item in proposal.mentions[start : start + mentions]),
            relation_indices=(),
            coverage=False,
        )
        for start in range(0, len(proposal.mentions), mentions)
    ) + tuple(
        ReviewSelection(
            schema="ghimera.review-selection/1",
            mention_keys=(),
            relation_indices=tuple(range(start, min(start + relations, len(proposal.relations)))),
            coverage=False,
        )
        for start in range(0, len(proposal.relations), relations)
    )
    return selections + (
        ReviewSelection(
            schema="ghimera.review-selection/1", mention_keys=(), relation_indices=(), coverage=True
        ),
    )


def assemble_review(
    proposal: SemanticProposal, parts: tuple[ReviewPart, ...]
) -> BatchedSemanticReview:
    if not parts:
        raise ValueError("review assembly needs its observed coverage call")
    if {item.key for part in parts for item in part.review.mentions} != {
        item.key for item in proposal.mentions
    } or {item.index for part in parts for item in part.review.relations} != set(
        range(len(proposal.relations))
    ):
        raise ValueError("review assembly cannot omit original proposed assessments")
    coverage = parts[-1].review
    return BatchedSemanticReview(
        schema="ghimera.semantic-review/4",
        proposal_digest=proposal.content_digest(),
        mentions=tuple(item for part in parts for item in part.review.mentions),
        relations=tuple(item for part in parts for item in part.review.relations),
        coverage=coverage.coverage,
        coverage_reason=coverage.coverage_reason,
        coverage_findings=coverage.coverage_findings,
        model_call=coverage.model_call,
        parts=parts,
    )


def bound_review_schema(
    schema: dict[str, JsonValue], selection: ReviewSelection, max_findings: int
) -> None:
    """Constrain generated arrays to the same original keys/indices checked at replay."""
    properties, definitions = schema.get("properties"), schema.get("$defs")
    if not isinstance(properties, dict) or not isinstance(definitions, dict):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    for name, count in (
        ("mentions", len(selection.mention_keys)),
        ("relations", len(selection.relation_indices)),
    ):
        field = properties.get(name)
        if not isinstance(field, dict):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        field["minItems"] = field["maxItems"] = count
    findings, coverage = properties.get("coverage_findings"), properties.get("coverage")
    if not isinstance(findings, dict) or not isinstance(coverage, dict):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    findings["maxItems"] = max_findings if selection.coverage else 0
    if not selection.coverage:
        coverage["enum"] = ["uncertain"]
    mention_values: list[JsonValue] = list(selection.mention_keys)
    relation_values: list[JsonValue] = list(selection.relation_indices)
    for record, name, values in (
        ("FactorizedMentionAssessment", "key", mention_values),
        ("GroundedRelationAssessment", "index", relation_values),
    ):
        definition = definitions.get(record)
        fields = definition.get("properties") if isinstance(definition, dict) else None
        field = fields.get(name) if isinstance(fields, dict) else None
        if not isinstance(field, dict):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if values:
            field["enum"] = values
