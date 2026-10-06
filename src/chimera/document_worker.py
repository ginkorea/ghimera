"""Real offline Docling parsing after source/network separation and archive checks."""

import contextlib
import hashlib
import io
import re
import sys
import zipfile
from importlib.metadata import version
from pathlib import PurePosixPath
from typing import Literal

from chimera.document_types import DocumentLayout, DocumentParseEvidence
from chimera.documents import DOCX_TYPE, DocumentExtractor, DocumentRequest, check_artifacts
from chimera.extraction import ExtractionResponse
from chimera.html_worker import no_network, public_link
from chimera.models import Extracted, LinkCandidate
from chimera.reference_types import DocumentReference, ReferenceSpan
from chimera.refusals import ChimeraRefused, RefusalCode


def check_docx(request: DocumentRequest) -> None:
    config = request.config
    with zipfile.ZipFile(io.BytesIO(request.page.body)) as archive:
        items = archive.infolist()
        if len(items) > config.max_archive_entries:
            raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
        total = 0
        names: set[str] = set()
        for item in items:
            path = PurePosixPath(item.filename)
            total += item.file_size
            if (
                item.filename in names
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in item.filename
                or item.flag_bits & 1
                or (item.external_attr >> 16) & 0o170000 == 0o120000
                or total > config.max_expanded_bytes
                or item.file_size > max(1, item.compress_size) * config.max_compression_ratio
            ):
                raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
            names.add(item.filename)
            if item.filename.endswith((".xml", ".rels")):
                data = archive.read(item)
                if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                    raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
        if not {"[Content_Types].xml", "word/document.xml"} <= names:
            raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)


