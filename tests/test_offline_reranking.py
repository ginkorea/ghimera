"""Native corpus integration with explicit controlled scores; no learned-quality claim."""

import asyncio
import hashlib
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.corpus import EvidenceCorpus
from ghimera.corpus_config import CorpusConfig
from ghimera.corpus_evidence import CorpusEvidenceReader
from ghimera.corpus_types import CorpusQuery
from ghimera.embedding import SelfHostedEncoder
from ghimera.reranking_config import OfflineRerankingConfig
from ghimera.reranking_types import (
    PassageScore,
    RerankCandidate,
    RerankingEvidence,
    RerankRequest,
    RerankScores,
    selection_decisions,
)
from tests.test_corpus_evidence import policy as reader_policy
from tests.test_encoding_recovery import FixtureHttp
from tests.test_evidence_corpus import config, harvest
from tests.test_hybrid_retrieval import policy as hybrid_policy


def rerank_policy(tmp_path, **updates):
    raw = dict(
        schema="ghimera.offline-reranking/1",
        runtime="transformers_sequence_classification/1",
        model_id="controlled-cross-encoder",
        revision="fixture/1",
        score_semantics="single_relevance_logit",
        input_recipe="query_document_pair/1",
        device="cpu",
        model_directory=tmp_path / "model",
        work_directory=tmp_path / "worker",
        worker_python=sys.executable,
        torch_version="operator-pinned",
        transformers_version="operator-pinned",
        artifacts=[
            dict(path="config.json", sha256="a" * 64, role="model"),
            dict(path="model.safetensors", sha256="b" * 64, role="model"),
            dict(path="tokenizer.json", sha256="c" * 64, role="tokenizer"),
        ],
        max_artifact_bytes=4096,
        cpu_threads=1,
        interop_threads=1,
        batch_size=2,
        max_workers=1,
        max_pairs=8,
        max_pair_tokens=16,
        max_input_chars=4096,
        max_request_bytes=65536,
        max_response_bytes=65536,
        max_diagnostic_bytes=8192,
        timeout_seconds=10.0,
        cleanup_timeout_seconds=5.0,
        max_passages_per_document=1,
        max_source_documents=2,
    )
    raw.update(updates)
    return OfflineRerankingConfig.model_validate(raw)


class ScoresPort:
    def __init__(self, policy, *, bad=None, prepare_failure=False):
        self.config = policy
        self.bad = bad
        self.prepare_failure = prepare_failure
        self.requests = []
        self.prepared = 0

    async def prepare(self):
        self.prepared += 1
        if self.prepare_failure:
            raise ValueError("controlled runtime admission failed")

    async def score(self, request):
        self.requests.append(request)
        scores = tuple(
            PassageScore(passage_id=row.passage_id, logit=-float(row.passage_id), pair_tokens=4)
            for row in request.candidates
        )
        if self.bad == "partial":
            scores = scores[:-1]
        elif self.bad == "duplicate" and scores:
            scores = scores[:-1] + (scores[0],)
        elif self.bad == "tokens":
            scores = tuple(row.model_copy(update={"pair_tokens": 100}) for row in scores)
        elif self.bad == "nan":
            scores = tuple(row.model_copy(update={"logit": float("nan")}) for row in scores)
        return RerankScores.model_construct(
            schema_version="ghimera.rerank-scores/1",
            request_sha256="0" * 64 if self.bad == "foreign" else request.sha256,
            scores=scores,
        )


def native_store(policy, port, *, create=False, query=None):
    return EvidenceCorpus(
        policy,
        create=create,
        encoder=SelfHostedEncoder(policy.encoder, http=FixtureHttp(policy.encoder)),
        query_encoder=SelfHostedEncoder(
            policy.query_encoder, http=query or FixtureHttp(policy.query_encoder)
        ),
        reranker=port,
    )


def learned_corpus(tmp_path, **updates):
    return config(
        tmp_path,
        12345,
        schema="ghimera.corpus/2",
        minimum_cosine=-1.0,
        reranking=rerank_policy(tmp_path),
        **updates,
    )


