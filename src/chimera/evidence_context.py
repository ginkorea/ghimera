"""Bounded native-text context; omitted material is observable, never fabricated."""

import hashlib
import re

from chimera.model_config import EvidenceContextConfig
from chimera.models import Document, Record
from chimera.refusals import ChimeraRefused, RefusalCode
from chimera.research_types import Citation


class ContextWindow(Record):
    citation: Citation


class ContextDocument(Record):
    document_id: str
    url: str
    title: str
    language: str
    text_chars: int
    selected_chars: int
    omitted_chars: int


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
    text = document.extracted.text
    return Citation(
        document_id="doc:" + document.sha256,
        source_url=document.url,
        document_sha256=document.sha256,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        start=start,
        end=end,
        quote=text[start:end],
    )


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
        by_digest = {(doc.sha256, doc.url): doc for doc in documents}
        windows: dict[tuple[str, str, int, int], ContextWindow] = {}
        chosen: dict[tuple[str, str], Document] = {}
        used = 0
        for citation in required:
            doc = by_digest.get((citation.document_sha256, citation.source_url))
            if doc is None or not citation.matches(doc):
                raise ChimeraRefused(RefusalCode.UNSUPPORTED_ANSWER)
            key = (doc.sha256, doc.url, citation.start, citation.end)
            if key in windows:
                continue
            if used + len(citation.quote) > self._policy.max_chars:
                raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            windows[key] = ContextWindow(citation=citation)
            chosen[(doc.sha256, doc.url)] = doc
            used += len(citation.quote)
        if len(chosen) > self._policy.max_documents:
            raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        terms = tuple(dict.fromkeys(re.findall(r"\w+", intent)))
        # Required answer citations always precede discretionary context.
        ordered = tuple(chosen.values()) + tuple(
            doc for doc in documents if (doc.sha256, doc.url) not in chosen
        )
        for doc in ordered:
            if (doc.sha256, doc.url) not in chosen and len(chosen) >= self._policy.max_documents:
                continue
            text = doc.extracted.text
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
                count = sum(key[:2] == (doc.sha256, doc.url) for key in windows)
                if count >= self._policy.max_windows_per_document:
                    break
                room = self._policy.max_chars - used
                if room <= 0:
                    break
                end = min(len(text), start + self._policy.window_chars, start + room)
                if end <= start or any(
                    key[:2] == (doc.sha256, doc.url) and start < key[3] and end > key[2]
                    for key in windows
                ):
                    continue
                windows[(doc.sha256, doc.url, start, end)] = ContextWindow(
                    citation=native_citation(doc, start, end)
                )
                chosen[(doc.sha256, doc.url)] = doc
                used += end - start
        metadata: list[ContextDocument] = []
        for doc in chosen.values():
            spans = sorted(
                (start, end)
                for digest, url, start, end in windows
                if (digest, url) == (doc.sha256, doc.url)
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
                )
            )
        return EvidenceContext(
            windows=tuple(windows.values()),
            documents=tuple(metadata),
            omitted_documents=tuple(
                ContextOmission(document_id="doc:" + doc.sha256, reason="context_limit")
                for doc in documents
                if (doc.sha256, doc.url) not in chosen
            ),
        )
