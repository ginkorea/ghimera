"""Native lexical/vector integration; fixture encodings are not model-quality admission."""

import asyncio

import pytest
from pydantic import ValidationError

from ghimera.corpus_evidence import CorpusEvidenceReader
from ghimera.corpus_search import CorpusLeadSearch
from ghimera.corpus_search_wire import CorpusSearchWire
from ghimera.corpus_types import CorpusQuery
from ghimera.retrieval import (
    HybridRetrievalConfig,
    NativeLexicalIndex,
    RetrievalEvidence,
    native_tokens,
)
from tests.test_corpus_evidence import policy as evidence_policy
from tests.test_corpus_search import binding, request
from tests.test_embedding_scoring import endpoint
from tests.test_evidence_corpus import config, corpus, harvest

__all__ = ["endpoint"]


def policy(**updates):
    values = dict(
        schema="ghimera.hybrid-retrieval/1",
        tokenizer="unicode_words_and_script_bigrams/1",
        reranker="weighted_reciprocal_rank_fusion/1",
        vector_candidates=2,
        lexical_candidates=3,
        max_passages=1000,
        max_text_chars=100_000,
        max_tokens_per_passage=1000,
        rank_constant=60.0,
        vector_weight=1.0,
        lexical_weight=2.0,
        bm25_k1=1.2,
        bm25_b=0.75,
    )
    values.update(updates)
    return HybridRetrievalConfig.model_validate(values)


@pytest.mark.parametrize("native", ["港口报告", "港口報告", "東京港", "항만 연구", "ท่าเรือ"])
def test_native_script_lexical_selection_never_translates_original(native):
    index = NativeLexicalIndex(policy(), ((1, "unrelated schools"), (2, native)))
    ranking = index.rerank(native, (1,))
    assert ranking.ranking[0].passage_id == 2
    assert ranking.ranking[0].vector_rank is None
    assert ranking.ranking[0].lexical_rank == 1
    assert native_tokens(native)
    assert RetrievalEvidence.model_validate_json(ranking.model_dump_json()) == ranking
    bad = ranking.model_dump()
    bad["ranking"][0]["fused_score"] = 10.0
    with pytest.raises(ValidationError):
        RetrievalEvidence.model_validate(bad)


@pytest.mark.parametrize(
    "updates", [dict(max_passages=1), dict(max_text_chars=1), dict(max_tokens_per_passage=1)]
)
def test_exhausted_lexical_admission_refuses_without_silent_truncation(updates):
    with pytest.raises(ValueError):
        NativeLexicalIndex(policy(**updates), ((1, "ports report"), (2, "school report")))


def test_query_token_admission_is_available_before_model_work():
    index = NativeLexicalIndex(policy(max_tokens_per_passage=1), ((1, "ports"),))
    with pytest.raises(ValueError, match="query token budget"):
        index.admit_query("ports report")


def test_native_corpus_query_reranks_retained_offsets_and_reopens(tmp_path, endpoint):
    async def operation():
        selected = config(tmp_path, endpoint[0], minimum_cosine=-1.0)
        store = corpus(selected, create=True)
        material = await harvest(("zh-Hans", "港口报告"), ("en", "school report"))
        try:
            await store.append(material)
        finally:
            store.close()
        reopened = corpus(selected, create=False)
        try:
            query = await reopened.search("school report", top_k=2, retrieval=policy())
            assert query.hits[0].passage.text == "school report"
            assert query.hits[0].passage.language == "en"
            assert query.retrieval is not None
            assert query.retrieval.passage_count == 2
            assert query.generation == 1
            assert CorpusQuery.model_validate_json(query.model_dump_json()) == query
            legacy = await reopened.search("ports", top_k=1)
            assert "retrieval" not in legacy.model_dump()
            cfg = binding(reopened, retrieval=policy(), minimum_cosine=-1.0)
            result = await CorpusLeadSearch(cfg, reopened).request(
                request(query=dict(text="school report", question_ids=("q1",)))
            )
            wire = CorpusSearchWire.model_validate_json(result.raw)
            wire.validate_policy(cfg, "school report")
            assert result.hits[0].snippet == "school report"
            assert wire.sources[0].raw == material.documents[1].raw
            bundle = await CorpusEvidenceReader(
                evidence_policy(reopened, retrieval=policy(), minimum_cosine=-1.0), reopened
            ).read("school report")
            assert bundle.hits[0].passage.text == "school report"
            assert bundle.query.retrieval.policy == policy()
            with pytest.raises(ValueError):
                wire.validate_policy(binding(reopened, minimum_cosine=-1.0), "school report")
            before = len(endpoint[1])
            with pytest.raises(ValueError):
                await reopened.search("ports", top_k=1, retrieval=policy(max_passages=1))
            assert len(endpoint[1]) == before  # Admission failure precedes query inference.
        finally:
            reopened.close()

    asyncio.run(operation())