def test_versioned_policy_preserves_legacy_bytes_and_vector_recipe(tmp_path):
    old = config(tmp_path, 12345)
    assert "reranking" not in old.model_dump() and '"reranking":' not in old.model_dump_json()
    # Frozen-base native fixture hashes; no new policy may change legacy receipts.
    assert old.identity == "678685819cbd49fc00eb86263b3e0ec1cac5c2fd0de007e918ae7da58bbdf3e9"
    assert old.recipe_identity == "95dcfd73164b8450962b034fa2a553ba02cc1a446cff018032c4690838181761"
    new = CorpusConfig.model_validate(
        dict(old.model_dump(), schema="ghimera.corpus/2", reranking=rerank_policy(tmp_path))
    )
    assert new.recipe_identity == old.recipe_identity
    assert new.identity != old.identity
    assert CorpusConfig.model_validate_json(new.model_dump_json()) == new
    for raw in (
        dict(old.model_dump(), reranking=rerank_policy(tmp_path)),
        dict(old.model_dump(), schema="ghimera.corpus/2"),
    ):
        with pytest.raises(ValidationError):
            CorpusConfig.model_validate(raw)
    with pytest.raises(ValueError, match="explicit offline policy"):
        native_store(old, ScoresPort(rerank_policy(tmp_path)), create=True)
    assert not old.directory.exists()


def test_example_is_typed_inert_and_operational_schema_advertises_opt_in():
    raw = tomllib.loads(Path("examples/offline-reranking.toml").read_text())
    policy = OfflineRerankingConfig.model_validate(raw)
    assert policy.device == "cpu" and all(row.sha256 == "0" * 64 for row in policy.artifacts)
    shape = CorpusConfig.model_json_schema()
    assert "ghimera.corpus/2" in shape["properties"]["schema"]["enum"]
    assert "OfflineRerankingConfig" in shape["$defs"]


def test_full_union_has_native_hashes_complete_logits_and_source_coverage_before_top_k(tmp_path):
    async def operation():
        policy = learned_corpus(tmp_path, chunk_chars=8, overlap_chars=0)
        port = ScoresPort(policy.reranking)
        store = native_store(policy, port, create=True)
        material = await harvest(("en", "ports A ports B"), ("en", "ports C"))
        try:
            await store.append(material)
            query = await store.search(
                "ports", top_k=2, retrieval=hybrid_policy(vector_candidates=1, lexical_candidates=8)
            )
            assert query.schema_version == "ghimera.corpus-query/2"
            assert len(port.requests) == 1 and port.prepared == 1
            assert len(query.reranking.request.candidates) == len(query.retrieval.ranking) == 3
            assert tuple(row.passage_id for row in port.requests[0].candidates) == tuple(
                row.passage_id for row in query.retrieval.ranking
            )
            assert len(query.hits) == len({hit.passage.document_id for hit in query.hits}) == 2
            assert {row.reason for row in query.reranking.decisions} == {
                "selected",
                "passages_per_document",
            }
            assert all(row.logit < 0 for row in query.reranking.scores.scores)
            assert CorpusQuery.model_validate_json(query.model_dump_json()) == query
            for hit in query.hits:
                original = store.document(hit.passage.document_id)
                hit.passage.validate_source(original)
                assert original in material.documents
            altered = query.model_dump()
            altered["reranking"]["request"]["generation"] += 1
            with pytest.raises(ValidationError):
                CorpusQuery.model_validate(altered)
            for field in ("hits", "retrieval"):
                altered = query.model_dump()
                if field == "hits":
                    altered[field] = tuple(reversed(altered[field]))
                else:
                    altered[field]["ranking"] = altered[field]["ranking"][:-1]
                with pytest.raises(ValidationError):
                    CorpusQuery.model_validate(altered)
            bundle = await CorpusEvidenceReader(
                reader_policy(
                    store,
                    retrieval=hybrid_policy(vector_candidates=1, lexical_candidates=8),
                    minimum_cosine=-1.0,
                ),
                store,
            ).read("ports")
            assert bundle.query.reranking is not None and bundle.sources
            from ghimera.research_reuse import ResearchRetrievalReport
            from ghimera.research_reuse_config import ResearchReuseConfig

            reuse = ResearchReuseConfig(
                schema="ghimera.research-reuse/1",
                reader=bundle.policy,
                max_queries=1,
                max_input_chars=1000,
                max_source_documents=5,
                max_snapshot_bytes=1_000_000,
                query_mode="intent_and_planned_queries",
                assess_before_discovery=True,
                source_age_policy="explicit_unknown",
            )
            with pytest.raises(ValueError, match="run-bound rerank"):
                ResearchRetrievalReport(
                    schema="ghimera.research-retrieval/1",
                    policy=reuse,
                    intent="ports",
                    observations=(),
                    snapshots=(bundle,),
                )
            identity = store.identity
            store.close()
            reopened = native_store(policy, ScoresPort(policy.reranking))
            try:
                again = await reopened.search(
                    "ports",
                    top_k=2,
                    retrieval=hybrid_policy(vector_candidates=1, lexical_candidates=8),
                )
                assert reopened.identity == identity
                assert again.reranking == query.reranking and again.hits == query.hits
            finally:
                reopened.close()
        finally:
            store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("bad", ["partial", "duplicate", "foreign", "nan", "tokens"])
