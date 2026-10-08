"""Bounded native-script lexical retrieval and reproducible rank fusion.

This owner consumes immutable passages from the native corpus generation;
it has no transport, storage, translation or implicit model dependency.
"""

import hashlib
import math
import re
import unicodedata
from collections import Counter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]


class HybridRetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.hybrid-retrieval/1"] = Field(alias="schema")
    tokenizer: Literal["unicode_words_and_script_bigrams/1"]
    reranker: Literal["weighted_reciprocal_rank_fusion/1"]
    vector_candidates: Positive
    lexical_candidates: Positive
    max_passages: Positive
    max_text_chars: Positive
    max_tokens_per_passage: Positive
    rank_constant: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    vector_weight: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    lexical_weight: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    bm25_k1: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    bm25_b: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class RankedPassage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    passage_id: Positive
    vector_rank: Positive | None
    lexical_rank: Positive | None
    lexical_score: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    fused_score: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class RetrievalEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.retrieval-evidence/1"] = Field(alias="schema")
    policy: HybridRetrievalConfig
    passage_count: Annotated[int, Field(strict=True, ge=0)]
    text_chars: Annotated[int, Field(strict=True, ge=0)]
    ranking: tuple[RankedPassage, ...]

    @model_validator(mode="after")
    def coherent(self) -> "RetrievalEvidence":
        policy = self.policy
        if self.passage_count > policy.max_passages or self.text_chars > policy.max_text_chars:
            raise ValueError("lexical corpus exceeded its admitted bounds")
        seen: set[int] = set()
        previous: tuple[float, int] | None = None
        for hit in self.ranking:
            score = 0.0
            if hit.vector_rank is not None:
                if hit.vector_rank > policy.vector_candidates:
                    raise ValueError("vector rank exceeds its candidate boundary")
                score += policy.vector_weight / (policy.rank_constant + hit.vector_rank)
            if hit.lexical_rank is not None:
                if hit.lexical_rank > policy.lexical_candidates or hit.lexical_score <= 0:
                    raise ValueError("lexical rank requires its observed score and bound")
                score += policy.lexical_weight / (policy.rank_constant + hit.lexical_rank)
            elif hit.lexical_score != 0:
                raise ValueError("unselected lexical candidates cannot claim lexical evidence")
            key = (-hit.fused_score, hit.passage_id)
            if (
                hit.passage_id in seen
                or not math.isclose(hit.fused_score, score, rel_tol=1e-12)
                or (previous is not None and key < previous)
            ):
                raise ValueError("ranking must replay its distinct deterministic fusion")
            seen.add(hit.passage_id)
            previous = key
        return self


def native_tokens(text: str) -> tuple[str, ...]:
    """Keep native evidence untouched; normalize only this search representation.

    CJK, Hangul and Southeast Asian scripts have useful character ngrams even
    without whitespace. This tokenizer is a recipe, not language-quality admission.
    """
    normalized = unicodedata.normalize("NFKC", text).casefold()
    output: list[str] = []
    for word in re.findall(r"[^\W_]+", normalized):
        if any(
            "CJK" in unicodedata.name(char, "")
            or "HIRAGANA" in unicodedata.name(char, "")
            or "KATAKANA" in unicodedata.name(char, "")
            or "HANGUL" in unicodedata.name(char, "")
            or "THAI" in unicodedata.name(char, "")
            or "LAO" in unicodedata.name(char, "")
            or "KHMER" in unicodedata.name(char, "")
            or "MYANMAR" in unicodedata.name(char, "")
            for char in word
        ):
            output.extend(word)
            output.extend(word[i : i + 2] for i in range(len(word) - 1))
        else:
            output.append(word)
    return tuple(output)


class NativeLexicalIndex:
    def __init__(
        self, policy: HybridRetrievalConfig, passages: tuple[tuple[int, str], ...]
    ) -> None:
        self.policy = HybridRetrievalConfig.model_validate(policy.model_dump())
        self.text_chars = sum(len(text) for _, text in passages)
        if len(passages) > policy.max_passages or self.text_chars > policy.max_text_chars:
            raise ValueError("lexical corpus exceeded its admitted bounds; no silent truncation")
        if len({identity for identity, _ in passages}) != len(passages):
            raise ValueError("lexical passages require distinct native identities")
        self._counts: dict[int, Counter[str]] = {}
        self._frequency: Counter[str] = Counter()
        for identity, text in passages:
            tokens = native_tokens(text)
            if len(tokens) > policy.max_tokens_per_passage:
                raise ValueError("passage token budget exhausted; no silent truncation")
            counts = Counter(tokens)
            self._counts[identity] = counts
            self._frequency.update(counts.keys())
        self._average = sum(sum(c.values()) for c in self._counts.values()) / max(
            1, len(self._counts)
        )

    def admit_query(self, text: str) -> set[str]:
        query = set(native_tokens(text))
        if len(query) > self.policy.max_tokens_per_passage:
            raise ValueError("query token budget exhausted")
        return query

    def search(self, text: str) -> tuple[tuple[int, float], ...]:
        policy = self.policy
        query = self.admit_query(text)
        scored: list[tuple[int, float]] = []
        size = len(self._counts)
        for identity, counts in self._counts.items():
            score = 0.0
            length = sum(counts.values())
            for term in query & counts.keys():
                frequency = self._frequency[term]
                inverse = math.log(1 + (size - frequency + 0.5) / (frequency + 0.5))
                tf = counts[term]
                denominator = tf + policy.bm25_k1 * (
                    1 - policy.bm25_b + policy.bm25_b * length / self._average
                )
                score += inverse * tf * (policy.bm25_k1 + 1) / denominator
            if score > 0:
                scored.append((identity, score))
        return tuple(sorted(scored, key=lambda row: (-row[1], row[0]))[: policy.lexical_candidates])

    def rerank(self, text: str, vector_ids: tuple[int, ...]) -> RetrievalEvidence:
        policy = self.policy
        if (
            len(vector_ids) > policy.vector_candidates
            or len(set(vector_ids)) != len(vector_ids)
            or not set(vector_ids) <= self._counts.keys()
        ):
            raise ValueError("vector candidates must bind distinct admitted native passages")
        vectors = {identity: rank for rank, identity in enumerate(vector_ids, start=1)}
        lexical = {
            identity: (rank, score)
            for rank, (identity, score) in enumerate(self.search(text), start=1)
        }
        rows: list[RankedPassage] = []
        for identity in vectors.keys() | lexical.keys():
            vrank, (lrank, lscore) = vectors.get(identity), lexical.get(identity, (None, 0.0))
            score = (policy.vector_weight / (policy.rank_constant + vrank) if vrank else 0) + (
                policy.lexical_weight / (policy.rank_constant + lrank) if lrank else 0
            )
            rows.append(
                RankedPassage(
                    passage_id=identity,
                    vector_rank=vrank,
                    lexical_rank=lrank,
                    lexical_score=lscore,
                    fused_score=score,
                )
            )
        return RetrievalEvidence(
            schema="ghimera.retrieval-evidence/1",
            policy=policy,
            passage_count=len(self._counts),
            text_chars=self.text_chars,
            ranking=tuple(sorted(rows, key=lambda row: (-row.fused_score, row.passage_id))),
        )
