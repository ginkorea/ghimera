"""Native shelf cosine and keyword/semantic frontier ranking, with shared spend."""

import asyncio
import hashlib
import heapq
import math

from pydantic import ValidationError

from chimera.budget import RunBudget
from chimera.config import ChimeraConfig
from chimera.embedding_types import EmbeddingReferences, EncodingBatch, EncodingCall, unit_vector
from chimera.ledger import Ledger
from chimera.models import Extracted, Goal, LedgerRow, LinkCandidate
from chimera.ports import EvidenceEncoder
from chimera.refusals import ChimeraRefused, EncodingCancelled, EncodingFailure, RefusalCode
from chimera.scoring import Scorer
from chimera.scoring_config import ScoringConfig
from chimera.scoring_types import LinkSimilarity, SimilarityEvidence, WindowSimilarity


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


class EmbeddingScorer(Scorer):
    name = "shelf_embedding"
    cost = 1

    def __init__(
        self, policy: ScoringConfig, encoder: EvidenceEncoder, references: EmbeddingReferences
    ) -> None:
        if encoder.model.location != "self_hosted":
            raise ValueError("semantic scoring requires the explicitly self-hosted encoder")
        service = policy.encoder
        if encoder.config != service or (encoder.model.model_id, encoder.model.revision) != (
            service.model_id,
            service.revision,
        ):
            raise ValueError("encoder must match its recorded policy and identity")
        if (
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
        self._vectors = tuple(unit_vector(chunk.vector) for chunk in references.chunks)

    def validate_config(self, config: ChimeraConfig) -> None:
        if config.scoring != self._policy:
            raise ValueError("scorer must share the run's exact effective policy")

    def _cosine(self, vector: tuple[float, ...]) -> tuple[float, int]:
        current = unit_vector(vector)
        if len(current) != self._policy.encoder.dimensions:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        similarities = tuple(
            max(
                -1.0,
                min(
                    1.0,
                    math.fsum(left * right for left, right in zip(current, reference, strict=True)),
                ),
            )
            for reference in self._vectors
        )
        index = max(range(len(similarities)), key=similarities.__getitem__)
        return similarities[index], index

    async def _encode(
        self, texts: tuple[str, ...], budget: RunBudget, ledger: Ledger, url: str | None
    ) -> tuple[tuple[float, ...], ...]:
        service = self._policy.encoder
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
                batch = await self._encoder.encode_batch(batch_texts)
                call = batch.call
                # Revalidate injected objects: model_copy/update can bypass Pydantic guards.
                batch = EncodingBatch.model_validate(batch.model_dump())
                if (
                    call.service != service
                    or call.input_sha256 != expected_hashes
                    or call.input_chars != size
                ):
                    raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                output.extend(batch.vectors)
            except EncodingFailure as exc:
                call, refusal = exc.call, exc.code
                raise
            except EncodingCancelled as exc:
                call, refusal = exc.call, RefusalCode.BUDGET_EXHAUSTED
                raise
            except ValidationError:
                refusal = RefusalCode.ADAPTER_CONTRACT
                raise ChimeraRefused(refusal) from None
            except (ChimeraRefused, asyncio.CancelledError):
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
                        model=self._encoder.model,
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
                raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
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
        spans = selected_windows(document.text, goal, policy)
        if not spans:
            raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
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
            cosine, index = self._cosine(vector)
            reference = self._references.chunks[index]
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
            cosine, _ = self._cosine(vector)
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
            references_sha256=self._references.sha256,
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
                reason="shelf_cosine_not_probability",
            )
        )
        return tuple(ranked)
