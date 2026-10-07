"""Collector integration over a real PDF; scripted model replies are not OCR accuracy."""

import asyncio
import hashlib
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.documents import DocumentExtractor
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.evidence_context import native_citation
from ghimera.fetch import FetchLadder
from ghimera.journal import read_journal
from ghimera.loop import GoalLoop
from ghimera.models import Document, Goal, Harvest, Page, Verdict
from ghimera.page_renderer import PdfPageRenderer
from ghimera.page_transcriber import LocalPageTranscriber
from ghimera.page_transcription_config import PdfTranscriptionConfig
from ghimera.pdf_transcription import PdfTranscriptionStage
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research import citation_for
from tests.test_document_extraction import config as document_config
from tests.test_document_extraction import native_pdf
from tests.test_local_inputs import recipe as input_recipe
from tests.test_local_inputs import seed
from tests.test_page_transcription import ModelPort, policy, run_context


def configured(tmp_path, mode="always"):
    cfg = PdfTranscriptionConfig(
        schema="ghimera.pdf-transcription/1",
        pages=policy(tmp_path),
        language_hint="zh-Hant",
        mode=mode,
        max_document_text_chars=20_000,
    )
    raw = document_config(tmp_path).model_dump()
    raw.update(pdf_transcription=cfg, local_inputs=input_recipe(tmp_path))
    return GhimeraConfig.model_validate(raw)


def stage(cfg, mutation=None):
    pages = cfg.pdf_transcription.pages
    first, second = (
        ModelPort(pages.transcriber),
        ModelPort(pages.reviewer, review=True, mutation=mutation),
    )
    return (
        PdfTranscriptionStage(
            cfg.pdf_transcription,
            renderer=PdfPageRenderer(pages.renderer),
            transcriber=LocalPageTranscriber(pages, transcription_http=first, review_http=second),
        ),
        first,
        second,
    )


def source():
    return Page(
        url="https://example.org/report.pdf",
        final_url="https://example.org/report.pdf",
        status=200,
        content_type="application/pdf",
        body=native_pdf(),
    )


def test_original_native_reading_is_not_overwritten_and_generated_citation_is_labelled(tmp_path):
    cfg = configured(tmp_path)
    adapter, first, second = stage(cfg)
    budget, ledger = run_context()
    page = source()
    result = asyncio.run(
        adapter.extract(page, native=DocumentExtractor(cfg), budget=budget, ledger=ledger)
    )
    assert result.text == "臺灣港務公司"  # Scripted reply: no model-quality claim.
    proof = result.pdf_transcription
    assert "terminal construction" in proof.native_reading.text
    assert proof.native_reading.document_parse.pipeline == "native"
    assert result.document_parse is None and result.document_layout is None
    assert proof.native_refusal is None and len(first.requests) == len(second.requests) == 1
    document = Document(
        url=page.final_url,
        sha256=hashlib.sha256(page.body).hexdigest(),
        raw=page.body,
        extracted=result,
        verdict=Verdict(
            decision="accept",
            kind="report",
            publisher="fixture",
            language="zh-Hant",
            reason="protocol",
        ),
    )
    document.validate_policy(cfg)
    assert Document.model_validate_json(document.model_dump_json()) == document
    for make_citation in (citation_for, native_citation):
        citation = make_citation(document, 0, len(result.text))
        assert citation.basis == "reviewed_pdf_transcription" and citation.page_indices == (0,)
        assert citation.matches(document)
        assert not citation.model_copy(update={"basis": "native"}).matches(document)
    broken = document.model_dump()
    broken["raw"] = b"%PDF-changed"
    broken["sha256"] = hashlib.sha256(broken["raw"]).hexdigest()
    with pytest.raises(ValidationError, match="original source"):
        Document.model_validate(broken)


def test_native_refused_mode_does_not_contact_models_for_readable_pdf(tmp_path):
    cfg = configured(tmp_path, "native_refused")
    adapter, first, second = stage(cfg)
    budget, ledger = run_context()
    result = asyncio.run(
        adapter.extract(source(), native=DocumentExtractor(cfg), budget=budget, ledger=ledger)
    )
    assert result.pdf_transcription is None and "terminal construction" in result.text
    assert not first.requests and not second.requests and budget.judge_calls == 0


