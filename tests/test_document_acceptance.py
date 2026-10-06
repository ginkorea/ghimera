"""Real local bytes, explicit expected content, and no inferred publisher proof."""

import asyncio
import hashlib

import pytest

from ghimera.document_acceptance import PdfExpectations, accept_pdf
from ghimera.refusals import GhimeraRefused
from tests.test_document_extraction import config, native_pdf, page


def expectations(**updates):
    return PdfExpectations.model_validate(
        dict(
            {
                "schema": "chimera.pdf-expectations/1",
                "required_text": ("terminal construction",),
                "ordered_text": ("Port infrastructure report", "Analysts reviewed"),
                "page_count": 1,
                "minimum_table_count": 0,
            },
            **updates,
        )
    )


def test_actual_pdf_acceptance_binds_bytes_expectations_and_real_parse(tmp_path):
    source = page(native_pdf(), "application/pdf")
    plan = expectations()
    result, report = asyncio.run(accept_pdf(config(tmp_path), source, plan))
    assert report.source_sha256 == hashlib.sha256(source.body).hexdigest()
    assert report.expectations_sha256 == hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
    assert report.parse == result.document_parse
    assert report.source_url_claim == source.final_url
    assert report.fetch_verified is False


@pytest.mark.parametrize(
    "updates",
    [
        dict(required_text=("invented quotation",)),
        dict(ordered_text=("Analysts reviewed", "Port infrastructure report")),
        dict(page_count=2),
        dict(minimum_table_count=1),
    ],
)
def test_acceptance_refuses_actual_mismatches_instead_of_scoring_them_passed(tmp_path, updates):
    source = page(native_pdf(), "application/pdf")
    with pytest.raises(GhimeraRefused):
        asyncio.run(accept_pdf(config(tmp_path), source, expectations(**updates)))
