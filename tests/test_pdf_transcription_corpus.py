"""Reviewed reading provenance survives actual corpus persistence and retrieval.

Native PDF rendering and SQLite/FAISS are real; transcription and embedding
replies are protocol fixtures. These tests do not measure model accuracy.
"""

import asyncio

import pytest
from pydantic import ValidationError

from ghimera.corpus import document_passages
from ghimera.corpus_types import CorpusPassage, CorpusQuery
from ghimera.documents import DocumentExtractor
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Goal
from tests.test_document_extraction import native_pdf
from tests.test_evidence_corpus import config as corpus_config
from tests.test_evidence_corpus import corpus, endpoint, harvest
from tests.test_local_inputs import seed
from tests.test_pdf_transcription_stage import configured, stage

__all__ = ["endpoint"]


async def reviewed_harvest(tmp_path):
    cfg = configured(tmp_path)
    adapter, _, _ = stage(cfg)
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        pdf_transcription=adapter,
    )
    raw = native_pdf()
    path = tmp_path / "original.pdf"
    path.write_bytes(raw)
    session = await loop.open(Goal(text="ports"))
    try:
        await loop.import_local(session, (seed(path, raw, content_type="application/pdf"),))
        return loop.finish(session, "frontier_empty")
    finally:
        session.ledger.close()


def test_reviewed_passages_keep_reading_basis_and_cannot_be_relabelled_native(tmp_path):
    material = asyncio.run(reviewed_harvest(tmp_path))
    source = material.documents[0]
    cfg = corpus_config(tmp_path, 9999)
    passages = document_passages(source, cfg)
    assert passages and all(p.kind == "reviewed_pdf_transcription" for p in passages)
    assert all(p.page_indices == (0,) for p in passages)
    for passage in passages:
        assert CorpusPassage.model_validate_json(passage.model_dump_json()) == passage
        passage.validate_source(source)
        for changes in (
            {"kind": "native", "page_indices": ()},
            {"page_indices": (1,)},
        ):
            forged = CorpusPassage.model_validate(passage.model_dump() | changes)
            with pytest.raises(ValueError, match="actual reading basis and source pages"):
                forged.validate_source(source)
        with pytest.raises(ValidationError):
            CorpusPassage.model_validate(passage.model_dump() | {"page_indices": (0, 0)})


def test_native_passage_wire_identity_has_no_additive_page_field(tmp_path):
    material = asyncio.run(harvest(("en", "native ports report")))
    source = material.documents[0]
    passage = document_passages(source, corpus_config(tmp_path, 9999))[0]
    assert passage.kind == "native" and passage.page_indices == ()
    assert "page_indices" not in passage.model_dump()
    original_wire = passage.model_dump()
    assert CorpusPassage.model_validate(original_wire).model_dump() == original_wire
    forged = passage.model_dump() | {"kind": "reviewed_pdf_transcription", "page_indices": (0,)}
    with pytest.raises(ValueError, match="actual reading basis and source pages"):
        CorpusPassage.model_validate(forged).validate_source(source)


def test_reviewed_basis_and_pages_survive_sqlite_reopen_and_vector_query(tmp_path, endpoint):
    selected = corpus_config(tmp_path, endpoint[0], max_document_bytes=2_000_000)

    async def operation():
        material = await reviewed_harvest(tmp_path)
        store = corpus(selected, create=True)
        try:
            receipt = await store.append(material)
            assert receipt.added_documents == receipt.added_passages == 1
        finally:
            store.close()
        reopened = corpus(selected, create=False)
        try:
            query = await reopened.search("unrelated", top_k=1)
            assert len(query.hits) == 1
            passage = query.hits[0].passage
            assert passage.kind == "reviewed_pdf_transcription" and passage.page_indices == (0,)
            source = reopened.document(passage.document_id)
            passage.validate_source(source)
            assert source.raw == material.documents[0].raw
            assert (
                source.extracted.pdf_transcription
                == material.documents[0].extracted.pdf_transcription
            )
            assert CorpusQuery.model_validate_json(query.model_dump_json()) == query
        finally:
            reopened.close()

    asyncio.run(operation())
