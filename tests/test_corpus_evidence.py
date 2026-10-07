"""Real persisted originals/FAISS, scripted encoders; no model-quality claim."""

import asyncio
import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera import CorpusEvidenceBundle, CorpusEvidenceConfig, CorpusEvidenceReader
from ghimera.doubles import FakeExtractor, FakeJudge, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.image_ocr import TesseractOcr
from ghimera.loop import GoalLoop
from ghimera.models import Document, Goal, Scope
from ghimera.visual_config import VisualConfig
from ghimera.visual_model import LocalVisionReader
from ghimera.visual_stage import VisualStage
from tests.test_evidence_corpus import config, corpus, endpoint, harvest
from tests.test_pdf_transcription_corpus import reviewed_harvest
from tests.test_served_models import service
from tests.test_visuals import VisionWire, VisualRoute, recipe, run_config

__all__ = ["endpoint"]


def policy(store, **updates):
    values = dict(
        schema="ghimera.corpus-evidence-config/1",
        corpus_id=store.identity,
        corpus_config_sha256=store.config.identity,
        query_encoder=store.config.query_encoder,
        max_query_chars=store.config.max_query_chars,
        max_passage_hits=store.config.max_top_k,
        max_documents=5,
        max_original_bytes=100000,
        max_response_bytes=500000,
        minimum_cosine=store.config.minimum_cosine,
        languages=(),
        timeout_seconds=10.0,
        source_mode="retained_snapshot",
    )
    return CorpusEvidenceConfig.model_validate(values | updates)


def test_retained_context_reopens_without_source_fetch_or_current_acceptance(tmp_path, endpoint):
    async def operation():
        material = await harvest(("zh", "港口基礎設施研究港口基礎設施研究"))
        selected = config(tmp_path, endpoint[0], chunk_chars=8, overlap_chars=2)
        store = corpus(selected, create=True)
        await store.append(material)
        append_calls = len(endpoint[1])
        bound = policy(store)
        store.close()
        reopened = corpus(selected, create=False)
        try:
            evidence = await CorpusEvidenceReader(bound, reopened).read("ports")
            assert evidence.sources == material.documents
            assert len(evidence.hits) > 1  # Do not drop the other passages of one source.
            assert evidence.selected_passages == tuple(h.passage_id for h in evidence.query.hits)
            assert evidence.source_mode == "retained_snapshot"
            assert evidence.source_age == "unknown" and not evidence.current_intent_verified
            assert not evidence.omissions
            assert CorpusEvidenceBundle.model_validate_json(evidence.model_dump_json()) == evidence
            assert len(endpoint[1]) == append_calls + 1
            assert endpoint[1][-1][1]["input"] == ["query: ports"]
            assert reopened.identity == bound.corpus_id  # Reader did not close the borrowed corpus.
        finally:
            reopened.close()

    asyncio.run(operation())


@pytest.mark.parametrize(
    "updates",
    [
        {"corpus_id": "0" * 32},
        {"corpus_config_sha256": "0" * 64},
        {"max_passage_hits": 11},
        {"minimum_cosine": 0.0},
    ],
)
def test_wrong_corpus_recipe_or_limits_refuse_before_spend(tmp_path, endpoint, updates):
    store = corpus(config(tmp_path, endpoint[0]), create=True)
    try:
        with pytest.raises(ValueError, match="exact admitted"):
            CorpusEvidenceReader(policy(store, **updates), store)
        assert not endpoint[1]
    finally:
        store.close()


def test_closed_corpus_and_bad_query_refuse_before_spend(tmp_path, endpoint):
    store = corpus(config(tmp_path, endpoint[0]), create=True)
    reader = CorpusEvidenceReader(policy(store), store)
    for text in (" ", "x" * 101):
        with pytest.raises(ValueError, match="declared bounds"):
            asyncio.run(reader.read(text))
    store.close()
    with pytest.raises(ValueError):
        asyncio.run(reader.read("ports"))
    assert not endpoint[1]


