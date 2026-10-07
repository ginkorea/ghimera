"""Real SQLite/FAISS and loopback encoding protocol; not multilingual model quality."""

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera import CorpusConfig, EvidenceCorpus, GhimeraConfig
from ghimera.corpus import document_passages
from ghimera.corpus_types import CorpusQuery
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.embedding import SelfHostedEncoder
from ghimera.fetch import FetchLadder
from ghimera.image_ocr import TesseractOcr
from ghimera.loop import GoalLoop
from ghimera.models import Extracted, Goal, Scope
from ghimera.refusals import EncodingFailure
from ghimera.visual_stage import VisualStage
from tests.test_embedding_scoring import endpoint, service
from tests.test_visuals import VisualRoute
from tests.test_visuals import recipe as visual_recipe
from tests.test_visuals import run_config as visual_run_config

__all__ = ["endpoint"]


def config(tmp_path, port, **updates):
    raw = dict(
        schema="ghimera.corpus/1",
        directory=tmp_path / "corpus",
        encoder=service(port),
        query_encoder=service(port, text_prefix="query: "),
        chunk_chars=80,
        overlap_chars=10,
        max_documents=100,
        max_chunks=1000,
        max_document_bytes=100000,
        max_stored_document_bytes=10000000,
        max_vector_bytes=1000000,
        max_audit_entries=10000,
        max_audit_bytes=10000000,
        max_encoding_calls_per_append=1000,
        max_encoding_chars_per_append=1000000,
        max_query_chars=100,
        max_top_k=10,
        search_candidates=50,
        minimum_cosine=0.5,
        hnsw_neighbors=16,
        hnsw_construction=64,
        hnsw_search=64,
        database_timeout_seconds=2.0,
        operation_timeout_seconds=10.0,
    )
    raw.update(updates)
    return CorpusConfig.model_validate(raw)


def corpus(policy, *, create):
    return EvidenceCorpus(
        policy,
        encoder=SelfHostedEncoder(policy.encoder),
        query_encoder=SelfHostedEncoder(policy.query_encoder),
        create=create,
    )


async def harvest(*texts):
    class NativeExtractor(FakeExtractor):
        async def extract(self, page):
            index = int(page.url.rsplit("/", 1)[-1])
            language, text = texts[index]
            return Extracted(
                title="native protocol fixture", text=text, language=language, links=()
            )

    cfg = GhimeraConfig.from_toml(Path("examples/chimera.toml"))
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=NativeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    return await loop.run(
        Goal(text="ports", seeds=tuple(f"https://source.example/{i}" for i in range(len(texts)))),
        Scope(allowed_hosts=("source.example",), max_depth=0, content_types=("text/html",)),
    )


def test_durable_native_passages_reopen_and_bind_queries_and_originals(tmp_path, endpoint):
    selected = config(tmp_path, endpoint[0])

    async def operation():
        material = await harvest(("zh", "港口基礎設施研究"), ("en", "unrelated schools"))
        store = corpus(selected, create=True)
        receipt = await store.append(material)
        assert receipt.generation == 1 and receipt.added_documents == 2
        assert receipt.added_passages == 2 and len(receipt.encoding_calls) == 1
        again = await store.append(material)
        assert again.added_documents == again.added_passages == 0
        assert again.generation == 1 and not again.encoding_calls
        assert len(endpoint[1]) == 1
        store.close()
        reopened = corpus(selected, create=False)
        found = await reopened.search("ports", top_k=2)
        assert len(found.hits) == 1 and found.hits[0].passage.text == "港口基礎設施研究"
        hit = found.hits[0]
        source = reopened.document(hit.passage.document_id)
        assert source.raw == material.documents[0].raw
        assert hit.passage.text == source.extracted.text[hit.passage.start : hit.passage.end]
        assert hit.passage.source_sha256 == hashlib.sha256(source.raw).hexdigest()
        assert found.encoding_call.service.text_prefix == "query: "
        assert CorpusQuery.model_validate_json(found.model_dump_json()) == found
        assert not (await reopened.search("ports", top_k=2, languages=("ja",))).hits
        reopened.close()

    asyncio.run(operation())
    database = sqlite3.connect(selected.directory / "corpus.sqlite")
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2
    assert database.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 3
    assert set(row[0] for row in database.execute("SELECT status FROM operations")) == {"committed"}
    database.close()
    assert selected.directory.stat().st_mode & 0o777 == 0o700
    assert (selected.directory / "corpus.sqlite").stat().st_mode & 0o777 == 0o600


