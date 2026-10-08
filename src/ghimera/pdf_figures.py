"""Source layout selects relevant PDF figure regions before offline raster work."""

import hashlib
import json
import math
import os
import struct
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ghimera.models import Document, DocumentSource
from ghimera.passive_worker import PassiveWorker
from ghimera.pdf_figure_config import PdfFigureConfig
from ghimera.visual_config import VisualConfig
from ghimera.visual_types import ImageCandidate, ImageRegion, PdfFigureAnchor, VisualRecord


class FigureSelection(VisualRecord):
    picture_index: Annotated[int, Field(strict=True, ge=0)]
    page_index: Annotated[int, Field(strict=True, ge=0)]
    region: ImageRegion
    caption: str
    layout_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FigureSelections(VisualRecord):
    figures: tuple[FigureSelection, ...]
    omissions: tuple[str, ...]


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("PDF layout object has an invalid shape")
    return {str(key): item for key, item in value.items()}


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError("PDF layout coordinate must be finite")
    return float(value)


def select_figures(document: DocumentSource, policy: PdfFigureConfig) -> FigureSelections:
    native = document.extracted
    if native.pdf_transcription is not None:
        native = native.pdf_transcription.native_reading or native
    layout = native.document_layout
    if not document.raw.startswith(b"%PDF-") or layout is None:
        return FigureSelections(figures=(), omissions=("no_native_pdf_figure_layout",))
    if len(layout.docling_json.encode()) > policy.max_layout_bytes:
        return FigureSelections(figures=(), omissions=("pdf_figure_layout_bound",))
    vendor = _object(json.loads(layout.docling_json))
    pictures, texts, pages = (
        vendor.get("pictures", []),
        vendor.get("texts", []),
        vendor.get("pages", {}),
    )
    if not isinstance(pictures, list) or not isinstance(texts, list):
        raise ValueError("PDF layout figure/text collections must be lists")
    if not pictures:
        return FigureSelections(figures=(), omissions=("no_native_pdf_figure_observations",))
    pages = _object(pages)
    accepted: list[FigureSelection] = []
    omissions: list[str] = []
    if len(pictures) > policy.max_candidates:
        return FigureSelections(figures=(), omissions=("pdf_figure_candidate_bound",))
    for index, raw in enumerate(pictures):
        picture = _object(raw)
        captions = picture.get("captions", [])
        if not isinstance(captions, list):
            raise ValueError("figure caption references must be a list")
        observed: list[str] = []
        for caption in captions:
            reference = _object(caption).get("$ref")
            if not isinstance(reference, str) or not reference.startswith("#/texts/"):
                continue
            suffix = reference.removeprefix("#/texts/")
            if not suffix.isdecimal() or int(suffix) >= len(texts):
                raise ValueError("figure caption reference is outside its retained layout")
            text = _object(texts[int(suffix)]).get("text")
            if isinstance(text, str):
                observed.append(text)
        caption_text = "\n".join(observed)
        folded = caption_text.casefold()
        if any(term in folded for term in policy.excluded_terms) or not any(
            term in folded for term in policy.candidate_terms
        ):
            omissions.append(f"figure:{index}:unadmitted_caption")
            continue
        provenance = picture.get("prov", [])
        if not isinstance(provenance, list) or len(provenance) != 1:
            omissions.append(f"figure:{index}:ambiguous_page_region")
            continue
        prov = _object(provenance[0])
        page_no = prov.get("page_no")
        if not isinstance(page_no, int) or isinstance(page_no, bool) or page_no < 1:
            raise ValueError("figure page number is invalid")
        page = _object(pages.get(str(page_no)))
        size = _object(page.get("size"))
        width, height = _number(size.get("width")), _number(size.get("height"))
        if width <= 0 or height <= 0:
            raise ValueError("figure page dimensions must be positive")
        bbox = _object(prov.get("bbox"))
        left, right = _number(bbox.get("l")), _number(bbox.get("r"))
        top, bottom = _number(bbox.get("t")), _number(bbox.get("b"))
        origin = bbox.get("coord_origin")
        if origin == "BOTTOMLEFT":
            top, bottom = height - top, height - bottom
        elif origin != "TOPLEFT":
            raise ValueError("figure coordinate origin is not declared")
        region = ImageRegion(
            left=left / width, right=right / width, top=top / height, bottom=bottom / height
        )
        if len(accepted) >= policy.max_figures:
            omissions.append(f"figure:{index}:pdf_figure_admission_bound")
            continue
        accepted.append(
            FigureSelection(
                picture_index=index,
                page_index=page_no - 1,
                region=region,
                caption=caption_text,
                layout_sha256=layout.sha256,
            )
        )
    return FigureSelections(figures=tuple(accepted), omissions=tuple(omissions))


class FigureCropRequest(VisualRecord):
    config: PdfFigureConfig
    source_url: str
    pdf: bytes
    selections: tuple[FigureSelection, ...]


class FigureCrop(VisualRecord):
    candidate: ImageCandidate
    png: bytes


class FigureCropResult(VisualRecord):
    crops: tuple[FigureCrop, ...]


