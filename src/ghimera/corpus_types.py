"""Searchable passages retain exact native offsets, not generated paraphrases."""

import hashlib
from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.embedding_types import EncodingCall, EncodingRecoveryEvidence
from ghimera.models import Document
from ghimera.retrieval import RetrievalEvidence
from ghimera.visual_types import ImageRegion

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
PassageKind = Literal["native", "reviewed_pdf_transcription", "image_ocr", "visual_claim"]


class CorpusRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)


@dataclass(frozen=True)
class BoundCorpusDocument:
    """Validate/hash an immutable source once, not once per passage of a large PDF."""

    document: Document
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        document = Document.model_validate(self.document.model_dump())
        object.__setattr__(self, "document", document)
        object.__setattr__(
            self, "identity", hashlib.sha256(document.model_dump_json().encode()).hexdigest()
        )


class CorpusPassage(CorpusRecord):
    document_id: Digest
    source_url: str
    source_sha256: Digest
    kind: PassageKind
    image_sha256: Digest | None
    claim_index: Count | None
    start: Count
    end: Annotated[int, Field(strict=True, gt=0)]
    text: Annotated[str, Field(min_length=1)]
    language: str
    regions: tuple[ImageRegion, ...]
    page_indices: tuple[Count, ...] = Field(default=(), exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def shape(self) -> "CorpusPassage":
        document_text = self.kind in {"native", "reviewed_pdf_transcription"}
        if (
            self.end <= self.start
            or len(self.text) != self.end - self.start
            or not self.text.strip()
            or document_text != (self.image_sha256 is None)
            or (self.kind == "visual_claim") != (self.claim_index is not None)
            or (document_text and self.regions)
            or (self.kind == "reviewed_pdf_transcription") != bool(self.page_indices)
            or tuple(sorted(set(self.page_indices))) != self.page_indices
        ):
            raise ValueError("passages require an exact source-bound reading or visual text span")
        return self

    def validate_source(self, document: Document) -> None:
        self.validate_binding(BoundCorpusDocument(document))

    def validate_binding(self, source: BoundCorpusDocument) -> None:
        document = source.document
        if (
            document.verdict.decision != "accept"
            or document.url != self.source_url
            or document.sha256 != self.source_sha256
            or source.identity != self.document_id
            or self.language != document.verdict.language
        ):
            raise ValueError("passage identity must bind the accepted original document")
        if self.kind in {"native", "reviewed_pdf_transcription"}:
            native = document.extracted.text
            transcription = document.extracted.pdf_transcription
            expected_kind = "reviewed_pdf_transcription" if transcription is not None else "native"
            if self.kind != expected_kind or self.page_indices != (
                transcription.cited_pages(self.start, self.end) if transcription is not None else ()
            ):
                raise ValueError("passage must preserve the actual reading basis and source pages")
        else:
            image = next((row for row in document.images if row.sha256 == self.image_sha256), None)
            if image is None:
                raise ValueError("visual passage is missing its retained image")
            if self.kind == "image_ocr":
                native = image.ocr.text
                expected = tuple(span.region for span in image.ocr.spans)
            else:
                interpretation = image.interpretation
                if (
                    interpretation is None
                    or self.claim_index is None
                    or self.claim_index >= len(interpretation.claims)
                ):
                    raise ValueError("visual passage lacks its reviewed interpretation")
                claim = interpretation.claims[self.claim_index]
                native, expected = claim.text, claim.regions
            if self.regions != expected:
                raise ValueError("visual passage must preserve its actual image regions")
        if native[self.start : self.end] != self.text or self.end > len(native):
            raise ValueError("passage text does not match retained native evidence")


class CorpusReceipt(CorpusRecord):
    schema_version: Literal["ghimera.corpus-receipt/1"] = Field(alias="schema")
    corpus_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    harvest_sha256: Digest
    config_sha256: Digest
    generation: Count
    added_documents: Count
    added_passages: Count
    total_documents: Count
    total_passages: Count
    encoding_calls: tuple[EncodingCall, ...]
    encoding_recovery: tuple[EncodingRecoveryEvidence, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def reconciled(self) -> "CorpusReceipt":
        if (
            self.added_documents > self.total_documents
            or self.added_passages > self.total_passages
            or any(call.outcome != "success" for call in self.encoding_calls)
            or sum(len(call.input_sha256) for call in self.encoding_calls) != self.added_passages
            or (self.encoding_recovery and len(self.encoding_recovery) != len(self.encoding_calls))
        ):
            raise ValueError("corpus receipt must reconcile its successful passage encodings")
        return self


class CorpusHit(CorpusRecord):
    passage_id: Annotated[int, Field(strict=True, gt=0)]
    cosine: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]
    passage: CorpusPassage


class CorpusQuery(CorpusRecord):
    schema_version: Literal["ghimera.corpus-query/1"] = Field(alias="schema")
    corpus_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    config_sha256: Digest
    generation: Count
    query_sha256: Digest
    encoding_call: EncodingCall
    encoding_recovery: EncodingRecoveryEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    hits: tuple[CorpusHit, ...]
    approximate: Literal[True] = True
    retrieval: RetrievalEvidence | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def reconciled(self) -> "CorpusQuery":
        if (
            self.encoding_call.outcome != "success"
            or len(self.encoding_call.input_sha256) != 1
            or len({hit.passage_id for hit in self.hits}) != len(self.hits)
            or (
                self.encoding_recovery is not None
                and self.encoding_recovery.generation != self.generation
            )
        ):
            raise ValueError("corpus query requires one successful encoding and distinct hits")
        if self.retrieval is not None:
            ranked = {
                row.passage_id: position for position, row in enumerate(self.retrieval.ranking)
            }
            if any(hit.passage_id not in ranked for hit in self.hits) or tuple(
                ranked[hit.passage_id] for hit in self.hits
            ) != tuple(sorted(ranked[hit.passage_id] for hit in self.hits)):
                raise ValueError("hybrid query hits must preserve their admitted ranking")
        return self