def test_no_matching_language_returns_an_observed_empty_context(tmp_path, endpoint):
    async def operation():
        store = corpus(config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究")))
            evidence = await CorpusEvidenceReader(policy(store, languages=("ja",)), store).read(
                "ports"
            )
            assert not evidence.sources and not evidence.hits and not evidence.omissions
            assert evidence.query.encoding_call.outcome == "success"
            assert len(endpoint[1]) == 2
            assert CorpusEvidenceBundle.model_validate_json(evidence.model_dump_json()) == evidence
        finally:
            store.close()

    asyncio.run(operation())


def test_every_omitted_passage_is_retained_with_its_reason(tmp_path, endpoint):
    async def operation():
        store = corpus(config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究"), ("en", "ports study")))
            reader = CorpusEvidenceReader(policy(store, max_documents=1), store)
            evidence = await reader.read("ports")
            assert len(evidence.sources) == len(evidence.hits) == 1
            assert len(evidence.omissions) == 1
            assert evidence.omissions[0].reason == "document_limit"
            small = await CorpusEvidenceReader(policy(store, max_original_bytes=1), store).read(
                "ports"
            )
            assert not small.sources and not small.hits
            assert len(small.omissions) == len(small.query.hits) == 2
            assert all(item.reason == "original_bytes" for item in small.omissions)
        finally:
            store.close()

    asyncio.run(operation())


def test_retained_context_cannot_change_source_query_selection_or_claim_freshness(
    tmp_path, endpoint
):
    async def operation():
        store = corpus(config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究")))
            evidence = await CorpusEvidenceReader(policy(store), store).read("ports")
            for change in ("source", "query", "selection", "omission", "freshness", "review"):
                forged = json.loads(evidence.model_dump_json())
                if change == "source":
                    forged["sources"][0]["extracted"]["text"] = "unsupported replacement"
                elif change == "query":
                    forged["query_text"] = "different intent"
                elif change == "selection":
                    forged["selected_passages"] = []
                elif change == "omission":
                    forged["omissions"] = [
                        {"passage_id": evidence.selected_passages[0], "reason": "response_bytes"}
                    ]
                elif change == "freshness":
                    forged["source_age"] = "fresh"
                else:
                    forged["current_intent_verified"] = True
                with pytest.raises(ValidationError):
                    CorpusEvidenceBundle.model_validate(forged)
            oversized = json.loads(evidence.model_dump_json())
            oversized["policy"]["max_response_bytes"] = 1
            with pytest.raises(ValidationError, match="response bound"):
                CorpusEvidenceBundle.model_validate(oversized)
        finally:
            store.close()

    asyncio.run(operation())


def test_oversized_query_record_is_not_silently_truncated(tmp_path, endpoint):
    async def operation():
        store = corpus(config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究")))
            with pytest.raises(ValueError, match="response bound"):
                await CorpusEvidenceReader(policy(store, max_response_bytes=1), store).read("ports")
            assert len(endpoint[1]) == 2  # Failed delivery does not erase the actual query call.
        finally:
            store.close()

    asyncio.run(operation())


def test_reviewed_pdf_originals_and_page_model_provenance_are_not_reprocessed(tmp_path, endpoint):
    async def operation():
        material = await reviewed_harvest(tmp_path)
        store = corpus(config(tmp_path, endpoint[0], max_document_bytes=2_000_000), create=True)
        try:
            await store.append(material)
            evidence = await CorpusEvidenceReader(
                policy(store, max_original_bytes=2_000_000, max_response_bytes=4_000_000), store
            ).read("unrelated")
            assert evidence.sources == material.documents
            assert evidence.hits[0].passage.kind == "reviewed_pdf_transcription"
            assert evidence.hits[0].passage.page_indices == (0,)
            retained = Document.model_validate_json(evidence.sources[0].model_dump_json())
            assert (
                retained.extracted.pdf_transcription
                == material.documents[0].extracted.pdf_transcription
            )
            assert retained.local_input == material.documents[0].local_input
            assert len(endpoint[1]) == 2
        finally:
            store.close()

    asyncio.run(operation())


def test_non_active_example_parses_without_opening_a_corpus_or_service():
    example = CorpusEvidenceConfig.model_validate(
        tomllib.loads(Path("examples/corpus-evidence.toml").read_text())
    )
    assert example.source_mode == "retained_snapshot"
    assert CorpusEvidenceConfig.model_validate_json(example.model_dump_json()) == example


def test_retained_image_ocr_and_reviewed_claim_keep_original_pixels_and_regions(tmp_path, endpoint):
    async def operation():
        visual = VisualConfig.model_validate(
            recipe(tmp_path).model_dump()
            | {
                "vision": service(9999, max_request_bytes=1000000, max_input_chars=100000),
                "reviewer": service(9999, max_request_bytes=1000000, max_input_chars=100000),
            }
        )
        cfg, route, judge = run_config(visual), VisualRoute(), FakeJudge()
        reader = LocalVisionReader(
            visual,
            vision_http=VisionWire(visual.vision),
            review_http=VisionWire(visual.reviewer),
        )
        loop = GoalLoop(
            config=cfg,
            fetcher=FetchLadder((route,)),
            extractor=FakeExtractor(),
            scorer=KeywordScorer(),
            judge=judge,
            visual_stage=VisualStage(visual, ocr=TesseractOcr(visual), judge=judge, vision=reader),
        )
        material = await loop.run(
            Goal(text="ports", seeds=("https://example.org/start",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
        store = corpus(config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(material)
            before = len(route.requests), len(endpoint[1])
            evidence = await CorpusEvidenceReader(policy(store), store).read("unrelated")
            assert evidence.sources == material.documents
            assert {hit.passage.kind for hit in evidence.hits} >= {"image_ocr", "visual_claim"}
            assert len(route.requests) == before[0]
            assert len(endpoint[1]) == before[1] + 1
            assert evidence.sources[0].images[0].raw == route.raw
            for hit in evidence.hits:
                hit.passage.validate_source(evidence.sources[0])
            forged = json.loads(evidence.model_dump_json())
            visual_hit = next(
                hit for hit in forged["query"]["hits"] if hit["passage"]["kind"] == "image_ocr"
            )
            visual_hit["passage"]["regions"] = []
            with pytest.raises(ValidationError, match="image regions"):
                CorpusEvidenceBundle.model_validate(forged)
        finally:
            store.close()

    asyncio.run(operation())


def test_partial_response_bound_reports_omissions_instead_of_shortening_originals(
    tmp_path, endpoint
):
    async def operation():
        store = corpus(config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(await harvest(("zh", "港口研究"), ("en", "ports research")))
            full = await CorpusEvidenceReader(policy(store), store).read("ports")
            original_size = len(full.sources[0].model_dump_json().encode())
            cap = len(full.model_dump_json().encode()) - original_size // 2
            partial = await CorpusEvidenceReader(policy(store, max_response_bytes=cap), store).read(
                "ports"
            )
            assert len(partial.sources) == len(partial.hits) == 1
            assert len(partial.query.hits) == 2
            assert len(partial.omissions) == 1 and partial.omissions[0].reason == "response_bytes"
            assert len(partial.model_dump_json().encode()) <= cap
            assert partial.sources[0] in full.sources
            assert CorpusEvidenceBundle.model_validate_json(partial.model_dump_json()) == partial
        finally:
            store.close()

    asyncio.run(operation())
