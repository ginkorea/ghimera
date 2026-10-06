"""Explicit binary PDF downloads retain MIME and use the real offline parser."""

import asyncio
import hashlib
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.document_config import DocumentExtractionConfig
from ghimera.document_media import DocumentMediaConfig
from ghimera.documents import DocumentExtractor
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Extracted, Goal, Harvest, Scope
from ghimera.refusals import GhimeraRefused
from tests.test_document_extraction import config, docx, native_pdf, page


def enabled(tmp_path):
    return config(
        tmp_path,
        schema="chimera.document-extraction/2",
        media={
            "schema": "ghimera.document-media/1",
            "pdf_download_types": ["application/octet-stream"],
        },
    )


def test_download_admission_has_one_explicit_versioned_configuration(tmp_path):
    raw = {"schema": "ghimera.document-media/1", "pdf_download_types": ["text/html"]}
    with pytest.raises(ValidationError):
        DocumentMediaConfig.model_validate(raw)
    with pytest.raises(ValidationError):
        config(tmp_path, media=enabled(tmp_path).document_extraction.media)
    with pytest.raises(ValidationError):
        config(tmp_path, schema="chimera.document-extraction/2")
    assert "media" not in config(tmp_path).document_extraction.model_dump(by_alias=True)
    with Path("examples/documents-downloads.toml").open("rb") as stream:
        policy = DocumentExtractionConfig.model_validate(tomllib.load(stream))
    assert policy.supported_content_types == {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    }


def test_real_generic_pdf_keeps_original_mime_and_ignores_filename(tmp_path):
    source = page(native_pdf(), "application/octet-stream")
    # The existing source helper intentionally gives a .docx URL to this PDF.
    policy = enabled(tmp_path)
    result = asyncio.run(DocumentExtractor(policy).extract(source))
    assert source.content_type == "application/octet-stream"
    assert "terminal construction" in result.text
    parsed = result.document_parse
    assert parsed.schema_version == "chimera.document-parse/2"
    assert parsed.pipeline == "native" and parsed.page_count == 1
    assert parsed.media.declared_content_type == source.content_type
    assert parsed.media.resolved_content_type == "application/pdf"
    assert parsed.media.method == "pdf_header_at_start"
    assert parsed.media.source_sha256 == hashlib.sha256(source.body).hexdigest()
    assert parsed.media.config_digest == policy.document_extraction.media.content_digest()
    assert Extracted.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "mime,body",
    [
        ("application/octet-stream", b"<html>not a PDF</html>"),
        ("application/octet-stream", b"MZ-executable"),
        ("application/octet-stream", b"padding%PDF-1.4"),
        ("application/octet-stream", docx()),
        ("binary/octet-stream", native_pdf()),
        ("text/html", native_pdf()),
    ],
)
def test_unapproved_or_non_pdf_downloads_refuse_before_worker(tmp_path, mime, body):
    with pytest.raises(GhimeraRefused, match="content_type_unwanted"):
        asyncio.run(DocumentExtractor(enabled(tmp_path)).extract(page(body, mime)))
    assert not (tmp_path / "worker").exists()


def test_default_policy_does_not_expand_to_binary_downloads(tmp_path):
    with pytest.raises(GhimeraRefused, match="content_type_unwanted"):
        asyncio.run(
            DocumentExtractor(config(tmp_path)).extract(
                page(native_pdf(), "application/octet-stream")
            )
        )


def test_declared_document_media_also_binds_the_new_policy(tmp_path):
    policy = enabled(tmp_path)
    result = asyncio.run(DocumentExtractor(policy).extract(page(native_pdf(), "application/pdf")))
    assert result.document_parse.media.method == "declared"
    assert result.document_parse.media.declared_content_type == "application/pdf"
    assert result.document_parse.media.resolved_content_type == "application/pdf"


def test_collection_and_reader_preserve_media_binding_and_reject_forgery(tmp_path):
    source = page(native_pdf(), "application/octet-stream")

    class DownloadRoute(FakeRoute):
        async def attempt(self, request):
            return source

    cfg = enabled(tmp_path)
    result = asyncio.run(
        GoalLoop(
            config=cfg,
            fetcher=FetchLadder((DownloadRoute(),)),
            extractor=DocumentExtractor(cfg),
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ).run(
            Goal(text="port infrastructure", seeds=(source.url,)),
            Scope(
                allowed_hosts=("example.org",), max_depth=0, content_types=(source.content_type,)
            ),
        )
    )
    assert len(result.documents) == 1 and result.documents[0].raw == source.body
    assert Harvest.model_validate_json(result.model_dump_json()) == result
    parsed = result.documents[0].extracted.document_parse
    assert next(row for row in result.ledger if row.event == "extraction").document_parse == parsed
    for change in (
        {"declared_content_type": "text/html"},
        {"method": "declared"},
        {"source_sha256": "0" * 64},
        {"config_digest": "0" * 64},
    ):
        raw = result.model_dump(by_alias=True)
        raw["documents"][0]["extracted"]["document_parse"]["media"].update(change)
        with pytest.raises(ValidationError):
            Harvest.model_validate(raw)
