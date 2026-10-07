"""Deterministic witnesses for review/3; no factual upgrades or model retries."""

import re

from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.semantic_types import (
    CoverageFinding,
    GroundedSemanticReview,
    NativeReviewWitness,
    OmittedMention,
    SemanticConfig,
    SemanticProposal,
)


def witness_span(witness: NativeReviewWitness, quote: str, reference: str) -> tuple[int, int]:
    matches = tuple(re.finditer(re.escape(witness.surface), quote))
    if witness.citation_id != reference or witness.occurrence >= len(matches):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    match = matches[witness.occurrence]
    return match.start(), match.end()


def validate_grounded_review(
    policy: SemanticConfig,
    proposal: SemanticProposal,
    review: GroundedSemanticReview,
    quote: str,
    reference: str,
) -> None:
    verification = policy.verification
    if (
        verification is None
        or verification.max_coverage_findings is None
        or len(review.coverage_findings) > verification.max_coverage_findings
    ):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    for item in review.relations:
        original = proposal.relations[item.index]
        asserted = original.valid_from is not None or original.valid_to is not None
        if item.checks.validity.asserted != asserted:
            raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    validate_coverage_findings(
        policy, review.coverage_findings, quote, reference, proposal=proposal
    )


def validate_coverage_findings(
    policy: SemanticConfig,
    findings: tuple[CoverageFinding, ...],
    quote: str,
    reference: str,
    *,
    proposal: SemanticProposal | None = None,
) -> None:
    verification = policy.verification
    if (
        verification is None
        or verification.schema_version != "ghimera.semantic-verification/3"
        or verification.max_coverage_findings is None
        or len(findings) > verification.max_coverage_findings
    ):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    mentions = proposal.mentions if proposal is not None else ()
    relations = proposal.relations if proposal is not None else ()
    by_key = {mention.key: mention for mention in mentions}
    covered_mentions = {(item.role, item.surface, item.occurrence) for item in mentions}
    covered_relations = {
        (
            item.rule,
            by_key[item.source].surface,
            by_key[item.source].occurrence,
            by_key[item.target].surface,
            by_key[item.target].occurrence,
        )
        for item in relations
    }
    seen_mentions: set[tuple[str, str, int]] = set()
    seen_relations: set[tuple[str, str, int, str, int]] = set()
    for finding in findings:
        if isinstance(finding, OmittedMention):
            witness_span(finding, quote, reference)
            mention = (finding.role, finding.surface, finding.occurrence)
            if (
                finding.role not in policy.entity_roles
                or mention in covered_mentions
                or mention in seen_mentions
            ):
                raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
            seen_mentions.add(mention)
        else:
            source = witness_span(finding.source, quote, reference)
            target = witness_span(finding.target, quote, reference)
            evidence = witness_span(finding.evidence, quote, reference)
            relation = (
                finding.rule,
                finding.source.surface,
                finding.source.occurrence,
                finding.target.surface,
                finding.target.occurrence,
            )
            if (
                finding.rule not in policy.relation_rules
                or relation in covered_relations
                or relation in seen_relations
                or not evidence[0] <= source[0] < source[1] <= evidence[1]
                or not evidence[0] <= target[0] < target[1] <= evidence[1]
            ):
                raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
            seen_relations.add(relation)