def test_incomplete_changed_or_unbounded_scores_refuse_without_rrf_fallback(tmp_path, bad):
    async def operation():
        policy = learned_corpus(tmp_path)
        port = ScoresPort(policy.reranking, bad=bad)
        store = native_store(policy, port, create=True)
        try:
            await store.append(await harvest(("en", "ports A"), ("en", "ports B")))
            with pytest.raises(ValueError):
                await store.search("ports", top_k=1, retrieval=hybrid_policy())
            assert len(port.requests) == 1
        finally:
            store.close()

    asyncio.run(operation())


def test_runtime_and_hybrid_admission_precede_query_encoding(tmp_path):
    async def operation():
        policy = learned_corpus(tmp_path)
        wire = FixtureHttp(policy.query_encoder)
        port = ScoresPort(policy.reranking, prepare_failure=True)
        store = native_store(policy, port, create=True, query=wire)
        try:
            await store.append(await harvest(("en", "ports")))
            with pytest.raises(ValueError, match="complete hybrid"):
                await store.search("ports", top_k=1)
            assert port.prepared == 0 and wire.calls == 0
            with pytest.raises(ValueError, match="runtime admission"):
                await store.search("ports", top_k=1, retrieval=hybrid_policy())
            assert wire.calls == 0 and not port.requests
        finally:
            store.close()

    asyncio.run(operation())


def test_mutated_port_policy_refuses_before_query_encoding(tmp_path):
    async def operation():
        policy = learned_corpus(tmp_path)
        wire = FixtureHttp(policy.query_encoder)
        port = ScoresPort(policy.reranking)
        store = native_store(policy, port, create=True, query=wire)
        try:
            port.config = port.config.model_copy(update={"revision": "changed"})
            with pytest.raises(ValueError, match="changed its exact"):
                await store.search("ports", top_k=1, retrieval=hybrid_policy())
            assert wire.calls == 0 and not port.requests and port.prepared == 0
        finally:
            store.close()

    asyncio.run(operation())


def test_injected_port_score_timeout_has_no_unbounded_wait_or_fallback(tmp_path):
    class WaitingPort(ScoresPort):
        async def score(self, request):
            await asyncio.Event().wait()

    async def operation():
        policy = learned_corpus(tmp_path)
        policy = CorpusConfig.model_validate(
            dict(policy.model_dump(), reranking=rerank_policy(tmp_path, timeout_seconds=0.01))
        )
        wire = FixtureHttp(policy.query_encoder)
        store = native_store(policy, WaitingPort(policy.reranking), create=True, query=wire)
        try:
            await store.append(await harvest(("en", "ports")))
            with pytest.raises(TimeoutError):
                await store.search("ports", top_k=1, retrieval=hybrid_policy())
            assert wire.calls == 1
        finally:
            store.close()

    asyncio.run(operation())


