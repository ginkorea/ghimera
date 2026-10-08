"""Bounded real offline PDF crops; fixture admission and judge are not quality acceptance."""

import asyncio
import hashlib
import io
import json
import math

import pytest
from PIL import Image

from ghimera.document_types import DocumentLayout, DocumentParseEvidence
from ghimera.doubles import FakeJudge
from ghimera.image_ocr import TesseractOcr
from ghimera.models import Document, Extracted, Goal, Verdict
from ghimera.page_renderer import PdfPageRenderer
from ghimera.pdf_figure_config import PdfFigureConfig
from ghimera.pdf_figures import PdfFigureCropper, select_figures, validate_pdf_images
from ghimera.research_types import Citation
from ghimera.visual_stage import VisualStage
from tests.test_document_extraction import native_pdf
from tests.test_page_transcription import render_config, run_context
from tests.test_visuals import recipe


def figures_policy(tmp_path, **updates):
    fields = dict(
        schema="ghimera.pdf-figures/1",
        renderer=render_config(tmp_path),
        max_candidates=10,
        max_figures=2,
        max_layout_bytes=100000,
        max_crop_pixels=100000,
        max_crop_bytes=100000,
        candidate_terms=("port", "chart"),
        excluded_terms=("logo",),
    )
    fields.update(updates)
    return PdfFigureConfig.model_validate(fields)


def source_document(*, origin="TOPLEFT", caption="Port infrastructure chart", pictures=1):
    """Controlled layout region over an actual PDF; no learned figure detector is claimed."""
    raw, text = native_pdf(), "Port infrastructure report"
    bbox = {
        "l": 20,
        "r": 590,
        "t": 20 if origin == "TOPLEFT" else 772,
        "b": 120 if origin == "TOPLEFT" else 672,
        "coord_origin": origin,
    }
    tree = {
        "schema_name": "DoclingDocument",
        "version": "1.9.0",
        "name": "controlled",
        "texts": [{"text": caption}],
        "pages": {"1": {"size": {"width": 612, "height": 792}}},
        "pictures": [{"captions": [{"$ref": "#/texts/0"}], "prov": [{"page_no": 1, "bbox": bbox}]}]
        * pictures,
    }
    vendor = json.dumps(tree)
    layout = DocumentLayout(
        schema="chimera.document-layout/1",
        docling_json=vendor,
        sha256=hashlib.sha256(vendor.encode()).hexdigest(),
    )
    parse = DocumentParseEvidence(
        schema="chimera.document-parse/1",
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_url="https://example.org/report.pdf",
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        layout_sha256=layout.sha256,
        config_digest="a" * 64,
        parser_revision="controlled-layout@1",
        pipeline="native",
        title_source="first_text",
        page_count=1,
        table_count=0,
        artifact_manifest_digest=None,
        language_confidence=1,
        language_margin=1,
        language_sample_chars=len(text),
        omitted_links=0,
    )
    return Document(
        url="https://example.org/report.pdf",
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        extracted=Extracted(
            title="Ports", text=text, language="en", document_layout=layout, document_parse=parse
        ),
        verdict=Verdict(
            decision="accept",
            kind="fixture",
            publisher="fixture",
            language="en",
            reason="controlled admission",
        ),
    )


def test_source_caption_admission_bottom_left_conversion_and_bounded_omissions(tmp_path):
    config = figures_policy(tmp_path)
    top = select_figures(source_document(), config)
    bottom = select_figures(source_document(origin="BOTTOMLEFT"), config)
    assert top.figures[0].region == bottom.figures[0].region
    assert top.figures[0].page_index == 0
    assert not select_figures(source_document(caption="Company logo port"), config).figures
    assert not select_figures(source_document(caption="Unknown figure"), config).figures
    overflow = select_figures(source_document(pictures=11), config)
    assert overflow.figures == () and overflow.omissions == ("pdf_figure_candidate_bound",)
    capped = select_figures(source_document(pictures=3), config)
    assert len(capped.figures) == 2 and "figure:2:pdf_figure_admission_bound" in capped.omissions
    with pytest.raises(ValueError, match="coordinate origin"):
        select_figures(source_document(origin="UNKNOWN"), config)


