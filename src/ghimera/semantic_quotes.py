"""Resolve declared native quote choices without changing a model's judgments."""

import hashlib
import json
import re

from pydantic import JsonValue

from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.semantic_types import (
    CoverageFinding,
    GroundedSemanticReview,
    IndependentSemanticReview,
    NativeQuoteChoice,
    NativeQuotedSemanticReview,
    NativeQuoteReviewEvidence,
    NativeQuoteTemplate,
    NativeReviewWitness,
    OmittedMention,
    OmittedRelation,
)


def quote_identifier(reference: str, start: int, end: int, quote: str) -> str:
    packet = json.dumps(
        ("ghimera.native-quote/1", reference, start, end, quote),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(packet.encode()).hexdigest()


def native_quote_templates(quote: str, reference: str) -> tuple[NativeQuoteTemplate, ...]:
    """Full-window choice preserves cross-clause scope; exact clauses aid precision.

    Boundaries retain every native character, including whitespace and line breaks.
    No sentence is filtered by language, relevance, entity presence or size.
    Repeated identical clauses retain their separate original occurrences.
    """
    spans = [(0, len(quote))]
    start = 0
    for boundary in re.finditer(r"[。！？.!?\n]+", quote):
        end = boundary.end()
        if quote[start:end].strip():
            spans.append((start, end))
        start = end
    if quote[start:].strip():
        spans.append((start, len(quote)))
    values = []
    for start, end in dict.fromkeys(spans):
        text = quote[start:end]
        matches = tuple(re.finditer(re.escape(text), quote))
        occurrence = next(index for index, match in enumerate(matches) if match.start() == start)
        values.append(
            NativeQuoteTemplate(
                quote_id=quote_identifier(reference, start, end, text),
                citation_id=reference,
                start=start,
                end=end,
                quote=text,
                occurrence=occurrence,
            )
        )
    return tuple(values)


def resolve_quote_findings(
    response: NativeQuotedSemanticReview, templates: tuple[NativeQuoteTemplate, ...]
) -> tuple[CoverageFinding, ...]:
    by_id = {template.quote_id: template for template in templates}
    if len(by_id) != len(templates):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    values: list[CoverageFinding] = []
    for finding in response.coverage_findings:
        if isinstance(finding, OmittedMention):
            values.append(finding)
            continue
        template = by_id.get(finding.evidence.quote_id)
        if template is None:
            raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
        values.append(
            OmittedRelation(
                kind="relation",
                rule=finding.rule,
                source=finding.source,
                target=finding.target,
                reason=finding.reason,
                evidence=NativeReviewWitness(
                    surface=template.quote,
                    citation_id=template.citation_id,
                    occurrence=template.occurrence,
                ),
            )
        )
    return tuple(values)


def derive_quote_review(
    observed: NativeQuotedSemanticReview, templates: tuple[NativeQuoteTemplate, ...]
) -> GroundedSemanticReview:
    from ghimera.semantic_batching import derive_independent_review

    # This is an internal normalization, never presented as another model call.
    normalized = IndependentSemanticReview(
        schema="ghimera.semantic-review/5",
        proposal_digest=observed.proposal_digest,
        mentions=observed.mentions,
        relations=observed.relations,
        coverage=observed.coverage,
        coverage_reason=observed.coverage_reason,
        coverage_findings=resolve_quote_findings(observed, templates),
        model_call=observed.model_call,
    )
    raw = derive_independent_review(normalized).model_dump()
    raw["dimension_response"] = None
    raw["quote_response"] = NativeQuoteReviewEvidence(
        response=observed.model_copy(update={"model_call": None}),
        templates=templates,
    )
    return GroundedSemanticReview.model_validate(raw)


def bind_quote_schema(
    schema: dict[str, JsonValue], templates: tuple[NativeQuoteTemplate | NativeQuoteChoice, ...]
) -> None:
    definitions = schema.get("$defs")
    record = definitions.get("NativeQuoteReference") if isinstance(definitions, dict) else None
    properties = record.get("properties") if isinstance(record, dict) else None
    choice = properties.get("quote_id") if isinstance(properties, dict) else None
    if not isinstance(choice, dict) or not templates:
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    choice["enum"] = [item.quote_id for item in templates]
