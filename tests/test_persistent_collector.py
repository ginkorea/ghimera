"""Actual configured collection and corpus HTTP adapters; not model quality."""

import asyncio

import pytest
from pydantic import ValidationError

from ghimera import (
    Collector,
    CorpusHandoffCancelled,
    CorpusHandoffFailure,
    EvidenceCorpus,
    PersistentCollection,
    PersistentCollector,
)
from ghimera.continuation import ResearchSuspended
from ghimera.embedding import SelfHostedEncoder
from ghimera.models import Goal, Scope
from tests.test_collector import (
    assembled,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)
from tests.test_evidence_corpus import config, corpus, harvest
from tests.test_http_fetch import ResolverFixture

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def test_configured_research_automatically_persists_and_reopens_native_sources(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, url = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    policy = config(tmp_path, encoder_endpoint[0])

    async def operation():
        store = corpus(policy, create=True)
        service = PersistentCollector(Collector(cfg, source_resolver=ResolverFixture()), store)
        completed = await service.run("find ports")
        assert completed.result.status == "answered"
        assert completed.corpus.added_documents == 1
        assert completed.corpus.encoding_calls
        assert PersistentCollection.model_validate_json(completed.model_dump_json()) == completed
        assert len(encoder_endpoint[1]) == (
            completed.harvest.receipt.encoding_calls + len(completed.corpus.encoding_calls)
        )
        repeated = await service.persist(completed.result)
        assert repeated.corpus.added_documents == 0 and not repeated.corpus.encoding_calls
        assert source_site[1]["/plain"] == 1 and len(search_endpoint[1]) == 1
        corrupted = completed.model_dump()
        corrupted["corpus"]["harvest_sha256"] = "0" * 64
        with pytest.raises(ValidationError, match="exact collected harvest"):
            PersistentCollection.model_validate(corrupted)
        store.close()
        reopened = corpus(policy, create=False)
        # The protocol fixture uses literal keyword vectors, not semantics:
        # "ports" and the source's singular "port" have orthogonal vectors.
        # Query retained native text to test reopen/binding, not invented quality.
        native_query = completed.harvest.documents[0].extracted.text[: policy.chunk_chars]
        found = await reopened.search(native_query, top_k=3)
        assert found.hits and all(hit.passage.source_url == url for hit in found.hits)
        assert all(
            reopened.document(hit.passage.document_id).raw == completed.harvest.documents[0].raw
            for hit in found.hits
        )
        for hit in found.hits:
            hit.passage.validate_source(reopened.document(hit.passage.document_id))
        reopened.close()

    asyncio.run(operation())


def test_cancelled_handoff_retains_sources_drains_work_and_releases_facade(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    policy = config(tmp_path, encoder_endpoint[0])

    async def operation():
        entered, release = asyncio.Event(), asyncio.Event()

        class HeldEncoder(SelfHostedEncoder):
            async def encode_batch(self, texts):
                entered.set()
                await release.wait()
                return await super().encode_batch(texts)

        store = EvidenceCorpus(
            policy,
            encoder=HeldEncoder(policy.encoder),
            query_encoder=SelfHostedEncoder(policy.query_encoder),
            create=True,
        )
        service = PersistentCollector(Collector(cfg, source_resolver=ResolverFixture()), store)
        material = await harvest(("zh", "港口基礎設施"))
        task = asyncio.create_task(service.persist(material))
        async with asyncio.timeout(3):
            await entered.wait()
        with pytest.raises(ValueError, match="active"):
            await service.persist(material)
        with pytest.raises(ValueError, match="active"):
            store.close()
        task.cancel()
        with pytest.raises(CorpusHandoffCancelled) as stopped:
            await task
        assert stopped.value.result == material
        assert not encoder_endpoint[1]
        release.set()
        completed = await service.persist(stopped.value.result)
        assert completed.corpus.added_documents == 1
        assert len(encoder_endpoint[1]) == 1
        store.close()

    asyncio.run(operation())


def test_closed_corpus_refuses_new_source_work_before_any_calls(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    store = corpus(config(tmp_path, encoder_endpoint[0]), create=True)
    service = PersistentCollector(Collector(cfg, source_resolver=ResolverFixture()), store)
    store.close()
    with pytest.raises(ValueError, match="closed"):
        asyncio.run(service.run("find ports"))
    assert not source_site[1] and not search_endpoint[1]
    assert not encoder_endpoint[1] and not model_endpoint[1]


def test_suspended_research_resumes_into_corpus_without_refetching_sources(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, _ = assembled(
        tmp_path,
        source_site,
        search_endpoint,
        model_endpoint,
        encoder_endpoint,
        journal={
            "schema": "chimera.run-journal-config/1",
            "directory": str(tmp_path / "journal"),
            "max_record_bytes": 2000000,
            "max_journal_bytes": 10000000,
            "max_summary_bytes": 2000000,
            "max_records": 2000,
        },
        continuation={
            "schema": "ghimera.continuation/1",
            "max_checkpoint_bytes": 4000000,
            "clock_policy": "include_downtime",
        },
    )

    async def operation():
        store = corpus(config(tmp_path, encoder_endpoint[0]), create=True)
        service = PersistentCollector(Collector(cfg, source_resolver=ResolverFixture()), store)
        with pytest.raises(ResearchSuspended) as stopped:
            await service.run("find ports", run_id="persist-resume", suspend_after_rounds=1)
        source_calls, search_calls = dict(source_site[1]), len(search_endpoint[1])
        completed = await service.resume(
            "persist-resume", checkpoint_sha256=stopped.value.receipt.sha256
        )
        assert completed.result.status == "answered"
        assert completed.corpus.added_documents == 1
        assert source_site[1] == source_calls and len(search_endpoint[1]) == search_calls
        store.close()

    asyncio.run(operation())


def test_failed_handoff_retains_completed_collection_without_repeating_sources(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, url = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    small = config(tmp_path, encoder_endpoint[0], max_encoding_chars_per_append=1)
    expanded = config(tmp_path, encoder_endpoint[0])
    goal = Goal(text="ports", seeds=(url,))
    scope = Scope(
        allowed_hosts=("fixture.example",),
        allowed_ports=(source_site[0],),
        max_depth=0,
        content_types=("text/html",),
    )

    async def operation():
        store = corpus(small, create=True)
        collector = Collector(cfg, source_resolver=ResolverFixture())
        service = PersistentCollector(collector, store)
        with pytest.raises(CorpusHandoffFailure) as failed:
            await service.collect(goal, scope)
        material = failed.value.result
        assert failed.value.__cause__ is not None
        assert material.documents and source_site[1]["/plain"] == 1
        store.close()
        repaired = corpus(expanded, create=False)
        completed = await PersistentCollector(collector, repaired).persist(material)
        assert completed.corpus.added_documents == 1
        assert source_site[1]["/plain"] == 1 and not search_endpoint[1]
        repaired.close()

    asyncio.run(operation())