class PdfFigureCropper:
    def __init__(self, config: PdfFigureConfig) -> None:
        renderer = config.renderer
        if not renderer.worker_python.is_file() or not os.access(renderer.worker_python, os.X_OK):
            raise ValueError("PDF figure renderer interpreter is unavailable")
        self.config = config
        self._worker = PassiveWorker(
            interpreter=renderer.worker_python,
            module="ghimera.pdf_figure_worker",
            work_directory=renderer.work_directory,
            max_workers=renderer.max_workers,
            timeout_seconds=renderer.timeout_seconds,
            max_output_bytes=renderer.max_output_bytes,
            max_diagnostic_bytes=renderer.max_diagnostic_bytes,
            cleanup_timeout_seconds=renderer.cleanup_timeout_seconds,
            environment={
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "HF_HUB_OFFLINE": "1",
                "CUDA_VISIBLE_DEVICES": "",
            },
        )

    async def crop(
        self, document: Document, selections: FigureSelections
    ) -> tuple[FigureCrop, ...]:
        if selections != select_figures(document, self.config):
            raise ValueError("PDF crops require exact source-layout candidate admission")
        if not selections.figures:
            return ()
        result = FigureCropResult.model_validate_json(
            await self._worker.run(
                FigureCropRequest(
                    config=self.config,
                    source_url=document.url,
                    pdf=document.raw,
                    selections=selections.figures,
                )
                .model_dump_json()
                .encode()
            )
        )
        if len(result.crops) != len(selections.figures):
            raise ValueError("PDF crop worker omitted an admitted figure")
        source = hashlib.sha256(document.raw).hexdigest()
        for selection, crop in zip(selections.figures, result.crops, strict=True):
            anchor = crop.candidate.pdf_crop
            if (
                anchor is None
                or anchor.source_sha256 != source
                or anchor.page_index != selection.page_index
                or anchor.region != selection.region
                or anchor.layout_sha256 != selection.layout_sha256
                or anchor.crop_policy_sha256 != self.config.content_digest()
                or anchor.render_policy_sha256 != self.config.renderer.content_digest()
                or crop.candidate.parent_url != document.url
                or crop.candidate.caption != selection.caption
                or crop.candidate.element_index != selection.picture_index
                or len(crop.png) > self.config.max_crop_bytes
                or len(crop.png) < 24
                or crop.png[:8] != b"\x89PNG\r\n\x1a\n"
                or crop.png[12:16] != b"IHDR"
            ):
                raise ValueError("PDF crop worker changed source, geometry or admission")
            width, height = struct.unpack(">II", crop.png[16:24])
            if (
                (width, height) != (crop.candidate.declared_width, crop.candidate.declared_height)
                or width <= 0
                or height <= 0
                or width * height > self.config.max_crop_pixels
            ):
                raise ValueError("PDF crop worker changed decoded dimensions or pixel bounds")
        return result.crops


def validate_pdf_images(document: DocumentSource, config: VisualConfig) -> None:
    """Synchronous archive admission; exact pixels are produced by the owned render worker."""
    crops = tuple(image for image in document.images if image.candidate.pdf_crop is not None)
    if not crops:
        return
    policy = config.pdf_figures
    if policy is None:
        raise ValueError("PDF crop evidence requires its effective figure policy")
    selections = {item.picture_index: item for item in select_figures(document, policy).figures}
    if len(crops) > policy.max_figures:
        raise ValueError("retained PDF crops exceed the admitted figure budget")
    for image in crops:
        candidate, anchor = image.candidate, image.candidate.pdf_crop
        selection = selections.get(candidate.element_index)
        if (
            anchor is None
            or selection is None
            or anchor.source_sha256 != document.sha256
            or hashlib.sha256(document.raw).hexdigest() != document.sha256
            or anchor.layout_sha256 != selection.layout_sha256
            or anchor.page_index != selection.page_index
            or anchor.region != selection.region
            or anchor.crop_policy_sha256 != policy.content_digest()
            or anchor.render_policy_sha256 != policy.renderer.content_digest()
            or candidate.caption != selection.caption
            or candidate.attributes != "native_pdf_figure"
            or candidate.parent_url != document.url
            or image.final_url != candidate.url
            or candidate.declared_width != image.ocr.width
            or candidate.declared_height != image.ocr.height
            or len(image.raw) > policy.max_crop_bytes
            or image.ocr.width * image.ocr.height > policy.max_crop_pixels
        ):
            raise ValueError(
                "PDF crop must bind original layout, page, region and effective policy"
            )


def figure_anchor(
    request: FigureCropRequest, selection: FigureSelection, page_sha256: str
) -> PdfFigureAnchor:
    return PdfFigureAnchor(
        schema="ghimera.pdf-figure-anchor/1",
        source_sha256=hashlib.sha256(request.pdf).hexdigest(),
        layout_sha256=selection.layout_sha256,
        page_index=selection.page_index,
        region=selection.region,
        page_image_sha256=page_sha256,
        render_policy_sha256=request.config.renderer.content_digest(),
        crop_policy_sha256=request.config.content_digest(),
    )