def request_fixture(tmp_path):
    candidates = tuple(
        RerankCandidate(
            passage_id=index + 1,
            document_id=doc * 64,
            passage_sha256="f" * 64,
            text=text,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            cosine=cosine,
            language=language,
        )
        for index, (doc, text, cosine, language) in enumerate(
            (
                ("a", "ports A", 1.0, "en"),
                ("a", "ports B", 1.0, "en"),
                ("b", "ports C", 0.0, "en"),
                ("c", "ports D", 1.0, "zh"),
                ("d", "ports E", 1.0, "en"),
                ("e", "ports F", 1.0, "en"),
            )
        )
    )
    return RerankRequest(
        schema="ghimera.rerank-request/1",
        policy=rerank_policy(tmp_path),
        corpus_id="0" * 32,
        config_sha256="e" * 64,
        generation=1,
        query="ports",
        candidates=candidates,
    )


@pytest.mark.parametrize("bound", ["max_input_chars", "max_request_bytes"])
def test_native_request_bounds_also_apply_to_injected_ports(tmp_path, bound):
    request = request_fixture(tmp_path)
    with pytest.raises(ValidationError):
        RerankRequest.model_validate(
            dict(request.model_dump(), policy=rerank_policy(tmp_path, **{bound: 1}))
        )


def test_native_response_byte_bound_is_not_delegated_to_injected_ports(tmp_path):
    request = request_fixture(tmp_path).model_copy(
        update={"policy": rerank_policy(tmp_path, max_response_bytes=1)}
    )
    scores = asyncio.run(ScoresPort(request.policy).score(request))
    with pytest.raises(ValueError):
        scores.validate_request(request)


def test_every_candidate_has_an_explicit_admission_or_selection_reason(tmp_path):
    request = request_fixture(tmp_path)
    scores = asyncio.run(ScoresPort(request.policy).score(request))
    decisions = selection_decisions(request, scores, top_k=2, minimum_cosine=0.5, languages=("en",))
    assert tuple(row.reason for row in decisions) == (
        "selected",
        "passages_per_document",
        "minimum_cosine",
        "language",
        "selected",
        "document_limit",
    )
    evidence = RerankingEvidence(
        schema="ghimera.reranking-evidence/1",
        request=request,
        scores=scores,
        top_k=2,
        minimum_cosine=0.5,
        languages=("en",),
        decisions=decisions,
    )
    assert type(evidence).model_validate_json(evidence.model_dump_json()) == evidence
    assert "top_k" in {
        row.reason
        for row in selection_decisions(request, scores, top_k=1, minimum_cosine=-1.0, languages=())
    }
    with pytest.raises(ValidationError):
        RerankRequest.model_validate(
            dict(request.model_dump(), policy=rerank_policy(tmp_path, max_pairs=2))
        )


def test_research_admission_refuses_unreserved_learned_calls_before_contact(tmp_path):
    from ghimera.corpus_search import CorpusLeadSearch
    from ghimera.research_reuse import RetainedResearchSession
    from ghimera.research_reuse_config import ResearchReuseConfig
    from tests.test_corpus_search import binding as search_policy

    policy = learned_corpus(tmp_path)
    port = ScoresPort(policy.reranking)
    store = native_store(policy, port, create=True)
    try:
        reader = CorpusEvidenceReader(
            reader_policy(store, retrieval=hybrid_policy(), minimum_cosine=-1.0), store
        )
        configured = ResearchReuseConfig(
            schema="ghimera.research-reuse/1",
            reader=reader.policy,
            max_queries=1,
            max_input_chars=1000,
            max_source_documents=5,
            max_snapshot_bytes=1_000_000,
            query_mode="intent_and_planned_queries",
            assess_before_discovery=True,
            source_age_policy="explicit_unknown",
        )
        with pytest.raises(ValueError, match="run-bound rerank"):
            RetainedResearchSession(configured, reader, "ports")
        with pytest.raises(ValueError, match="run-bound rerank"):
            CorpusLeadSearch(search_policy(store, minimum_cosine=-1.0), store)
        assert not port.requests and port.prepared == 0
    finally:
        store.close()
