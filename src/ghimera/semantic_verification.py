"""Bind independent semantic assessments to one unchanged native proposal."""

from ghimera.config import GhimeraConfig
from ghimera.evidence_context import ContextSelector, native_citation
from ghimera.model_config import ModelServiceConfig
from ghimera.models import Document
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research_types import Citation
from ghimera.semantic_types import SEMANTIC_REVIEW_REVISION, SemanticProposal, SemanticReview


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
    service = review_service(config)
    call = review.model_call
    citation = native_citation(document, start, end)
    context = ContextSelector(
        service.context.model_copy(update={"max_documents": 1, "max_windows_per_document": 1})
    ).build(intent, (document,), required=(citation,))
    if (
        call is None
        or call.service != service
        or call.task != "semantic_review"
        or call.prompt_revision != SEMANTIC_REVIEW_REVISION
        or call.outcome != "success"
        or call.context_sha256 != context.content_digest()
        or call.selected_spans != ((citation.document_id, start, end),)
        or call.omitted_chars != len(document.extracted.text) - (end - start)
        or review.proposal_digest != proposal.content_digest()
        or {item.key for item in review.mentions} != {item.key for item in proposal.mentions}
        or {item.index for item in review.relations} != set(range(len(proposal.relations)))
    ):
        raise GhimeraRefused(RefusalCode.SEMANTIC_EXTRACTION_FAILED)