def test_actual_fresh_process_query_rebuilds_native_index(tmp_path, endpoint):
    selected = config(tmp_path, endpoint[0])

    async def seed():
        store = corpus(selected, create=True)
        await store.append(await harvest(("zh", "港口基礎設施")))
        store.close()

    asyncio.run(seed())
    program = """
import asyncio,sys
from ghimera import CorpusConfig,EvidenceCorpus
from ghimera.embedding import SelfHostedEncoder
policy=CorpusConfig.model_validate_json(sys.stdin.read())
store=EvidenceCorpus(policy,encoder=SelfHostedEncoder(policy.encoder),query_encoder=SelfHostedEncoder(policy.query_encoder))
result=asyncio.run(store.search('ports',top_k=1))
store.close()
print(result.model_dump_json())
"""
    child = subprocess.run(
        [sys.executable, "-c", program],
        input=selected.model_dump_json(),
        text=True,
        capture_output=True,
        timeout=20,
        env=dict(os.environ, PYTHONPATH=str(Path.cwd() / "src")),
    )
    assert child.returncode == 0, child.stderr
    result = CorpusQuery.model_validate_json(child.stdout)
    assert result.generation == 1 and result.hits[0].passage.text == "港口基礎設施"


def test_failed_encoding_is_audited_and_does_not_publish_partial_documents(tmp_path, endpoint):
    selected = config(tmp_path, endpoint[0])
    endpoint[2]["failure"] = "wrong_model"

    async def operation():
        store = corpus(selected, create=True)
        with pytest.raises(EncodingFailure):
            await store.append(await harvest(("zh", "港口")))
        store.close()

    asyncio.run(operation())
    database = sqlite3.connect(selected.directory / "corpus.sqlite")
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert database.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert database.execute("SELECT status FROM operations").fetchone()[0] == "refused"
    evidence = json.loads(database.execute("SELECT payload FROM calls").fetchone()[0])
    assert evidence["outcome"] == "refused" and evidence["status"] == 200
    assert evidence["response_bytes"] > 0
    database.close()


def test_capacity_refuses_before_embedding_and_revision_cannot_mix(tmp_path, endpoint):
    selected = config(tmp_path, endpoint[0], max_documents=1)

    async def operation():
        store = corpus(selected, create=True)
        with pytest.raises(ValueError, match="capacity"):
            await store.append(await harvest(("zh", "港口"), ("en", "another")))
        assert not endpoint[1]
        await store.append(await harvest(("zh", "港口")))
        store.close()

    asyncio.run(operation())
    next_policy = config(
        tmp_path,
        endpoint[0],
        max_documents=1,
        encoder=service(endpoint[0], revision="fixture-2"),
        query_encoder=service(endpoint[0], revision="fixture-2", text_prefix="query: "),
    )
    with pytest.raises(ValueError, match="recorded"):
        corpus(next_policy, create=False)


def test_stored_vector_corruption_refuses_before_query_call(tmp_path, endpoint):
    selected = config(tmp_path, endpoint[0])

    async def operation():
        store = corpus(selected, create=True)
        await store.append(await harvest(("zh", "港口")))
        store.close()
        database = sqlite3.connect(selected.directory / "corpus.sqlite")
        database.execute("UPDATE chunks SET vector=?", (b"[0.0,1.0]",))
        database.commit()
        database.close()
        store = corpus(selected, create=False)
        with pytest.raises(ValueError, match="changed"):
            await store.search("ports", top_k=1)
        assert len(endpoint[1]) == 1
        store.close()

    asyncio.run(operation())


