"""Bounded offline render owner, separate from private model-control calls."""

import hashlib
import os
from pathlib import Path

from ghimera.page_transcription_config import PageRenderConfig
from ghimera.page_transcription_types import RenderedPdf, TranscriptionRecord
from ghimera.passive_worker import PassiveWorker
from ghimera.refusals import GhimeraRefused, RefusalCode


class PageRenderRequest(TranscriptionRecord):
    config: PageRenderConfig
    pdf: bytes


class PdfPageRenderer:
    def __init__(self, config: PageRenderConfig) -> None:
        if not config.worker_python.is_file() or not os.access(config.worker_python, os.X_OK):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self.config = config
        self._worker = PassiveWorker(
            interpreter=config.worker_python,
            module="ghimera.page_render_worker",
            work_directory=config.work_directory,
            max_workers=config.max_workers,
            timeout_seconds=config.timeout_seconds,
            max_output_bytes=config.max_output_bytes,
            max_diagnostic_bytes=config.max_diagnostic_bytes,
            cleanup_timeout_seconds=config.cleanup_timeout_seconds,
            environment={
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "HF_HUB_OFFLINE": "1",
                "CUDA_VISIBLE_DEVICES": "",
            },
        )

    async def render(self, pdf: bytes) -> RenderedPdf:
        if not pdf.startswith(b"%PDF-") or len(pdf) > self.config.max_input_bytes:
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
        response = await self._worker.run(
            PageRenderRequest(config=self.config, pdf=pdf).model_dump_json().encode()
        )
        try:
            result = RenderedPdf.model_validate_json(response)
        except ValueError:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        if (
            result.source_sha256 != hashlib.sha256(pdf).hexdigest()
            or result.policy_sha256 != self.config.content_digest()
            or len(result.pages) > self.config.max_pages
            or sum(len(page.png) for page in result.pages) > self.config.max_total_image_bytes
            or any(
                page.width * page.height > self.config.max_pixels_per_page
                or len(page.png) > self.config.max_image_bytes
                or page.scale != self.config.scale
                for page in result.pages
            )
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        return result
