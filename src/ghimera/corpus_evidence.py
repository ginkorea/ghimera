"""Retained originals and ranked passages, never a pretend fresh fetch or verdict."""

import asyncio
from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.corpus import EvidenceCorpus
from ghimera.corpus_bindings import validate_query, validate_reader
from ghimera.corpus_evidence_config import CorpusEvidenceConfig
from ghimera.corpus_types import BoundCorpusDocument, CorpusHit, CorpusQuery, CorpusRecord
from ghimera.embedding_types import EncodingCall
from ghimera.models import Document
from ghimera.reranking_config import OfflineRerankingConfig
from ghimera.research_reranking_types import RerankInvoker

PassageId = Annotated[int, Field(strict=True, gt=0)]
OmissionReason = Literal["minimum_cosine", "document_limit", "original_bytes", "response_bytes"]


class CorpusEvidenceOmission(CorpusRecord):
    passage_id: PassageId
    reason: OmissionReason


class CorpusEvidenceBundle(CorpusRecord):
    schema_version: Literal["ghimera.corpus-evidence/1"] = Field(alias="schema")
    policy: CorpusEvidenceConfig
    query_text: Annotated[str, Field(min_length=1)]
    query: CorpusQuery
    sources: tuple[Document, ...]
    selected_passages: tuple[PassageId, ...]
    omissions: tuple[CorpusEvidenceOmission, ...]
    source_mode: Literal["retained_snapshot"]
    source_age: Literal["unknown"]
    current_intent_verified: Literal[False]

    @property
    def hits(self) -> tuple[CorpusHit, ...]:
        selected = set(self.selected_passages)
        return tuple(hit for hit in self.query.hits if hit.passage_id in selected)

    @model_validator(mode="after")
    def source_bindings(self) -> "CorpusEvidenceBundle":
        policy = self.policy
        validate_query(
            self.query,
            self.query_text,
            corpus_id=policy.corpus_id,
            config_sha256=policy.corpus_config_sha256,
            query_encoder=policy.query_encoder,
        )
        bound = tuple(BoundCorpusDocument(source) for source in self.sources)
        sources = {source.identity: source for source in bound}
        selected = set(self.selected_passages)
        omitted = {item.passage_id for item in self.omissions}
        hits = {hit.passage_id: hit for hit in self.query.hits}
        if (
            not self.query_text.strip()
            or (self.query.retrieval.policy if self.query.retrieval is not None else None)
            != policy.retrieval
            or len(self.query_text) > policy.max_query_chars
            or len(hits) > policy.max_passage_hits
            or len(sources) != len(bound)
            or len(sources) > policy.max_documents
            or len(selected) != len(self.selected_passages)
            or len(omitted) != len(self.omissions)
            or selected & omitted
            or selected | omitted != hits.keys()
            or tuple(hit.passage_id for hit in self.query.hits if hit.passage_id in selected)
            != self.selected_passages
            or {hits[identity].passage.document_id for identity in selected} != sources.keys()
            or any(
                len(source.document.model_dump_json().encode()) > policy.max_original_bytes
                for source in bound
            )
            or any(
                policy.languages and hit.passage.language not in policy.languages
                for hit in self.query.hits
            )
        ):
            raise ValueError("retained context requires complete bounded passage/source selection")
        for hit in self.hits:
            if hit.cosine < policy.minimum_cosine:
                raise ValueError("retained context cannot promote a below-threshold passage")
            hit.passage.validate_binding(sources[hit.passage.document_id])
        self.validate_size()
        return self

    def validate_size(self) -> None:
        if len(self.model_dump_json().encode()) > self.policy.max_response_bytes:
            raise ValueError("retained evidence bundle exceeds its response bound")


class CorpusEvidenceReader:
    """Borrow an owned corpus; callers retain its lifecycle and evidence review."""

    def __init__(self, policy: CorpusEvidenceConfig, corpus: EvidenceCorpus) -> None:
        self.policy = CorpusEvidenceConfig.model_validate(policy.model_dump())
        self._corpus = corpus
        self._check()

    def _check(self) -> None:
        policy = self.policy
        validate_reader(
            self._corpus,
            corpus_id=policy.corpus_id,
            config_sha256=policy.corpus_config_sha256,
            query_encoder=policy.query_encoder,
            max_query_chars=policy.max_query_chars,
            max_passage_hits=policy.max_passage_hits,
            minimum_cosine=policy.minimum_cosine,
        )

    @property
    def reranking_policy(self) -> OfflineRerankingConfig | None:
        return self._corpus.config.reranking

    async def read(
        self,
        text: str,
        *,
        encoding_observer: Callable[[EncodingCall], None] | None = None,
        rerank_invoker: RerankInvoker | None = None,
    ) -> CorpusEvidenceBundle:
        self._check()
        policy = self.policy
        if not text.strip() or len(text) > policy.max_query_chars:
            raise ValueError("retained evidence query exceeds its declared bounds")
        async with asyncio.timeout(policy.timeout_seconds):
            query = await self._corpus.search(
                text,
                top_k=policy.max_passage_hits,
                languages=policy.languages,
                encoding_observer=encoding_observer,
                retrieval=policy.retrieval,
                rerank_invoker=rerank_invoker,
            )
            bundle = CorpusEvidenceBundle(
                schema="ghimera.corpus-evidence/1",
                policy=policy,
                query_text=text,
                query=query,
                sources=(),
                selected_passages=(),
                omissions=tuple(
                    CorpusEvidenceOmission(passage_id=hit.passage_id, reason="response_bytes")
                    for hit in query.hits
                ),
                source_mode="retained_snapshot",
                source_age="unknown",
                current_intent_verified=False,
            )
            bundle.validate_size()  # Even the complete query/omission record must fit.
            sources: dict[str, Document] = {}
            selected: list[int] = []
            omissions: list[CorpusEvidenceOmission] = []
            for hit in query.hits:
                identity = hit.passage.document_id
                reason: OmissionReason | None = None
                original: Document | None = sources.get(identity)
                if hit.cosine < policy.minimum_cosine:
                    reason = "minimum_cosine"
                elif original is None and len(sources) >= policy.max_documents:
                    reason = "document_limit"
                elif original is None:
                    original = (await self._corpus.documents((identity,)))[0]
                    if len(original.model_dump_json().encode()) > policy.max_original_bytes:
                        reason = "original_bytes"
                if reason is None and original is not None:
                    pending_sources = dict(sources) | {identity: original}
                    pending_selected = selected + [hit.passage_id]
                    decided = set(pending_selected) | {item.passage_id for item in omissions}
                    pending = bundle.model_copy(
                        update={
                            "sources": tuple(pending_sources.values()),
                            "selected_passages": tuple(pending_selected),
                            "omissions": tuple(omissions)
                            + tuple(
                                CorpusEvidenceOmission(
                                    passage_id=item.passage_id, reason="response_bytes"
                                )
                                for item in query.hits
                                if item.passage_id not in decided
                            ),
                        }
                    )
                    if len(pending.model_dump_json().encode()) <= policy.max_response_bytes:
                        sources, selected = pending_sources, pending_selected
                    else:
                        reason = "response_bytes"
                if reason is not None:
                    omissions.append(
                        CorpusEvidenceOmission(passage_id=hit.passage_id, reason=reason)
                    )
            result = CorpusEvidenceBundle.model_validate(
                bundle.model_dump()
                | {
                    "sources": tuple(sources.values()),
                    "selected_passages": tuple(selected),
                    "omissions": tuple(omissions),
                }
            )
            result.validate_size()
            return result
