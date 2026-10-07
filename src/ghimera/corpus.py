"""Durable native evidence corpus, independent of collector/service lifecycles."""

import asyncio
import hashlib
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from ghimera.corpus_config import CorpusConfig
from ghimera.corpus_index import NativePassageIndex
from ghimera.corpus_storage import CorpusStorage, digest
from ghimera.corpus_types import (
    BoundCorpusDocument,
    CorpusHit,
    CorpusPassage,
    CorpusQuery,
    CorpusReceipt,
)
from ghimera.embedding_types import EncodingBatch, EncodingCall
from ghimera.models import Document, Harvest
from ghimera.ports import EvidenceEncoder
from ghimera.refusals import EncodingCancelled, EncodingFailure, GhimeraRefused, RefusalCode
from ghimera.visual_types import ImageRegion

Result = TypeVar("Result")


async def off_loop(function: Callable[[], Result]) -> Result:
    """Keep native/SQLite reconstruction off-loop and drain it even on cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()  # A native failure is not hidden behind caller cancellation.
        raise


def document_passages(document: Document, config: CorpusConfig) -> tuple[CorpusPassage, ...]:
    source = BoundCorpusDocument(document)
    document, identity = source.document, source.identity
    output: list[CorpusPassage] = []

    def add(
        text: str,
        kind: str,
        image: str | None = None,
        claim: int | None = None,
        regions: tuple[ImageRegion, ...] = (),
    ) -> None:
        for start in range(0, len(text), config.chunk_chars - config.overlap_chars):
            end = min(len(text), start + config.chunk_chars)
            if not text[start:end].strip():
                continue
            passage = CorpusPassage.model_validate(
                {
                    "document_id": identity,
                    "source_url": document.url,
                    "source_sha256": document.sha256,
                    "kind": kind,
                    "image_sha256": image,
                    "claim_index": claim,
                    "start": start,
                    "end": end,
                    "text": text[start:end],
                    "language": document.verdict.language,
                    "regions": regions,
                }
            )
            passage.validate_binding(source)
            output.append(passage)
            if len(output) > config.max_chunks:
                raise ValueError("document passages exceed configured corpus capacity")

    add(document.extracted.text, "native")
    for image in document.images:
        add(
            image.ocr.text,
            "image_ocr",
            image.sha256,
            regions=tuple(s.region for s in image.ocr.spans),
        )
        if image.interpretation is not None:
            for position, claim in enumerate(image.interpretation.claims):
                add(claim.text, "visual_claim", image.sha256, position, claim.regions)
    return tuple(output)


class EvidenceCorpus:
    """Explicit model bindings; atomic append, independently readable generations."""

    def __init__(
        self,
        config: CorpusConfig,
        *,
        encoder: EvidenceEncoder,
        query_encoder: EvidenceEncoder,
        create: bool = False,
    ) -> None:
        self.config = CorpusConfig.model_validate(config.model_dump())
        for port, service in ((encoder, config.encoder), (query_encoder, config.query_encoder)):
            if port.config != service or (
                port.model.model_id,
                port.model.revision,
                port.model.location,
            ) != (service.model_id, service.revision, "self_hosted"):
                raise ValueError("corpus encoder must match its pinned self-hosted binding")
        # Validate optional backend admission before creating files or spending.
        NativePassageIndex(config, ())
        self._storage = CorpusStorage(config, create=create)
        self._encoder, self._query_encoder = encoder, query_encoder
        self._operations = 0
        self._closed = False
        self._index: tuple[int, tuple[int, ...], NativePassageIndex] | None = None
        self._building = asyncio.Lock()

    @contextmanager
    def _operation(self) -> Iterator[None]:
        if self._closed:
            raise ValueError("corpus is closed")
        self._storage.check()
        self._operations += 1
        try:
            yield
        finally:
            self._operations -= 1

    def close(self) -> None:
        if self._operations:
            raise ValueError("corpus cannot close while it owns active operations")
        if not self._closed:
            self._storage.close()
            self._closed = True
            self._index = None

    def check_ready(self) -> None:
        """Validate open, owned storage before a caller starts new source work."""
        with self._operation():
            pass

    def document(self, document_id: str) -> Document:
        with self._operation():
            return self._storage.document(document_id)

    @property
    def identity(self) -> str:
        with self._operation():
            return self._storage.identity

    async def documents(self, document_ids: tuple[str, ...]) -> tuple[Document, ...]:
        """Read bounded originals off-loop on a worker-owned SQLite connection."""
        with self._operation():
            if len(document_ids) > self.config.max_top_k or len(set(document_ids)) != len(
                document_ids
            ):
                raise ValueError("original lookup requires bounded distinct document identities")
            identity = self._storage.identity

            def read() -> tuple[Document, ...]:
                storage = CorpusStorage(self.config, create=False)
                try:
                    if storage.identity != identity:
                        raise ValueError("original lookup cannot switch corpus identity")
                    return tuple(storage.document(value) for value in document_ids)
                finally:
                    storage.close()

            return await off_loop(read)

    async def _encode(
        self, texts: tuple[str, ...], encoder: EvidenceEncoder, operation: str, purpose: str
    ) -> tuple[EncodingBatch, int]:
        service = encoder.config
        expected = tuple(digest((service.text_prefix + text).encode()) for text in texts)
        size = sum(len(service.text_prefix) + len(text) for text in texts)
        started = asyncio.get_running_loop().time()
        call: EncodingCall | None = None
        successful = False
        try:
            async with asyncio.timeout(service.timeout_seconds):
                batch = await encoder.encode_batch(texts)
            call = batch.call
            batch = EncodingBatch.model_validate(batch.model_dump())
            if call.service != service or call.input_sha256 != expected or call.input_chars != size:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            successful = True
        except (EncodingFailure, EncodingCancelled) as exc:
            call = exc.call
            raise
        finally:
            if (
                call is None
                or call.service != service
                or call.input_sha256 != expected
                or call.input_chars != size
            ):
                call = EncodingCall(
                    schema="chimera.encoding-call/1",
                    service=service,
                    request_sha256=digest(b""),
                    response_sha256=digest(b""),
                    response_bytes=0,
                    input_sha256=expected,
                    input_chars=size,
                    status=None,
                    latency_seconds=max(0.0, asyncio.get_running_loop().time() - started),
                    usage=None,
                    outcome="refused",
                    telemetry="unavailable",
                )
            elif not successful and call.outcome == "success":
                call = call.model_copy(update={"outcome": "refused"})
            call = EncodingCall.model_validate(call.model_dump())
            identity = self._storage.audit(operation, purpose, call)
        return batch, identity

    def _batches(
        self, passages: tuple[CorpusPassage, ...]
    ) -> tuple[tuple[CorpusPassage, ...], ...]:
        service = self.config.encoder
        batches: list[tuple[CorpusPassage, ...]] = []
        pending: list[CorpusPassage] = []
        size = 0
        for passage in passages:
            chars = len(service.text_prefix) + len(passage.text)
            if chars > service.max_input_chars:
                raise ValueError("native passage exceeds the encoder batch budget")
            if pending and (
                len(pending) == service.max_batch_texts or size + chars > service.max_input_chars
            ):
                batches.append(tuple(pending))
                pending, size = [], 0
            pending.append(passage)
            size += chars
        if pending:
            batches.append(tuple(pending))
        if (
            len(batches) > self.config.max_encoding_calls_per_append
            or sum(len(p.text) + len(service.text_prefix) for p in passages)
            > self.config.max_encoding_chars_per_append
        ):
            raise ValueError("append exceeds its explicitly separate encoding allowance")
        return tuple(batches)

    async def append(self, harvest: Harvest) -> CorpusReceipt:
        with self._operation(), self._storage.writer():
            harvest = Harvest.model_validate(harvest.model_dump())
            count_docs, count_chunks, source_bytes, vector_bytes = self._storage.counts()
            documents: dict[str, bytes] = {}
            passages: list[CorpusPassage] = []
            added_bytes = 0
            for document in harvest.documents:
                if document.verdict.decision != "accept":
                    raise ValueError("corpus accepts only independently accepted documents")
                data = document.model_dump_json().encode()
                identity = digest(data)
                if identity in documents or self._storage.has_document(identity):
                    if identity not in documents:
                        self._storage.document(identity)
                    continue
                if len(data) > self.config.max_document_bytes:
                    raise ValueError("retained original exceeds its corpus document bound")
                added_bytes += len(data)
                if (
                    count_docs + len(documents) + 1 > self.config.max_documents
                    or source_bytes + added_bytes > self.config.max_stored_document_bytes
                ):
                    raise ValueError("corpus capacity exhausted before encoding")
                documents[identity] = data
                passages.extend(document_passages(document, self.config))
                if count_chunks + len(passages) > self.config.max_chunks:
                    raise ValueError("append cannot silently truncate native passages")
            if (
                vector_bytes + len(passages) * self.config.encoder.dimensions * 8
                > self.config.max_vector_bytes
            ):
                raise ValueError("corpus capacity exhausted before encoding")
            batches = self._batches(tuple(passages))
            operation = uuid.uuid4().hex
            harvest_sha = digest(harvest.model_dump_json().encode())
            self._storage.start(
                operation,
                kind="append",
                input_sha=harvest_sha,
                calls=len(batches),
            )
            vectors: list[tuple[CorpusPassage, tuple[float, ...], int, int]] = []
            calls: list[EncodingCall] = []
            try:
                async with asyncio.timeout(self.config.operation_timeout_seconds):
                    for pending in batches:
                        batch, call_id = await self._encode(
                            tuple(p.text for p in pending), self._encoder, operation, "passage"
                        )
                        calls.append(batch.call)
                        for position, (passage, vector) in enumerate(
                            zip(pending, batch.vectors, strict=True)
                        ):
                            vectors.append((passage, vector, call_id, position))
                    generation = self._storage.commit(
                        operation, tuple(documents.items()), tuple(vectors)
                    )
            except BaseException as exc:
                self._storage.terminal(
                    operation, "cancelled" if isinstance(exc, asyncio.CancelledError) else "refused"
                )
                raise
            self._index = None
            return CorpusReceipt(
                schema="ghimera.corpus-receipt/1",
                corpus_id=self._storage.identity,
                harvest_sha256=harvest_sha,
                config_sha256=self.config.identity,
                generation=generation,
                added_documents=len(documents),
                added_passages=len(passages),
                total_documents=count_docs + len(documents),
                total_passages=count_chunks + len(passages),
                encoding_calls=tuple(calls),
            )

    async def _snapshot(self) -> tuple[int, tuple[int, ...], NativePassageIndex]:
        async with self._building:
            generation = self._storage.generation()
            if self._index is not None and self._index[0] == generation:
                return self._index
            self._index = await off_loop(self._rebuild)
            return self._index

    def _rebuild(self) -> tuple[int, tuple[int, ...], NativePassageIndex]:
        # This connection belongs to the worker, never crosses SQLite threads.
        storage = CorpusStorage(self.config, create=False)
        try:
            generation, ids, vectors = storage.vectors()
            return generation, ids, NativePassageIndex(self.config, vectors)
        finally:
            storage.close()

    async def search(
        self, text: str, *, top_k: int, languages: tuple[str, ...] = ()
    ) -> CorpusQuery:
        with self._operation():
            if (
                not text.strip()
                or len(text) > self.config.max_query_chars
                or type(top_k) is not int
                or not 0 < top_k <= self.config.max_top_k
                or any(not language.strip() for language in languages)
            ):
                raise ValueError("query, language filter and top-k require explicit bounds")
            operation = uuid.uuid4().hex
            query_hash = hashlib.sha256(text.encode()).hexdigest()
            self._storage.start(operation, kind="query", input_sha=query_hash, calls=1)
            try:
                async with asyncio.timeout(self.config.operation_timeout_seconds):
                    generation, ids, index = await self._snapshot()
                    batch, _ = await self._encode((text,), self._query_encoder, operation, "query")
                    hits: list[CorpusHit] = []
                    neighbors = await off_loop(
                        lambda: index.search(
                            batch.vectors[0], candidates=self.config.search_candidates
                        )
                    )
                    for position, cosine in neighbors:
                        if cosine < self.config.minimum_cosine:
                            continue
                        passage = self._storage.passage(ids[position])
                        if languages and passage.language not in languages:
                            continue
                        hits.append(
                            CorpusHit(passage_id=ids[position], cosine=cosine, passage=passage)
                        )
                        if len(hits) == top_k:
                            break
                    result = CorpusQuery(
                        schema="ghimera.corpus-query/1",
                        corpus_id=self._storage.identity,
                        config_sha256=self.config.identity,
                        generation=generation,
                        query_sha256=query_hash,
                        encoding_call=batch.call,
                        hits=tuple(hits),
                    )
                self._storage.terminal(operation, "committed")
                return result
            except BaseException as exc:
                self._storage.terminal(
                    operation, "cancelled" if isinstance(exc, asyncio.CancelledError) else "refused"
                )
                raise
