"""Native shelf cosine and keyword/semantic frontier ranking, with shared spend."""

import asyncio
import hashlib
import heapq
import math
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.embedding_types import (
    EmbeddingReferences,
    EncodingBatch,
    EncodingCall,
    IntentReferenceEvidence,
    ReferenceChunk,
    unit_vector,
)
from ghimera.judgment_context import native_scoring_reading, scoring_source_binding
from ghimera.judgment_validation import validate_scoring_readings
from ghimera.ledger import Ledger
from ghimera.models import Extracted, Goal, LedgerRow, LinkCandidate
from ghimera.ports import EvidenceEncoder
from ghimera.refusals import EncodingCancelled, EncodingFailure, GhimeraRefused, RefusalCode
from ghimera.scoring import Scorer
from ghimera.scoring_config import ScoringConfig
from ghimera.scoring_types import LinkSimilarity, SimilarityEvidence, WindowSimilarity


def keyword_score(goal: Goal, link: LinkCandidate) -> float:
    haystack = (link.url + " " + link.anchor).casefold()
    return float(any(term in haystack for term in goal.text.casefold().split()))


def selected_windows(text: str, goal: Goal, policy: ScoringConfig) -> tuple[tuple[int, int], ...]:
    """Bounded selection: best lexical windows first, native offsets never rewritten."""
    terms = tuple(dict.fromkeys(goal.text.casefold().split()))
    step = policy.window_chars - policy.overlap_chars

    def rank(start: int) -> tuple[int, int]:
        window = text[start : start + policy.window_chars].casefold()
        return sum(term in window for term in terms), -start

    # nlargest retains max_windows candidates, not an unbounded list of extracted chunks.
    starts = heapq.nlargest(policy.max_windows, range(0, len(text), step), key=rank)
    return tuple((start, min(start + policy.window_chars, len(text))) for start in sorted(starts))


def covered_chars(spans: tuple[tuple[int, int], ...]) -> int:
    total, previous_end = 0, 0
    for start, end in spans:
        total += max(0, end - max(start, previous_end))
        previous_end = max(previous_end, end)
    return total


@dataclass
class _IntentSession:
    """Weakly budget-keyed state; no references can cross runs or original intents."""

    goal_sha256: str
    ledger: Ledger
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    references: EmbeddingReferences | None = None