def test_real_owned_pdf_crop_matches_exact_renderer_region_and_cleans_worker(tmp_path):
    config, document = figures_policy(tmp_path), source_document()
    selections = select_figures(document, config)

    async def scenario():
        crops = await PdfFigureCropper(config).crop(document, selections)
        rendered = await PdfPageRenderer(config.renderer).render(document.raw)
        return crops, rendered

    crops, rendered = asyncio.run(scenario())
    crop, page, region = crops[0], rendered.pages[0], selections.figures[0].region
    bounds = (
        math.floor(region.left * page.width),
        math.floor(region.top * page.height),
        math.ceil(region.right * page.width),
        math.ceil(region.bottom * page.height),
    )
    with Image.open(io.BytesIO(page.png)) as original, Image.open(io.BytesIO(crop.png)) as actual:
        expected = original.crop(bounds)
        try:
            assert actual.size == expected.size and actual.tobytes() == expected.tobytes()
        finally:
            expected.close()
    anchor = crop.candidate.pdf_crop
    assert anchor.source_sha256 == document.sha256 and anchor.page_image_sha256 == page.image_sha256
    assert (
        anchor.region == region
        and anchor.layout_sha256 == document.extracted.document_layout.sha256
    )
    assert not list(config.renderer.work_directory.iterdir())


def test_real_crop_ocr_uses_existing_visual_stage_and_citation_basis(tmp_path):
    figure_config, document = figures_policy(tmp_path), source_document()
    visual = recipe(tmp_path)
    visual = visual.model_validate(
        visual.model_dump() | {"pdf_figures": figure_config, "min_height": 80}
    )
    stage = VisualStage(visual, ocr=TesseractOcr(visual), judge=FakeJudge())
    budget, ledger = run_context()
    images = asyncio.run(
        stage.collect_pdf(
            goal=Goal(text="ports"),
            document=document,
            budget=budget,
            ledger=ledger,
            language_hint="en",
        )
    )
    assert len(images) == 1 and "Port" in images[0].ocr.text
    retained = document.model_copy(update={"images": images})
    validate_pdf_images(retained, visual)
    citation = Citation.from_image(retained, 0, 0)
    assert citation.basis == "image_ocr" and citation.page_indices == (0,)
    assert citation.matches(retained) and citation.visual_anchor.regions
    assert images[0].candidate.pdf_crop.region != citation.visual_anchor.regions[0]
    assert not list(figure_config.renderer.work_directory.iterdir())
    assert not list(visual.work_directory.iterdir())
    changed_anchor = images[0].candidate.pdf_crop.model_copy(update={"layout_sha256": "f" * 64})
    changed = images[0].model_copy(
        update={"candidate": images[0].candidate.model_copy(update={"pdf_crop": changed_anchor})}
    )
    with pytest.raises(ValueError, match="PDF crop must bind"):
        validate_pdf_images(document.model_copy(update={"images": (changed,)}), visual)


def test_missing_native_layout_is_explicit_gap_and_no_render(tmp_path):
    document = source_document().model_copy(
        update={"extracted": Extracted(title="Ports", text="native text", language="en")}
    )
    config = figures_policy(tmp_path)
    selections = select_figures(document, config)
    assert selections.omissions == ("no_native_pdf_figure_layout",)
    assert asyncio.run(PdfFigureCropper(config).crop(document, selections)) == ()
    assert not config.renderer.work_directory.exists()


def test_zero_native_figures_is_a_coverage_gap(tmp_path):
    document = source_document(pictures=0)
    selected = select_figures(document, figures_policy(tmp_path))
    assert selected.figures == ()
    assert selected.omissions == ("no_native_pdf_figure_observations",)
