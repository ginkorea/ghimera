"""Worker-only vendor construction from the lightweight, explicit PDF policy."""

from importlib.metadata import PackageNotFoundError, version

from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.object_detection_engine_options import (
    OnnxRuntimeObjectDetectionEngineOptions,
    TransformersObjectDetectionEngineOptions,
)
from docling.datamodel.pipeline_options import (
    LayoutObjectDetectionOptions,
    OcrMode,
    PdfPipelineOptions,
    RapidOcrOptions,
    TableFormerMode,
    TableStructureOptions,
    ThreadedPdfPipelineOptions,
)
from docling.datamodel.stage_model_specs import ObjectDetectionModelSpec
from docling.models.postprocessing.reading_order_rb import (
    PageElement,
    ReadingOrderPredictor,
    SeparatorElement,
)
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling_core.types.doc.labels import DocItemLabel

from ghimera.document_config import DocumentExtractionConfig
from ghimera.document_order import LayoutBlock, ReadingOrderPolicy, column_order
from ghimera.refusals import GhimeraRefused, RefusalCode


class ConfiguredPdfPipelineOptions(ThreadedPdfPipelineOptions):
    """Pinned worker-only extension of Docling's actual pipeline options."""

    reading_order_policy: ReadingOrderPolicy


class ConfiguredReadingOrderPredictor(ReadingOrderPredictor):
    """Reuse caption/footnote/merge behavior; replace only geometric ordering.

    Native PDF separator geometry takes precedence through the vendor's existing
    implementation. XY cut addresses pages without those observed separators,
    notably scans. Every returned element is an unchanged original object.
    """

    def __init__(self, policy: ReadingOrderPolicy) -> None:
        # The inspected 2.134.0 vendor constructor is unannotated and owns only
        # this switch. Preserve that exact per-instance initialization rather
        # than introduce an untyped call or process-global override. The worker
        # refuses a different vendor revision before constructing this adapter.
        self.dilated_page_element = True
        self._policy = policy

    def predict_reading_order(
        self,
        page_elements: list[PageElement],
        page_separators: list[SeparatorElement] | None = None,
    ) -> list[PageElement]:
        if self._policy.method == "vendor" or page_separators:
            return super().predict_reading_order(page_elements, page_separators)
        by_page: dict[int, list[tuple[int, PageElement]]] = {}
        for index, element in enumerate(page_elements):
            by_page.setdefault(element.page_no, []).append((index, element))
        ordered: list[PageElement] = []
        for page in sorted(by_page):
            values = by_page[page]
            size = values[0][1].page_size
            for labels in ((DocItemLabel.PAGE_HEADER,), (), (DocItemLabel.PAGE_FOOTER,)):
                selected = tuple(
                    (index, element)
                    for index, element in values
                    if (
                        element.label in labels
                        if labels
                        else element.label
                        not in {
                            DocItemLabel.PAGE_HEADER,
                            DocItemLabel.PAGE_FOOTER,
                        }
                    )
                )
                blocks: list[LayoutBlock] = []
                for index, element in selected:
                    box = element.to_top_left_origin(page_height=size.height)
                    blocks.append(LayoutBlock(index, box.l, box.t, box.r, box.b))
                indices = column_order(
                    tuple(blocks), self._policy, page_width=size.width, page_height=size.height
                )
                ordered.extend(page_elements[index] for index in indices)
        return ordered


class ConfiguredPdfPipeline(StandardPdfPipeline):
    """Reuse the vendor pipeline; configure its per-instance reading-order switch.

    This is a pinned 2.134.0 integration, not a patch to vendor source or a
    process-global override. Generic vendor options retain vendor behavior.
    """

    def __init__(self, pipeline_options: ThreadedPdfPipelineOptions) -> None:
        super().__init__(pipeline_options)
        if isinstance(pipeline_options, ConfiguredPdfPipelineOptions):
            self.reading_order_model.ro_model = ConfiguredReadingOrderPredictor(
                pipeline_options.reading_order_policy
            )


def pdf_options(config: DocumentExtractionConfig) -> PdfPipelineOptions:
    models, root = config.pdf_models, config.artifacts_directory
    if models is None or root is None or config.pdf_pipeline != "standard":
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    try:
        if any(version(pin.name) != pin.version for pin in models.runtime_packages):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    except PackageNotFoundError:
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
    engine = (
        OnnxRuntimeObjectDetectionEngineOptions(
            model_filename=models.layout_model_filename,
            providers=["CPUExecutionProvider"],
            score_threshold=models.layout_score_threshold,
        )
        if models.layout_engine == "onnxruntime"
        else TransformersObjectDetectionEngineOptions(
            compile_model=False,
            score_threshold=models.layout_score_threshold,
        )
    )
    layout = LayoutObjectDetectionOptions(
        model_spec=ObjectDetectionModelSpec(
            name=models.layout_repository,
            repo_id=models.layout_repository,
            revision=models.layout_revision,
            engine_overrides={},
        ),
        engine_options=engine,
    )
    options = ConfiguredPdfPipelineOptions(
        artifacts_path=root,
        enable_remote_services=False,
        allow_external_plugins=False,
        document_timeout=config.timeout_seconds,
        do_ocr=config.do_ocr,
        do_table_structure=config.do_table_structure,
        generate_page_images=False,
        generate_picture_images=False,
        accelerator_options=AcceleratorOptions(device="cpu", num_threads=config.cpu_threads),
        layout_options=layout,
        reading_order_policy=models.reading_order,
        images_scale=models.images_scale,
        table_structure_options=TableStructureOptions(
            mode=TableFormerMode.ACCURATE
            if models.table_mode == "accurate"
            else TableFormerMode.FAST,
            do_cell_matching=models.table_cell_matching,
        ),
    )
    if models.ocr is not None:
        ocr = models.ocr
        options.ocr_options = RapidOcrOptions(
            backend="onnxruntime",
            lang=[ocr.language],
            model_size=ocr.model_size,
            det_model_path=str(root / ocr.detection_path),
            cls_model_path=str(root / ocr.classification_path),
            rec_model_path=str(root / ocr.recognition_path),
            rec_keys_path=str(root / ocr.recognition_keys_path)
            if ocr.recognition_keys_path
            else None,
            mode=OcrMode(ocr.mode),
            scale=ocr.scale,
            text_score=ocr.text_score,
            use_det=True,
            use_cls=True,
            use_rec=True,
            print_verbose=False,
        )
    return options
