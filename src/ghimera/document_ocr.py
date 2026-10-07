"""Worker-only native OCR that preserves one recognition and its source boxes."""

from collections.abc import Iterable
from pathlib import Path

from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.base_models import Page
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import TesseractOcrOptions
from docling.models.base_ocr_model import BaseOcrModel
from docling.models.stages.ocr.tesseract_utils import tesseract_box_to_bounding_rectangle
from docling_core.types.doc.base import BoundingBox, CoordOrigin
from docling_core.types.doc.page import TextCell
from pydantic import Field
from tesserocr import RIL, PyTessBaseAPI, get_languages

from ghimera.refusals import GhimeraRefused, RefusalCode


class PageRecognitionOptions(TesseractOcrOptions):
    """Per-instance vendor extension; never a global plugin or source patch."""

    detect_orientation: bool
    minimum_orientation_confidence: float = Field(ge=0, allow_inf_nan=False)


class PageRecognitionOcr(BaseOcrModel):
    """Reuse Docling region selection and PDF/OCR merging, not its line recrop.

    Tesseract recognizes the selected region once. Text, confidence and geometry
    come from that same result iterator: no tightly cropped second recognition
    can replace the text with a different reading. The owned parser process
    bounds native execution and owns all reader lifetimes.
    """

    multiple_languages = True

    @classmethod
    def get_options_type(cls) -> type[PageRecognitionOptions]:
        return PageRecognitionOptions

    def __init__(
        self,
        *,
        enabled: bool,
        artifacts_path: Path | None,
        options: PageRecognitionOptions,
        accelerator_options: AcceleratorOptions,
    ) -> None:
        super().__init__(
            enabled=enabled,
            artifacts_path=artifacts_path,
            options=options,
            accelerator_options=accelerator_options,
        )
        self._options = options
        self._reader: PyTessBaseAPI | None = None
        self._orientation_reader: PyTessBaseAPI | None = None
        if not enabled:
            return
        if options.path is None or options.psm is None or not options.lang:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        _, installed = get_languages(options.path)
        required = set(options.lang) | ({"osd"} if options.detect_orientation else set())
        if not required <= set(installed):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        try:
            self._reader = PyTessBaseAPI(
                path=options.path,
                lang="+".join(options.lang),
                psm=options.psm,
            )
            if options.detect_orientation:
                self._orientation_reader = PyTessBaseAPI(path=options.path, lang="osd", psm=0)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        for name in ("_reader", "_orientation_reader"):
            reader = getattr(self, name, None)
            if isinstance(reader, PyTessBaseAPI):
                reader.End()
                setattr(self, name, None)

    def __del__(self) -> None:
        self.close()

    def __call__(self, conv_res: ConversionResult, page_batch: Iterable[Page]) -> Iterable[Page]:
        if not self.enabled:
            yield from page_batch
            return
        reader = self._reader
        if reader is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        for page in page_batch:
            backend = page._backend
            if backend is None or not backend.is_valid():
                yield page
                continue
            cells: list[TextCell] = []
            for region in self.get_ocr_rects(page):
                if region.area() == 0:
                    continue
                image = backend.get_page_image(scale=self._options.scale, cropbox=region)
                orientation = 0
                if self._orientation_reader is not None:
                    self._orientation_reader.SetImage(image)
                    observed = self._orientation_reader.DetectOrientationScript()
                    if observed is not None and observed["orient_conf"] >= (
                        self._options.minimum_orientation_confidence
                    ):
                        degrees = observed["orient_deg"]
                        if degrees not in (0, 90, 180, 270):
                            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                        orientation = (-degrees) % 360
                        if orientation:
                            image = image.rotate(-orientation, expand=True)
                reader.SetImage(image)
                if not reader.Recognize():
                    raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
                iterator = reader.GetIterator()
                if iterator is None:
                    continue
                while True:
                    box = iterator.BoundingBox(RIL.TEXTLINE)
                    if box is not None:
                        text = iterator.GetUTF8Text(RIL.TEXTLINE).strip()
                        if text:
                            left, top, right, bottom = box
                            rect = tesseract_box_to_bounding_rectangle(
                                BoundingBox(
                                    l=left,
                                    t=top,
                                    r=right,
                                    b=bottom,
                                    coord_origin=CoordOrigin.TOPLEFT,
                                ),
                                original_offset=region,
                                scale=self._options.scale,
                                orientation=orientation,
                                im_size=image.size,
                            )
                            cells.append(
                                TextCell(
                                    index=len(cells),
                                    text=text,
                                    orig=text,
                                    from_ocr=True,
                                    confidence=iterator.Confidence(RIL.TEXTLINE) / 100,
                                    rect=rect,
                                )
                            )
                    if not iterator.Next(RIL.TEXTLINE):
                        break
            self.post_process_cells(cells, page, conv_res)
            yield page
