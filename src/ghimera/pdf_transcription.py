"""Reviewed PDF extraction integrated with the collector's own budgets and evidence."""

import asyncio
import hashlib
from typing import Literal, Protocol

from ghimera.budget import RunBudget
from ghimera.ledger import Ledger
from ghimera.models import Extracted, LedgerRow, Page, PdfTranscriptionEvidence
from ghimera.page_transcriber import PageTranscriber
from ghimera.page_transcription_config import PageRenderConfig, PdfTranscriptionConfig
from ghimera.page_transcription_types import RenderedPdf
from ghimera.ports import Extractor
from ghimera.refusals import GhimeraRefused, RefusalCode


class PdfRenderer(Protocol):
    @property
    def config(self) -> PageRenderConfig: ...

    async def render(self, pdf: bytes) -> RenderedPdf: ...


class PdfTranscriptionStage:
    """No hidden model fallback; native readings and refusals stay independently retained."""

    revision = "ghimera.reviewed-pdf/1"

    def __init__(
        self, config: PdfTranscriptionConfig, *, renderer: PdfRenderer, transcriber: PageTranscriber
    ) -> None:
        if renderer.config != config.pages.renderer or transcriber.config != config.pages:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self.config, self._renderer, self._transcriber = config, renderer, transcriber

    async def extract(
        self, page: Page, *, native: Extractor, budget: RunBudget, ledger: Ledger
    ) -> Extracted:
        if page.content_type.split(";", 1)[0].strip().lower() != "application/pdf":
            return await native.extract(page)
        reading = None
        refusal: Literal[RefusalCode.EXTRACTION_FAILED] | None = None
        try:
            reading = await native.extract(page)
        except GhimeraRefused as exc:
            if exc.code != RefusalCode.EXTRACTION_FAILED:
                raise
            refusal = RefusalCode.EXTRACTION_FAILED
        if reading is not None and self.config.mode == "native_refused":
            return reading
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event="policy",
                url=page.final_url,
                refusal=refusal,
                document_parse=reading.document_parse if reading is not None else None,
                reason=f"pdf_transcription_selected:{self.config.content_digest()}",
            )
        )
        rendered = await self._renderer.render(page.body)
        render_policy = self.config.pages.renderer
        if (
            rendered.source_sha256 != hashlib.sha256(page.body).hexdigest()
            or rendered.policy_sha256 != render_policy.content_digest()
            or len(rendered.pages) > render_policy.max_pages
            or sum(len(item.png) for item in rendered.pages) > render_policy.max_total_image_bytes
            or any(
                item.width * item.height > render_policy.max_pixels_per_page
                or item.scale != render_policy.scale
                or len(item.png) > render_policy.max_image_bytes
                for item in rendered.pages
            )
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        tasks = [
            asyncio.create_task(
                self._transcriber.transcribe(
                    pixels,
                    language_hint=self.config.language_hint,
                    source_url=page.final_url,
                    budget=budget,
                    ledger=ledger,
                )
            )
            for pixels in rendered.pages
        ]
        try:
            pages = tuple(await asyncio.gather(*tasks))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if any(not item.accepted for item in pages):
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
        if len("\n\n".join(item.proposal.text for item in pages)) > (
            self.config.max_document_text_chars
        ):
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        try:
            evidence = PdfTranscriptionEvidence(
                schema="ghimera.pdf-transcription-evidence/1",
                source_url=page.final_url,
                source_sha256=rendered.source_sha256,
                config=self.config,
                pages=pages,
                native_reading=reading,
                native_refusal=refusal,
            )
            return Extracted(
                title=reading.title if reading is not None else pages[0].proposal.lines[0],
                text=evidence.text,
                language=self.config.language_hint,
                # Native references are not silently rebound onto generated offsets.
                links=reading.links if reading is not None else (),
                byline=reading.byline if reading is not None else None,
                date=reading.date if reading is not None else None,
                pdf_transcription=evidence,
            )
        except ValueError:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