@pytest.mark.parametrize(
    "key,value", [("overlap_chars", 80), ("directory", Path("/")), ("search_candidates", 1)]
)
def test_invalid_corpus_configuration_is_not_a_runtime_guess(tmp_path, endpoint, key, value):
    with pytest.raises(ValidationError):
        config(tmp_path, endpoint[0], **{key: value})


def test_chunks_cover_native_scripts_without_translation_or_silent_truncation(tmp_path, endpoint):
    selected = config(tmp_path, endpoint[0], chunk_chars=10, overlap_chars=2)

    async def operation():
        data = await harvest(("ja", "日本政府と港湾施設についての調査結果です。"))
        chunks = document_passages(data.documents[0], selected)
        covered = set()
        for passage in chunks:
            passage.validate_source(data.documents[0])
            covered.update(range(passage.start, passage.end))
        assert covered == set(range(len(data.documents[0].extracted.text)))
        store = corpus(selected, create=True)
        await store.append(data)
        store.close()

    asyncio.run(operation())


def test_pending_append_does_not_block_committed_queries_or_lose_cancelled_audit(
    tmp_path, endpoint
):
    selected = config(tmp_path, endpoint[0])

    async def operation():
        store = corpus(selected, create=True)
        await store.append(await harvest(("zh", "港口")))
        entered = asyncio.Event()
        real = SelfHostedEncoder(selected.encoder)

        class DelayedEncoder:
            config = selected.encoder
            model = real.model

            async def encode(self, texts):
                return (await self.encode_batch(texts)).vectors

            async def encode_batch(self, texts):
                entered.set()
                await asyncio.Event().wait()

        # Deliberately held before the real wire, to test unavailable telemetry.
        store._encoder = DelayedEncoder()
        task = asyncio.create_task(store.append(await harvest(("en", "unrelated new source"))))
        await entered.wait()
        with pytest.raises(ValueError, match="active"):
            store.close()
        reader = corpus(selected, create=False)
        found = await reader.search("ports", top_k=1)
        assert found.generation == 1 and found.hits[0].passage.text == "港口"
        with pytest.raises(BlockingIOError):
            await reader.append(await harvest(("en", "different append")))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        reader.close()
        store.close()

    asyncio.run(operation())
    database = sqlite3.connect(selected.directory / "corpus.sqlite")
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert (
        database.execute("SELECT COUNT(*) FROM operations WHERE status='cancelled'").fetchone()[0]
        == 1
    )
    calls = [json.loads(row[0]) for row in database.execute("SELECT payload FROM calls")]
    assert sum(call["telemetry"] == "unavailable" for call in calls) == 1
    assert all(row[0] == 0 for row in database.execute("SELECT reserved_entries FROM operations"))
    database.close()


def test_audit_capacity_and_binary_vector_capacity_refuse_before_model_calls(tmp_path, endpoint):
    async def operation():
        material = await harvest(("zh", "港口"))
        for name, updates in (
            ("audit", {"max_audit_bytes": 1}),
            ("vector", {"max_vector_bytes": 1}),
        ):
            selected = config(tmp_path, endpoint[0], directory=tmp_path / name, **updates)
            store = corpus(selected, create=True)
            with pytest.raises(ValueError, match="capacity"):
                await store.append(material)
            store.close()
        assert not endpoint[1]

    asyncio.run(operation())


