"""Reproducible offline PDF acceptance against explicit input expectations.

This checks provided PDF bytes, not a publisher's identity or model accuracy
over an unstated corpus. Source fetching and model provisioning remain separate.
"""

import argparse
import asyncio
import hashlib
import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from chimera.config import ChimeraConfig
from chimera.document_types import Digest, DocumentParseEvidence
from chimera.documents import DocumentExtractor
from chimera.models import Extracted, Page, Record
from chimera.passive_worker import private_directory
from chimera.refusals import ChimeraRefused, RefusalCode


class PdfExpectations(Record):
    schema_version: Literal["chimera.pdf-expectations/1"] = Field(alias="schema")
    required_text: tuple[Annotated[str, Field(min_length=1)], ...]
    ordered_text: tuple[Annotated[str, Field(min_length=1)], ...]
    page_count: Annotated[int, Field(strict=True, gt=0)]
    minimum_table_count: Annotated[int, Field(strict=True, ge=0)]


class PdfAcceptanceReport(Record):
    schema_version: Literal["chimera.pdf-acceptance/1"] = Field(alias="schema")
    source_sha256: Digest
    source_url_claim: str
    # A URL attached to supplied local bytes is not independently verified here.
    fetch_verified: Literal[False] = False
    expectations_sha256: Digest
    seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    parse: DocumentParseEvidence


async def accept_pdf(
    config: ChimeraConfig,
    source: Page,
    expectations: PdfExpectations,
) -> tuple[Extracted, PdfAcceptanceReport]:
    """Use the real extractor port and retained structure; no fake converter."""
    started = time.monotonic()
    result = await DocumentExtractor(config).extract(source)
    evidence = result.document_parse
    if evidence is None or evidence.page_count != expectations.page_count:
        raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
    if evidence.table_count < expectations.minimum_table_count or any(
        text not in result.text for text in expectations.required_text
    ):
        raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
    offset = 0
    for text in expectations.ordered_text:
        position = result.text.find(text, offset)
        if position < 0:
            raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
        offset = position + len(text)
    report = PdfAcceptanceReport(
        schema="chimera.pdf-acceptance/1",
        source_sha256=hashlib.sha256(source.body).hexdigest(),
        source_url_claim=source.final_url,
        expectations_sha256=hashlib.sha256(expectations.model_dump_json().encode()).hexdigest(),
        seconds=time.monotonic() - started,
        parse=evidence,
    )
    return result, report


def _cmd_accept_pdf() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, required=True, help="Full collector TOML or JSON config"
    )
    parser.add_argument(
        "--pdf", type=Path, required=True, help="Provided local PDF, read boundedly"
    )
    parser.add_argument(
        "--source-url", required=True, help="Caller-supplied origin claim, not fetched"
    )
    parser.add_argument(
        "--expectations", type=Path, required=True, help="Versioned expectation JSON"
    )
    parser.add_argument(
        "--output-directory", type=Path, required=True, help="Private receipt directory"
    )
    args = parser.parse_args()
    config = (
        ChimeraConfig.model_validate_json(args.config.read_bytes())
        if args.config.suffix == ".json"
        else ChimeraConfig.from_toml(args.config)
    )
    if config.document_extraction is None:
        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    with args.pdf.open("rb") as stream:
        body = stream.read(config.document_extraction.max_input_bytes + 1)
    source = Page(
        url=args.source_url,
        final_url=args.source_url,
        status=200,
        content_type="application/pdf",
        body=body,
    )
    expectations = PdfExpectations.model_validate_json(args.expectations.read_bytes())
    private_directory(args.output_directory)
    # Avoid overwriting the record of a different acceptance run.
    if any(args.output_directory.iterdir()):
        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    result, report = asyncio.run(accept_pdf(config, source, expectations))
    with (args.output_directory / "source.pdf").open("xb") as stream:
        stream.write(body)
    for name, content in (
        ("extracted.json", result.model_dump_json()),
        ("report.json", report.model_dump_json()),
        ("expectations.json", expectations.model_dump_json()),
        ("effective-config.json", config.model_dump_json()),
    ):
        with (args.output_directory / name).open("x", encoding="utf-8") as output:
            output.write(content)
    # No source prose, secrets or arbitrary vendor diagnostics on stdout.
    print(report.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(_cmd_accept_pdf())