def parse(request: DocumentRequest) -> Extracted:
    from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import NativePdfPipelineOptions
    from docling.document_converter import (
        DocumentConverter,
        FormatOption,
        NativePdfFormatOption,
        PdfFormatOption,
    )
    from docling_core.types.doc.labels import DocItemLabel
    from lingua import IsoCode639_1, LanguageDetectorBuilder

    actual = tuple(
        version(name) for name in ("docling-slim", "docling-core", "lingua-language-detector")
    )
    if actual != ("2.134.0", "2.99.0", "2.1.1"):
        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    config, page = request.config, request.page
    if not page.body or len(page.body) > config.max_input_bytes:
        raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
    mime = page.content_type.split(";", 1)[0].strip().lower()
    options: dict[InputFormat, FormatOption] = {}
    pipeline: Literal["docx", "native", "standard"]
    if mime == DOCX_TYPE:
        check_docx(request)
        pipeline = "docx"
        fmt, suffix = InputFormat.DOCX, ".docx"
    elif mime == "application/pdf":
        if not page.body.startswith(b"%PDF-"):
            raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
        pipeline, fmt, suffix = config.pdf_pipeline, InputFormat.PDF, ".pdf"
        if pipeline == "native":
            options[fmt] = NativePdfFormatOption(
                pipeline_options=NativePdfPipelineOptions(
                    enable_remote_services=False,
                    allow_external_plugins=False,
                    document_timeout=config.timeout_seconds,
                    generate_page_images=False,
                    generate_picture_images=False,
                    parser_threads=config.cpu_threads,
                )
            )
        else:
            from chimera.document_pipeline import ConfiguredPdfPipeline, pdf_options

            check_artifacts(config)
            options[fmt] = PdfFormatOption(
                pipeline_cls=ConfiguredPdfPipeline, pipeline_options=pdf_options(config)
            )
    else:
        raise ChimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
    source_hash = hashlib.sha256(page.body).hexdigest()
    converter = DocumentConverter(allowed_formats=[fmt], format_options=options)
    result = converter.convert(
        DocumentStream(name=source_hash + suffix, stream=io.BytesIO(page.body)),
        max_num_pages=config.max_pages,
        max_file_size=config.max_input_bytes,
    )
    if result.status != ConversionStatus.SUCCESS:
        raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
    document = result.document
    text = document.export_to_markdown().strip()
    if not text or len(text) > config.max_text_chars:
        raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
    heading = next(
        (
            item.text
            for item in document.texts
            if item.label in {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER} and item.text.strip()
        ),
        None,
    )
    first = next((item.text for item in document.texts if item.text.strip()), None)
    title = heading or first
    if title is None:
        raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
    sample = text[: config.language_max_chars]
    codes: list[IsoCode639_1] = []
    for value in config.languages:
        code: object = getattr(IsoCode639_1, value.upper(), None)
        if not isinstance(code, IsoCode639_1):
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        codes.append(code)
    detector = LanguageDetectorBuilder.from_iso_codes_639_1(*codes).with_low_accuracy_mode().build()
    confidence = detector.compute_language_confidence_values(sample)
    if not confidence:
        raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
    best = confidence[0]
    margin = best.value - confidence[1].value if len(confidence) > 1 else best.value
    language = (
        best.language.iso_code_639_1.name.lower()
        if (
            len(sample) >= config.language_min_chars
            and best.value >= config.language_min_confidence
            and margin >= config.language_min_margin
        )
        else "und"
    )
    layout_json = document.model_dump_json()
    layout = DocumentLayout(
        schema="chimera.document-layout/1",
        docling_json=layout_json,
        sha256=hashlib.sha256(layout_json.encode()).hexdigest(),
    )
    references: list[DocumentReference] = []
    for match in re.finditer(r"https?://[^\s<>\[\]`]+", text):
        observed = match.group().rstrip(".,;:)")
        target = public_link(page.final_url, observed)
        if target is not None:
            references.append(
                DocumentReference(
                    schema="chimera.document-reference/1",
                    target_url=target,
                    anchor="document reference",
                    kind="native_url",
                    base_url=page.final_url,
                    span=ReferenceSpan(
                        start=match.start(), end=match.start() + len(observed), quote=observed
                    ),
                )
            )
    for index, item in enumerate(document.texts):
        if item.hyperlink is None:
            continue
        observed = str(item.hyperlink)
        target = public_link(page.final_url, observed)
        if target is not None:
            references.append(
                DocumentReference(
                    schema="chimera.document-reference/1",
                    target_url=target,
                    anchor=item.text,
                    kind="docling_hyperlink",
                    layout_index=index,
                    observed_url=observed,
                    base_url=page.final_url,
                )
            )
    links: list[LinkCandidate] = []
    retained: list[DocumentReference] = []
    seen: set[str] = set()
    for reference in references:
        target = reference.target_url
        if target not in seen:
            seen.add(target)
            if len(links) < config.max_links:
                links.append(LinkCandidate(url=target, anchor=reference.anchor))
                retained.append(reference)
    evidence = DocumentParseEvidence(
        schema="chimera.document-parse/1",
        source_sha256=source_hash,
        source_url=page.final_url,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        layout_sha256=layout.sha256,
        config_digest=config.content_digest(),
        parser_revision=DocumentExtractor.revision,
        pipeline=pipeline,
        title_source="heading" if heading else "first_text",
        page_count=len(document.pages),
        table_count=len(document.tables),
        artifact_manifest_digest=config.artifact_digest() if pipeline == "standard" else None,
        language_confidence=best.value,
        language_margin=margin,
        language_sample_chars=len(sample),
        omitted_links=len(seen) - len(links),
    )
    return Extracted(
        title=title,
        text=text,
        language=language,
        links=tuple(links),
        references=tuple(retained),
        document_parse=evidence,
        document_layout=layout,
    )


def _cmd_extract() -> int:
    sys.addaudithook(no_network)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            request = DocumentRequest.model_validate_json(sys.stdin.buffer.read())
            result = parse(request)
        wire = ExtractionResponse(result=result)
    except ChimeraRefused as exc:
        wire = ExtractionResponse(refusal=exc.code)
    except Exception:
        wire = ExtractionResponse(refusal=RefusalCode.EXTRACTION_FAILED)
    sys.stdout.write(wire.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(_cmd_extract())
