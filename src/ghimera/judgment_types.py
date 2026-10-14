"""Opt-in, source-bound document context and separately attributed disposition."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.scoring_types import WindowSimilarity

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
Positive = Annotated[int, Field(strict=True, gt=0)]
Decision = Literal["accept", "reject", "hold"]
# A reading's relevance grade is metadata on a KEPT page, never a reason to drop it.
Relevance = Literal["direct", "supporting", "background", "unrelated", "unjudged"]
GRADE_ORDER: tuple[Relevance, ...] = ("direct", "supporting", "background", "unjudged", "unrelated")
# Conservative: the one recorded on-topic scoring observation in the test
# fixtures (retained_organization_judgment.json) reads 0.88, and e5/bge-family
# encoders put plainly unrelated text in the 0.70s; 0.80 relabels only a page
# that the encoder already reads as close to the intent.
DEFAULT_KEEP_AT_OR_ABOVE = 0.80
ReadingReason = Literal[
    "native_model_decision",
    "incomplete_rejection_hold",
    "incomplete_unrelated_hold",
    "similarity_floor",
    "unrelated_kept",
    "undecided_after_second_look",
]


def model_grade(decision: Decision, relevance: Relevance | None) -> Relevance | None:
    """The grade a model reply states; a legacy ``reject`` reads as ``unrelated``."""
    if relevance is not None:
        return relevance
    return "unrelated" if decision == "reject" else None


def reading_disposition(
    decision: Decision,
    relevance: Relevance | None,
    *,
    omitted_chars: int,
    second_look: bool,
    document_cosine: float | None,
    keep_at_or_above: float | None,
) -> tuple[Literal["accept", "hold"], Relevance | None, ReadingReason]:
    """Client disposition of one reading: every page is kept, only its grade varies.

    ``hold`` asks for the second, wider look; after the second look nothing is held.
    """
    grade = model_grade(decision, relevance)
    if decision == "hold":
        if second_look:
            return "accept", "background", "undecided_after_second_look"
        return "hold", None, "native_model_decision"
    if grade == "unrelated":
        if (
            keep_at_or_above is not None
            and document_cosine is not None
            and document_cosine >= keep_at_or_above
        ):
            return "accept", "background", "similarity_floor"
        if omitted_chars > 0 and not second_look:
            return "hold", None, "incomplete_unrelated_hold"
        return "accept", "unrelated", "unrelated_kept"
    return "accept", grade, "native_model_decision"


class JudgmentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class DocumentJudgmentConfig(JudgmentRecord):
    schema_version: Literal[
        "ghimera.document-judgment/1",
        "ghimera.document-judgment/2",
        "ghimera.document-judgment/3",
    ] = Field(alias="schema")
    selection: Literal["scored_native_windows"]
    max_windows: Positive
    first_look_max_chars: Positive
    expanded_look_max_chars: Positive
    padding_chars: Count
    incomplete_rejection: Literal["hold"]
    # A model "unrelated" grade on a page whose intent cosine is at or above this
    # floor is relabelled "background". Unset means DEFAULT_KEEP_AT_OR_ABOVE.
    keep_at_or_above: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    prompt_profile: Literal["contribution_relevance"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    input_layout: Literal["source_first"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def bounds(self) -> "DocumentJudgmentConfig":
        if self.first_look_max_chars > self.expanded_look_max_chars:
            raise ValueError("expanded judgment cannot reduce its declared context allowance")
        if self.schema_version == "ghimera.document-judgment/1":
            if "prompt_profile" in self.model_fields_set:
                raise ValueError("legacy scored judgment does not select a prompt profile")
        elif self.prompt_profile != "contribution_relevance":
            raise ValueError("scored judgment /2 or /3 requires explicit contribution_relevance")
        if self.schema_version == "ghimera.document-judgment/3":
            if self.input_layout != "source_first":
                raise ValueError("scored judgment /3 requires explicit source_first input layout")
        elif "input_layout" in self.model_fields_set:
            raise ValueError("scored judgment /1 and /2 do not select an input layout")
        return self

    @property
    def effective_keep_at_or_above(self) -> float:
        return DEFAULT_KEEP_AT_OR_ABOVE if self.keep_at_or_above is None else self.keep_at_or_above

    @property
    def effective_prompt_revision(self) -> str:
        return "ghimera-scored-document-judgment/" + self.schema_version.rsplit("/", 1)[1]


class ScoringNativeReading(JudgmentRecord):
    """Native scorer attribution to its exact observed parser operation."""

    schema_version: Literal["ghimera.scoring-native-reading/1"] = Field(alias="schema")
    source_url: Annotated[str, Field(min_length=1)]
    source_sha256: Digest
    text_sha256: Digest
    parser_sequence: Count
    parser_sha256: Digest
    native_text: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def exact_reading(self) -> "ScoringNativeReading":
        if hashlib.sha256(self.native_text.encode()).hexdigest() != self.text_sha256:
            raise ValueError("retained native reading must bind its exact original text digest")
        return self


class ScoringSourceBinding(JudgmentRecord):
    schema_version: Literal["ghimera.scoring-source/1"] = Field(alias="schema")
    source_url: Annotated[str, Field(min_length=1)]
    source_sha256: Digest
    text_sha256: Digest
    reading_sequence: Count
    reading_sha256: Digest


class ScoredNativeWindow(JudgmentRecord):
    start: Count
    end: Positive
    text: Annotated[str, Field(min_length=1)]
    text_sha256: Digest
    anchors: Annotated[tuple[WindowSimilarity, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def exact_text(self) -> "ScoredNativeWindow":
        if (
            self.end <= self.start
            or len(self.text) != self.end - self.start
            or (hashlib.sha256(self.text.encode()).hexdigest() != self.text_sha256)
        ):
            raise ValueError("selected window must bind its exact unchanged native text")
        if any(
            not self.start <= seed.start < seed.end <= self.end
            or (
                hashlib.sha256(
                    self.text[seed.start - self.start : seed.end - self.start].encode()
                ).hexdigest()
                != seed.text_sha256
            )
            for seed in self.anchors
        ):
            raise ValueError("selected anchors must bind exact original scored native spans")
        if tuple((seed.start, seed.end) for seed in self.anchors) != tuple(
            sorted(set((seed.start, seed.end) for seed in self.anchors))
        ):
            raise ValueError("selected anchors must remain distinct and in original order")
        return self


class ScoredNativeContext(JudgmentRecord):
    """Reusable selection facts, independent of judgment or semantic decisions."""

    schema_version: Literal["ghimera.scored-native-context/1"] = Field(alias="schema")
    source_url: Annotated[str, Field(min_length=1)]
    source_sha256: Digest
    text_sha256: Digest
    goal_sha256: Digest
    references_sha256: Digest
    scoring_sequence: Count
    scoring_sha256: Digest
    scoring_source: ScoringSourceBinding
    total_chars: Positive
    selected_chars: Positive
    omitted_chars: Count
    windows: Annotated[tuple[ScoredNativeWindow, ...], Field(min_length=1)]
    omissions: tuple[Literal["max_chars", "max_windows", "scoring_omissions"], ...]

    @model_validator(mode="after")
    def native_union(self) -> "ScoredNativeContext":
        spans = tuple((window.start, window.end) for window in self.windows)
        if (
            spans != tuple(sorted(set(spans)))
            or any(end > self.total_chars for _, end in spans)
            or any(left[1] >= right[0] for left, right in zip(spans, spans[1:], strict=False))
        ):
            raise ValueError("selected windows must be ordered, distinct and disjoint")
        if (
            sum(end - start for start, end in spans) != self.selected_chars
            or (self.total_chars != self.selected_chars + self.omitted_chars)
            or len(set(self.omissions)) != len(self.omissions)
        ):
            raise ValueError("selected and omitted native characters must reconcile exactly")
        return self


class JudgmentContextReservation(JudgmentRecord):
    schema_version: Literal["ghimera.judgment-context-reservation/1"] = Field(alias="schema")
    context: ScoredNativeContext
    policy_sha256: Digest
    second_look: bool
    input_sha256: Digest
    input_bytes: Positive


class DocumentJudgmentEvidence(JudgmentRecord):
    """Client attribution never overwrites the original model verdict/ACK."""

    schema_version: Literal["ghimera.document-judgment-evidence/1"] = Field(alias="schema")
    policy_sha256: Digest
    context: ScoredNativeContext
    context_sequence: Count
    second_look: bool
    intent_sequence: Count
    ack_sequence: Count
    output_sha256: Digest
    original_model_decision: Decision
    original_relevance: Relevance | None = Field(default=None, exclude_if=lambda v: v is None)
    client_disposition: Decision
    client_relevance: Relevance | None = Field(default=None, exclude_if=lambda v: v is None)
    document_cosine: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    keep_at_or_above: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    reason: ReadingReason

    @model_validator(mode="after")
    def separate_disposition(self) -> "DocumentJudgmentEvidence":
        if self.ack_sequence <= self.intent_sequence:
            raise ValueError("client hold must remain distinct from its original model decision")
        expected = reading_disposition(
            self.original_model_decision,
            self.original_relevance,
            omitted_chars=self.context.omitted_chars,
            second_look=self.second_look,
            document_cosine=self.document_cosine,
            keep_at_or_above=self.keep_at_or_above,
        )
        if (self.client_disposition, self.client_relevance, self.reason) == expected:
            return self
        # Ledgers written before graded readings: reject could drop, hold could persist.
        held = self.original_model_decision == "reject" and self.context.omitted_chars > 0
        legacy = (
            self.original_relevance is None
            and self.client_relevance is None
            and self.document_cosine is None
            and self.keep_at_or_above is None
            and self.client_disposition == ("hold" if held else self.original_model_decision)
            and self.reason == ("incomplete_rejection_hold" if held else "native_model_decision")
        )
        if not legacy:
            raise ValueError("client hold must remain distinct from its original model decision")
        return self