@pytest.mark.parametrize("code", [RefusalCode.EXTRACTION_FAILED, RefusalCode.ADAPTER_CONTRACT])
def test_only_declared_native_extraction_failure_can_trigger_transcription(tmp_path, code):
    cfg = configured(tmp_path, "native_refused")
    adapter, first, second = stage(cfg)
    budget, ledger = run_context()

    class RefusingNative:
        revision = "fixture"

        def validate_config(self, config):
            pass

        async def extract(self, page):
            raise GhimeraRefused(code)

    async def run():
        return await adapter.extract(
            source(), native=RefusingNative(), budget=budget, ledger=ledger
        )

    if code == RefusalCode.EXTRACTION_FAILED:
        result = asyncio.run(run())
        assert result.pdf_transcription.native_refusal == code
        assert result.pdf_transcription.native_reading is None
        assert len(first.requests) == len(second.requests) == 1
    else:
        with pytest.raises(GhimeraRefused) as error:
            asyncio.run(run())
        assert error.value.code == code
        assert not first.requests and not second.requests


def test_uncertain_review_never_promotes_a_partial_document(tmp_path):
    cfg = configured(tmp_path)
    adapter, _, _ = stage(cfg, "uncertain_review")
    budget, ledger = run_context()
    with pytest.raises(GhimeraRefused) as error:
        asyncio.run(
            adapter.extract(source(), native=DocumentExtractor(cfg), budget=budget, ledger=ledger)
        )
    assert error.value.code == RefusalCode.EXTRACTION_FAILED and budget.judge_calls == 2
    assert sum(row.transcription_call is not None for row in ledger.snapshot()) == 2


def test_collector_loop_journal_and_local_original_preserve_exact_model_observations(tmp_path):
    cfg = configured(tmp_path)
    raw = cfg.model_dump()
    raw["journal"] = dict(
        schema="chimera.run-journal-config/1",
        directory=str(tmp_path / "journal"),
        max_record_bytes=2_000_000,
        max_journal_bytes=10_000_000,
        max_summary_bytes=2_000_000,
        max_records=1000,
    )
    cfg = GhimeraConfig.model_validate(raw)
    adapter, _, _ = stage(cfg)
    route = FakeRoute()
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route,)),
        extractor=DocumentExtractor(cfg),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        pdf_transcription=adapter,
    )
    pdf = native_pdf()
    path = tmp_path / "original.pdf"
    path.write_bytes(pdf)

    async def run():
        session = await loop.open(Goal(text="ports"), run_id="transcription-protocol")
        await loop.import_local(session, (seed(path, pdf, content_type="application/pdf"),))
        return loop.finish(session, "frontier_empty")

    result = asyncio.run(run())
    assert len(result.documents) == 1 and result.documents[0].raw == pdf
    assert not route.requests and result.receipt.fetches == 0
    assert result.receipt.judge_calls == 3
    assert Harvest.model_validate_json(result.model_dump_json()) == result
    assert read_journal(cfg.journal, "transcription-protocol").rows == result.ledger
    # Removing a call cannot turn a generated reading into acknowledged evidence.
    broken = result.model_dump()
    for row in broken["ledger"]:
        if row["event"] == "transcription":
            row["transcription_call"]["response_sha256"] = "0" * 64
            break
    with pytest.raises(ValidationError, match="model-call observation"):
        Harvest.model_validate(broken)


def test_non_active_example_and_parent_configuration_require_original_document_adapter():
    config = PdfTranscriptionConfig.model_validate(
        tomllib.loads(Path("examples/pdf-transcription.toml").read_text())
    )
    assert config.mode == "native_refused" and config.language_hint == "zh-Hans"
    parent = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump()
    parent["pdf_transcription"] = config
    with pytest.raises(ValidationError, match="original binary-document adapter"):
        GhimeraConfig.model_validate(parent)
