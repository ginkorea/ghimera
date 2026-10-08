"""Bounded native-text context; omitted material is observable, never fabricated."""

import hashlib
import re
from typing import Annotated

from pydantic import Field, model_validator

from ghimera.corpus_types import BoundCorpusDocument
from ghimera.model_citations import citation_id
from ghimera.model_config import EvidenceContextConfig
from ghimera.models import Document, Record
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research_types import Citation
from ghimera.visual_evidence import image_reading


class ContextWindow(Record):
    citation: Citation
    citation_id: Annotated[str, Field(pattern=r"^cite:[0-9a-f]{64}$")]

    @classmethod
    def from_citation(cls, citation: Citation) -> "ContextWindow":
        return cls(citation=citation, citation_id=citation_id(citation))

    @model_validator(mode="after")
    def bound_reference(self) -> "ContextWindow":
        if self.citation_id != citation_id(self.citation):
            raise ValueError("citation identifier must bind its exact native template")
        return self


class ContextDocument(Record):
    document_id: str
    url: str
    title: str
    language: str
    text_chars: int
    selected_chars: int
    omitted_chars: int
    visual_chars: int = Field(default=0, exclude_if=lambda v: v == 0)
    selected_visual_chars: int = Field(default=0, exclude_if=lambda v: v == 0)
    omitted_visual_chars: int = Field(default=0, exclude_if=lambda v: v == 0)


class ContextOmission(Record):
    document_id: str
    reason: str


class EvidenceContext(Record):
    windows: tuple[ContextWindow, ...]
    documents: tuple[ContextDocument, ...]
    omitted_documents: tuple[ContextOmission, ...]

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def native_citation(document: Document, start: int, end: int) -> Citation:
    """Retain the actual reading basis; generated PDF text is never labelled native."""
    return Citation.from_document(document, start, end)


class ContextSelector:
    def __init__(self, policy: EvidenceContextConfig) -> None:
        self._policy = policy

    def build(
        self,
        intent: str,
        documents: tuple[Document, ...],
        *,
        required: tuple[Citation, ...] = (),
    ) -> EvidenceContext:
        originals = {BoundCorpusDocument(doc).identity: doc for doc in documents}
        windows: dict[tuple[str, int, int], ContextWindow] = {}
        chosen: dict[str, Document] = {}
        used = 0
        for citation in required:
            doc = next((doc for doc in originals.values() if citation.matches(doc)), None)
            if doc is None:
                raise GhimeraRefused(RefusalCode.UNSUPPORTED_ANSWER)
            identity = BoundCorpusDocument(doc).identity
            # Derived readings have their own offset space; native and each image never collide.
            reading_id = (
                ":" + citation.visual_anchor.reading_sha256 if citation.visual_anchor else ""
            )
            key = (identity + reading_id, citation.start, citation.end)
            if key in windows:
                continue
            if used + len(citation.quote) > self._policy.max_chars:
                raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            windows[key] = ContextWindow.from_citation(citation)
            chosen[identity] = doc
            used += len(citation.quote)
        if len(chosen) > self._policy.max_documents:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        terms = tuple(dict.fromkeys(re.findall(r"\w+", intent)))
        # Required answer citations always precede discretionary context.
        ordered = tuple(chosen.values()) + tuple(
            doc for identity, doc in originals.items() if identity not in chosen
        )
        for doc in ordered:
            identity = BoundCorpusDocument(doc).identity
            if identity not in chosen and len(chosen) >= self._policy.max_documents:
                continue
            text = doc.extracted.text
            # Rank visual observations alongside native windows under the same explicit limits.
            visual_candidates = [
                Citation.from_image(doc, image_index, span_index)
                for image_index, image in enumerate(doc.images)
                if (reading := image_reading(image)) is not None
                for span_index in range(len(reading.spans))
            ]
            visual_candidates.sort(
                key=lambda citation: (
                    -sum(
                        bool(re.search(re.escape(term), citation.quote, re.IGNORECASE))
                        for term in terms
                    ),
                    citation.basis != "reviewed_visual_claim",
                    citation_id(citation),
                )
            )
            for citation in visual_candidates:
                if citation.visual_anchor is None:
                    raise ValueError("visual context lost its region anchor")
                key = (
                    identity + ":" + citation.visual_anchor.reading_sha256,
                    citation.start,
                    citation.end,
                )
                if key in windows:
                    continue
                count = sum(
                    key[0] == identity or key[0].startswith(identity + ":") for key in windows
                )
                if (
                    count >= self._policy.max_windows_per_document
                    or used + len(citation.quote) > self._policy.max_chars
                ):
                    break
                windows[key] = ContextWindow.from_citation(citation)
                chosen[identity] = doc
                used += len(citation.quote)
            starts = {0}
            for term in terms:
                for hit in re.finditer(re.escape(term), text, re.IGNORECASE):
                    stride = self._policy.window_chars - self._policy.overlap_chars
                    starts.add((hit.start() // stride) * stride)
            ranked = sorted(
                starts,
                key=lambda start: (
                    -sum(
                        bool(
                            re.search(
                                re.escape(term),
                                text[start : start + self._policy.window_chars],
                                re.IGNORECASE,
                            )
                        )
                        for term in terms
                    ),
                    start,
                ),
            )
            for start in ranked:
                count = sum(
                    key[0] == identity or key[0].startswith(identity + ":") for key in windows
                )
                if count >= self._policy.max_windows_per_document:
                    break
                room = self._policy.max_chars - used
                if room <= 0:
                    break
                end = min(len(text), start + self._policy.window_chars, start + room)
                if end <= start or any(
                    key[0] == identity and start < key[2] and end > key[1] for key in windows
                ):
                    continue
                windows[(identity, start, end)] = ContextWindow.from_citation(
                    native_citation(doc, start, end)
                )
                chosen[identity] = doc
                used += end - start
        metadata: list[ContextDocument] = []
        for identity, doc in chosen.items():
            spans = sorted(
                (start, end)
                for document_identity, start, end in windows
                if document_identity == identity
            )
            covered, cursor = 0, 0
            for start, end in spans:
                covered += max(0, end - max(start, cursor))
                cursor = max(cursor, end)
            metadata.append(
                ContextDocument(
                    document_id="doc:" + doc.sha256,
                    url=doc.url,
                    title=doc.extracted.title,
                    language=doc.extracted.language,
                    text_chars=len(doc.extracted.text),
                    selected_chars=covered,
                    omitted_chars=len(doc.extracted.text) - covered,
                    visual_chars=sum(
                        len(reading.text)
                        for image in doc.images
                        if (reading := image_reading(image)) is not None
                    ),
                    selected_visual_chars=sum(
                        len(window.citation.quote)
                        for key, window in windows.items()
                        if key[0].startswith(identity + ":")
                    ),
                    omitted_visual_chars=sum(
                        len(span.quote)
                        for image in doc.images
                        if (reading := image_reading(image)) is not None
                        for span in reading.spans
                    )
                    - sum(
                        len(window.citation.quote)
                        for key, window in windows.items()
                        if key[0].startswith(identity + ":")
                    ),
                )
            )
        return EvidenceContext(
            windows=tuple(windows.values()),
            documents=tuple(metadata),
            omitted_documents=tuple(
                ContextOmission(document_id="doc:" + doc.sha256, reason="context_limit")
                for identity, doc in originals.items()
                if identity not in chosen
            ),
        )