def test_actual_selective_visual_ocr_is_indexed_without_logo_or_fake_page_offsets(
    tmp_path, endpoint
):
    selected = config(tmp_path, endpoint[0])

    async def operation():
        visual = visual_recipe(tmp_path)
        cfg = visual_run_config(visual)
        route = VisualRoute()
        judge = FakeJudge()
        loop = GoalLoop(
            config=cfg,
            fetcher=FetchLadder((route,)),
            extractor=FakeExtractor(),
            scorer=KeywordScorer(),
            judge=judge,
            visual_stage=VisualStage(visual, ocr=TesseractOcr(visual), judge=judge),
        )
        material = await loop.run(
            Goal(text="ports", seeds=("https://example.org/start",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
        store = corpus(selected, create=True)
        receipt = await store.append(material)
        # Two native windows plus one OCR passage: neither page text nor OCR drops.
        assert receipt.added_documents == 1 and receipt.added_passages == 3
        # The protocol fixture distinguishes uppercase OCR from lowercase page
        # text by vector direction. This tests routing, not semantic relevance.
        found = await store.search("unrelated", top_k=10)
        # The fixture gives the second native window and OCR identical vectors.
        # Do not impose a semantic ordering that the protocol fixture cannot prove.
        assert len(found.hits) == 2
        visual_hits = [hit for hit in found.hits if hit.passage.kind == "image_ocr"]
        assert len(visual_hits) == 1
        hit = visual_hits[0]
        assert hit.passage.kind == "image_ocr"
        assert "PACIFIC" in hit.passage.text and "PORTS" in hit.passage.text
        original = store.document(hit.passage.document_id)
        assert len(original.images) == 1 and original.images[0].raw == route.raw
        assert hit.passage.image_sha256 == original.images[0].sha256
        assert hit.passage.regions == tuple(s.region for s in original.images[0].ocr.spans)
        assert all("logo" not in request.url for request in route.requests)
        hit.passage.validate_source(original)
        store.close()

    asyncio.run(operation())


def test_repeated_cancel_drains_native_build_without_query_spend(tmp_path, endpoint, monkeypatch):
    import ghimera.corpus as implementation

    selected = config(tmp_path, endpoint[0])

    async def operation():
        store = corpus(selected, create=True)
        await store.append(await harvest(("zh", "港口")))
        started, release = threading.Event(), threading.Event()
        original = implementation.NativePassageIndex

        def slow_build(config, vectors):
            started.set()
            assert release.wait(5)
            return original(config, vectors)

        monkeypatch.setattr(implementation, "NativePassageIndex", slow_build)
        task = asyncio.create_task(store.search("ports", top_k=1))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        with pytest.raises(ValueError, match="active"):
            store.close()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(endpoint[1]) == 1
        store.close()

    asyncio.run(operation())


def test_operator_capacity_and_ann_tuning_reuse_the_same_immutable_vectors(tmp_path, endpoint):
    selected = config(tmp_path, endpoint[0])

    async def operation():
        store = corpus(selected, create=True)
        receipt = await store.append(await harvest(("zh", "港口")))
        store.close()
        changed = config(
            tmp_path,
            endpoint[0],
            max_documents=200,
            minimum_cosine=0.8,
            hnsw_neighbors=24,
            hnsw_construction=80,
            hnsw_search=100,
        )
        assert changed.identity != selected.identity
        assert changed.recipe_identity == selected.recipe_identity
        reader = corpus(changed, create=False)
        found = await reader.search("ports", top_k=1)
        assert found.corpus_id == receipt.corpus_id and found.generation == 1
        assert found.config_sha256 == changed.identity
        assert found.hits[0].passage.text == "港口"
        assert len(endpoint[1]) == 2  # One passage call, one query; no re-embedding.
        reader.close()
        with pytest.raises(ValueError, match="recorded"):
            corpus(config(tmp_path, endpoint[0], chunk_chars=70), create=False)

    asyncio.run(operation())


def test_schema_corruption_refuses_on_open_before_any_new_model_call(tmp_path, endpoint):
    selected = config(tmp_path, endpoint[0])
    store = corpus(selected, create=True)
    store.close()
    database = sqlite3.connect(selected.directory / "corpus.sqlite")
    database.execute("UPDATE state SET schema='wrong-schema'")
    database.commit()
    database.close()
    with pytest.raises(ValueError, match="metadata"):
        corpus(selected, create=False)
    assert not endpoint[1]