class EmbeddingScorer(Scorer):
    name = "shelf_embedding"
    cost = 1

    def __init__(
        self,
        policy: ScoringConfig,
        encoder: EvidenceEncoder,
        references: EmbeddingReferences | None = None,
        *,
        query_encoder: EvidenceEncoder | None = None,
    ) -> None:
        if encoder.model.location != "self_hosted":
            raise ValueError("semantic scoring requires the explicitly self-hosted encoder")
        service = policy.encoder
        if encoder.config != service or (encoder.model.model_id, encoder.model.revision) != (
            service.model_id,
            service.revision,
        ):
            raise ValueError("encoder must match its recorded policy and identity")
        if (policy.reference_source == "pinned") != (references is not None):
            raise ValueError(
                "pinned scoring requires shelf vectors; intent scoring prepares its own"
            )
        if (policy.query_encoder is not None) != (query_encoder is not None):
            raise ValueError("query encoder injection must match its explicit scoring policy")
        if query_encoder is not None and (
            query_encoder.model.location != "self_hosted"
            or query_encoder.config != policy.intent_encoder
            or (query_encoder.model.model_id, query_encoder.model.revision)
            != (policy.intent_encoder.model_id, policy.intent_encoder.revision)
        ):
            raise ValueError("query encoder must match its recorded policy and identity")
        if references is not None and (
            references.sha256 != policy.references_sha256
            or len(references.chunks) > policy.max_reference_chunks
            or (
                references.model_id,
                references.revision,
                references.dimensions,
                references.text_prefix,
            )
            != (service.model_id, service.revision, service.dimensions, service.text_prefix)
        ):
            raise ValueError("shelf vectors must match the pinned encoder, dimensions and prefix")
        self._policy, self._encoder, self._references = policy, encoder, references
        self._query_encoder = query_encoder or encoder
        self._sessions: WeakKeyDictionary[RunBudget, _IntentSession] = WeakKeyDictionary()

    def validate_config(self, config: GhimeraConfig) -> None:
        if config.scoring != self._policy:
            raise ValueError("scorer must share the run's exact effective policy")

    def _cosine(
        self, vector: tuple[float, ...], references: tuple[tuple[float, ...], ...]
    ) -> tuple[float, int]:
        current = unit_vector(vector)
        if len(current) != self._policy.encoder.dimensions:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        similarities = tuple(
            max(
                -1.0,
                min(
                    1.0,
                    math.fsum(left * right for left, right in zip(current, reference, strict=True)),
                ),
            )
            for reference in references
        )
        index = max(range(len(similarities)), key=similarities.__getitem__)
        return similarities[index], index

    async def _references_for(
        self, goal: Goal, budget: RunBudget, ledger: Ledger
    ) -> EmbeddingReferences:
        if self._references is not None:
            return self._references
        goal_hash = hashlib.sha256(goal.text.encode()).hexdigest()
        session = self._sessions.get(budget)
        if session is None:
            session = _IntentSession(goal_hash, ledger)
            self._sessions[budget] = session
        if session.goal_sha256 != goal_hash or session.ledger is not ledger:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        async with session.lock:
            if session.references is not None:
                return session.references
            # A resumed budget has a new object identity, but its original intent
            # vectors remain acknowledged native observations, not another call.
            retained = tuple(
                row.intent_reference
                for row in ledger.snapshot()
                if row.intent_reference is not None
            )
            if retained:
                if len(retained) != 1 or retained[0].goal_sha256 != goal_hash:
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                prepared = IntentReferenceEvidence.model_validate(retained[0].model_dump())
                observed = ledger.snapshot()
                if prepared.encoding_sequence >= len(observed):
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                retained_call = observed[prepared.encoding_sequence].encoding_call
                if retained_call is None:
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                prepared.validate_binding(goal.text, self._policy.intent_encoder, retained_call)
                session.references = prepared.references
                return session.references
            vectors = await self._encode(
                (goal.text,), budget, ledger, None, encoder=self._query_encoder
            )
            service = self._policy.intent_encoder
            references = EmbeddingReferences(
                schema="chimera.embedding-references/1",
                model_id=service.model_id,
                revision=service.revision,
                dimensions=service.dimensions,
                text_prefix=service.text_prefix,
                chunks=(
                    ReferenceChunk(
                        source_id="intent:" + goal_hash,
                        text_sha256=goal_hash,
                        vector=vectors[0],
                    ),
                ),
            )
            # One intent is one encoder input/batch. Capture the actual completed
            # call, not a sequence reserved before other tasks could append rows.
            encoded = ledger.snapshot()[-1]
            if encoded.encoding_call is None:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            evidence = IntentReferenceEvidence(
                schema="chimera.intent-reference/1",
                goal_sha256=goal_hash,
                encoding_sequence=encoded.sequence,
                references=references,
            )
            evidence.validate_binding(goal.text, service, encoded.encoding_call)
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="intent_reference",
                    model=self._query_encoder.model,
                    intent_reference=evidence,
                    reason="original_intent_reference_not_probability",
                )
            )
            # Cache only after durable ledger acknowledgement, never on a failed
            # call or a journal-write failure. The run owns both vectors and spend.
            session.references = references
            return references

    async def _encode(
        self,
        texts: tuple[str, ...],
        budget: RunBudget,
        ledger: Ledger,
        url: str | None,
        *,
        encoder: EvidenceEncoder | None = None,
    ) -> tuple[tuple[float, ...], ...]:
        encoder = encoder or self._encoder
        service = encoder.config
        output: list[tuple[float, ...]] = []
        pending: list[str] = []
        size = 0

        async def flush() -> None:
            nonlocal size
            if not pending:
                return
            batch_texts = tuple(pending)
            expected_hashes = tuple(
                hashlib.sha256((service.text_prefix + text).encode()).hexdigest()
                for text in batch_texts
            )
            budget.reserve_encoding(size)
            started = asyncio.get_running_loop().time()
            call: EncodingCall | None = None
            refusal = None
            try:
                batch = await encoder.encode_batch(batch_texts)
                call = batch.call
                # Revalidate injected objects: model_copy/update can bypass Pydantic guards.
                batch = EncodingBatch.model_validate(batch.model_dump())
                if (
                    call.service != service
                    or call.input_sha256 != expected_hashes
                    or call.input_chars != size
                ):
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                output.extend(batch.vectors)
            except EncodingFailure as exc:
                call, refusal = exc.call, exc.code
                raise
            except EncodingCancelled as exc:
                call, refusal = exc.call, RefusalCode.BUDGET_EXHAUSTED
                raise
            except ValidationError:
                refusal = RefusalCode.ADAPTER_CONTRACT
                raise GhimeraRefused(refusal) from None
            except (GhimeraRefused, asyncio.CancelledError):
                refusal = RefusalCode.ADAPTER_CONTRACT
                raise
            finally:
                if (
                    call is None
                    or call.service != service
                    or call.input_sha256 != expected_hashes
                    or call.input_chars != size
                ):
                    # The injected adapter failed before yielding trustworthy evidence.
                    # Record unknown response/usage; never copy its fabricated telemetry.
                    call = EncodingCall(
                        schema="chimera.encoding-call/1",
                        service=service,
                        request_sha256=hashlib.sha256(b"").hexdigest(),
                        response_sha256=hashlib.sha256(b"").hexdigest(),
                        response_bytes=0,
                        input_sha256=expected_hashes,
                        input_chars=size,
                        status=None,
                        latency_seconds=max(0.0, asyncio.get_running_loop().time() - started),
                        usage=None,
                        outcome="refused",
                        telemetry="unavailable",
                    )
                    refusal = RefusalCode.ADAPTER_CONTRACT
                elif refusal is not None and call.outcome == "success":
                    call = call.model_copy(update={"outcome": "refused"})
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="encoding",
                        model=encoder.model,
                        url=url,
                        reason=call.outcome,
                        refusal=refusal,
                        encoding_call=call,
                    )
                )
            pending.clear()
            size = 0

        for text in texts:
            chars = len(text) + len(service.text_prefix)
            if chars > service.max_text_chars or chars > service.max_input_chars:
                raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            if len(pending) >= service.max_batch_texts or size + chars > service.max_input_chars:
                await flush()
            pending.append(text)
            size += chars
        await flush()
        return tuple(output)

    async def rank(
        self, goal: Goal, document: Extracted, budget: RunBudget, ledger: Ledger
    ) -> tuple[LinkCandidate, ...]:
        self.validate_config(budget.config)
        policy = self._policy
        config = budget.config
        native_reading = (
            native_scoring_reading(ledger.snapshot(), document)
            if config.document_judgment is not None
            or (config.semantics is not None and config.semantics.window_selection is not None)
            else None
        )
        source_binding = None
        if native_reading is not None:
            reading_row = LedgerRow(
                sequence=ledger.next_sequence,
                event="scoring_source",
                url=native_reading.source_url,
                scoring_reading=native_reading,
                reason="private_native_reading_before_encoding_not_accepted_content",
            )
            validate_scoring_readings(config, ledger.snapshot() + (reading_row,))
            ledger.append(reading_row)
            source_binding = scoring_source_binding(ledger.snapshot(), document)
        spans = selected_windows(document.text, goal, policy)
        if not spans:
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
        references = await self._references_for(goal, budget, ledger)
        reference_vectors = tuple(unit_vector(chunk.vector) for chunk in references.chunks)
        links = tuple(
            sorted(document.links, key=lambda link: keyword_score(goal, link), reverse=True)[
                : policy.max_links
            ]
        )
        windows = tuple(document.text[start:end] for start, end in spans)
        # Link text is observed anchor+URL only, not invented content behind an unfetched URL.
        link_texts = tuple(
            link.url + "\n" + link.anchor[: policy.max_anchor_chars] for link in links
        )
        vectors = await self._encode(windows + link_texts, budget, ledger, document.canonical_url)
        observations = []
        for (start, end), text, vector in zip(spans, windows, vectors[: len(windows)], strict=True):
            cosine, index = self._cosine(vector, reference_vectors)
            reference = references.chunks[index]
            observations.append(
                WindowSimilarity(
                    start=start,
                    end=end,
                    text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    cosine=cosine,
                    reference_source_id=reference.source_id,
                    reference_text_sha256=reference.text_sha256,
                )
            )
        link_observations, ranked = [], []
        for link, text, vector in zip(links, link_texts, vectors[len(windows) :], strict=True):
            cosine, _ = self._cosine(vector, reference_vectors)
            keyword = keyword_score(goal, link)
            # Negative similarity is zero frontier weight, not a 50% probability.
            score = policy.keyword_weight * keyword + (1.0 - policy.keyword_weight) * max(
                0.0, cosine
            )
            link_observations.append(
                LinkSimilarity(
                    url=link.url,
                    input_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    cosine=cosine,
                    keyword_score=keyword,
                    score=score,
                    omitted_anchor_chars=max(0, len(link.anchor) - policy.max_anchor_chars),
                )
            )
            ranked.append(LinkCandidate(url=link.url, anchor=link.anchor, score=score))
        selected = covered_chars(spans)
        evidence = SimilarityEvidence(
            schema="chimera.similarity/1",
            text_sha256=hashlib.sha256(document.text.encode()).hexdigest(),
            references_sha256=references.sha256,
            goal_sha256=hashlib.sha256(goal.text.encode()).hexdigest(),
            total_chars=len(document.text),
            selected_chars=selected,
            omitted_chars=len(document.text) - selected,
            windows=tuple(observations),
            links=tuple(link_observations),
            omitted_links=len(document.links) - len(links),
            document_cosine=max(item.cosine for item in observations),
        )
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event="scoring",
                url=document.canonical_url,
                model=self._encoder.model,
                similarity=evidence,
                scoring_source=source_binding,
                reason=(
                    "intent_cosine_not_probability"
                    if policy.reference_source == "intent"
                    else "shelf_cosine_not_probability"
                ),
            )
        )
        return tuple(ranked)
