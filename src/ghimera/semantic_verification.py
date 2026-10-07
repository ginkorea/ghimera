"""Bind independent semantic assessments to one unchanged native proposal."""

from ghimera.config import GhimeraConfig
from ghimera.evidence_context import ContextSelector, native_citation
from ghimera.model_citations import citation_id
from ghimera.model_config import ModelServiceConfig
from ghimera.models import Document
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research_types import Citation
from ghimera.semantic_grounding import validate_grounded_review
from ghimera.semantic_types import (
    BatchedSemanticReview,
    GroundedSemanticReview,
    ReviewPart,
    ReviewSelection,
    SemanticProposal,
    SemanticReview,
    review_profile_matches,
)


def validate_proposal(
    config: GhimeraConfig,
    proposal: SemanticProposal,
    document: Document,
    start: int,
    end: int,
    intent: str,
) -> Citation:
    policy, models, call = config.semantics, config.models, proposal.model_call
    if (
        policy is None
        or models is None
        or call is None
        or call.service != models.service(policy.model_role)
        or call.task != "semantic_extract"
        or call.prompt_revision != policy.effective_prompt_revision
        or call.outcome != "success"
        or len(proposal.mentions) > policy.max_mentions_per_window
        or len(proposal.relations) > policy.max_relations_per_window
        or not 0 <= start < end <= len(document.extracted.text)
        or end - start > policy.window_chars
    ):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    citation = native_citation(document, start, end)
    context = ContextSelector(
        call.service.context.model_copy(update={"max_documents": 1, "max_windows_per_document": 1})
    ).build(intent, (document,), required=(citation,))
    if (
        call.context_sha256 != context.content_digest()
        or call.selected_spans != ((citation.document_id, start, end),)
        or call.omitted_chars != len(document.extracted.text) - (end - start)
    ):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    return citation


def review_service(config: GhimeraConfig) -> ModelServiceConfig:
    policy, models = config.semantics, config.models
    if policy is None or policy.verification is None or models is None:
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    return models.service(policy.verification.model_role)


def validate_review(
    config: GhimeraConfig,
    proposal: SemanticProposal,
    review: SemanticReview,
    document: Document,
    start: int,
    end: int,
    intent: str,
) -> None:
    _validate_review_call(
        config,
        proposal,
        review,
        document,
        start,
        end,
        intent,
        {item.key for item in proposal.mentions},
        set(range(len(proposal.relations))),
    )
    policy = config.semantics
    if policy is None or policy.verification is None:
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    if policy.verification.schema_version == "ghimera.semantic-verification/4":
        from ghimera.semantic_batching import review_selections

        if not isinstance(review, BatchedSemanticReview) or tuple(
            part.selection for part in review.parts
        ) != review_selections(policy.verification, proposal):
            raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
        for part in review.parts:
            validate_review_part(
                config, proposal, part.review, part.selection, document, start, end, intent
            )
    elif isinstance(review, BatchedSemanticReview):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)


def validate_review_part(
    config: GhimeraConfig,
    proposal: SemanticProposal,
    review: GroundedSemanticReview,
    selection: ReviewSelection,
    document: Document,
    start: int,
    end: int,
    intent: str,
) -> None:
    policy = config.semantics
    verification = policy.verification if policy is not None else None
    if policy is None or verification is None:
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    from ghimera.semantic_batching import validate_selection

    validate_selection(verification, proposal, selection)
    ReviewPart(selection=selection, review=review)
    _validate_review_call(
        config,
        proposal,
        review,
        document,
        start,
        end,
        intent,
        set(selection.mention_keys),
        set(selection.relation_indices),
    )


def _validate_review_call(
    config: GhimeraConfig,
    proposal: SemanticProposal,
    review: SemanticReview,
    document: Document,
    start: int,
    end: int,
    intent: str,
    mention_keys: set[str],
    relation_indices: set[int],
) -> None:
    service = review_service(config)
    policy = config.semantics
    if policy is None or policy.verification is None:
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    call = review.model_call
    citation = native_citation(document, start, end)
    context = ContextSelector(
        service.context.model_copy(update={"max_documents": 1, "max_windows_per_document": 1})
    ).build(intent, (document,), required=(citation,))
    if (
        call is None
        or call.service != service
        or call.task != "semantic_review"
        or call.prompt_revision != policy.verification.effective_prompt_revision
        or not review_profile_matches(policy.verification, review)
        or call.outcome != "success"
        or call.context_sha256 != context.content_digest()
        or call.selected_spans != ((citation.document_id, start, end),)
        or call.omitted_chars != len(document.extracted.text) - (end - start)
        or review.proposal_digest != proposal.content_digest()
        or {item.key for item in review.mentions} != mention_keys
        or {item.index for item in review.relations} != relation_indices
    ):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
    if isinstance(review, GroundedSemanticReview):
        validate_grounded_review(policy, proposal, review, citation.quote, citation_id(citation))
