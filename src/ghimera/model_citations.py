"""Model-selected template IDs become exact native application citations.

The language model chooses support; it never regenerates hashes, offsets or
source quotes in this wire mode. Resolution is against this call's context
only, not an archive-wide lookup or a similarity-based quotation repair.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ghimera.models import Record
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research_types import (
    AnswerDraft,
    Assessment,
    Citation,
    Claim,
    Coverage,
    QuestionId,
    Text,
)


def citation_id(citation: Citation) -> str:
    return "cite:" + citation.content_digest()


class CitationReference(Record):
    citation_id: Annotated[str, Field(pattern=r"^cite:[0-9a-f]{64}$")]


class ReferencedCoverage(Record):
    question_id: QuestionId
    status: Literal["answered", "unresolved", "contradicted"]
    reason: Text
    citations: tuple[CitationReference, ...]


class ReferencedAssessment(Record):
    coverage: tuple[ReferencedCoverage, ...]


class ReferencedClaim(Record):
    text: Text
    question_ids: Annotated[tuple[QuestionId, ...], Field(min_length=1)]
    citations: Annotated[tuple[CitationReference, ...], Field(min_length=1)]


class ReferencedAnswer(Record):
    claims: Annotated[tuple[ReferencedClaim, ...], Field(min_length=1)]
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


def referenced_output(output: type[BaseModel]) -> type[BaseModel]:
    if output is Assessment:
        return ReferencedAssessment
    if output is AnswerDraft:
        return ReferencedAnswer
    return output


class ModelCitationResolver:
    def __init__(self, citations: tuple[Citation, ...]) -> None:
        self._citations = {citation_id(item): item for item in citations}

    def _resolve(self, references: tuple[CitationReference, ...]) -> tuple[Citation, ...]:
        result = []
        for reference in references:
            citation = self._citations.get(reference.citation_id)
            if citation is None:
                raise GhimeraRefused(RefusalCode.UNSUPPORTED_ANSWER)
            result.append(citation)
        return tuple(result)

    def content(self, raw: str, output: type[BaseModel]) -> str:
        if output is Assessment:
            response = ReferencedAssessment.model_validate_json(raw)
            # Application validation still enforces supported status, question
            # coverage and exact source bindings. No wire condition weakens it.
            result = Assessment(
                coverage=tuple(
                    Coverage(
                        question_id=item.question_id,
                        status=item.status,
                        reason=item.reason,
                        citations=self._resolve(item.citations),
                    )
                    for item in response.coverage
                )
            )
            return result.model_dump_json()
        if output is AnswerDraft:
            answer = ReferencedAnswer.model_validate_json(raw)
            draft = AnswerDraft(
                claims=tuple(
                    Claim(
                        text=item.text,
                        question_ids=item.question_ids,
                        citations=self._resolve(item.citations),
                    )
                    for item in answer.claims
                ),
                confidence=answer.confidence,
            )
            return draft.model_dump_json()
        return raw
