"""Selective visual enrichment of accepted pages; only accepted bytes survive."""

import asyncio
import hashlib
from functools import partial

from ghimera.budget import RunBudget
from ghimera.fetch import FetchLadder
from ghimera.image_candidates import admitted, image_candidates
from ghimera.image_ocr import ImageOcr
from ghimera.ledger import Ledger
from ghimera.model_work import ModelInvocation, port_input, record_output
from ghimera.models import Document, Extracted, Goal, LedgerRow, Page, Scope
from ghimera.pdf_figures import PdfFigureCropper, select_figures
from ghimera.ports import Judge
from ghimera.refusals import GhimeraRefused, ModelFailure, RefusalCode
from ghimera.visual_config import VisualConfig
from ghimera.visual_model import VisionReader
from ghimera.visual_types import ImageCandidate, ImageEvidence


class VisualStage:
    def __init__(
        self,
        config: VisualConfig,
        *,
        ocr: ImageOcr,
        judge: Judge,
        vision: VisionReader | None = None,
    ) -> None:
        self.config, self._ocr, self._judge, self._vision = config, ocr, judge, vision
        if ocr.config != config or (vision is not None and vision.config != config):
            raise ValueError("visual collaborators must bind the same recipe")
        if (config.vision is not None) != (vision is not None):
            raise ValueError("configured visual interpretation cannot silently disappear")
        self._pdf_cropper = PdfFigureCropper(config.pdf_figures) if config.pdf_figures else None

    async def _inspect(
        self,
        *,
        goal: Goal,
        candidate: ImageCandidate,
        raw: bytes,
        final_url: str,
        media_type: str,
        budget: RunBudget,
        ledger: Ledger,
        language_hint: str | None,
    ) -> ImageEvidence | None:
        if len(raw) > self.config.max_image_bytes:
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
        ocr = await self._ocr.read(raw, language_hint=language_hint)
        if media_type != ocr.media_type:
            raise GhimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
        interpretation = None
        if self._vision is not None:
            interpretation = await self._vision.interpret(
                intent=goal.text,
                raw=raw,
                ocr=ocr,
                budget=budget,
                ledger=ledger,
                url=final_url,
            )
            accepted, reason = interpretation is not None, "source_bound_visual_review"
        elif ocr.spans:
            extracted = Extracted(
                title=candidate.caption or "image OCR", text=ocr.text, language="und"
            )
            invocation = ModelInvocation(
                budget,
                ledger,
                phase="verdict",
                model=self._judge.model,
                url=final_url,
                request=port_input(budget, goal, extracted, second_look=False),
            )
            try:
                verdict = await invocation.invoke(
                    partial(self._judge.document, goal, extracted, second_look=False),
                    record_output,
                )
            except (GhimeraRefused, TimeoutError) as exc:
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="verdict",
                        url=final_url,
                        model=self._judge.model,
                        refusal=exc.code
                        if isinstance(exc, GhimeraRefused)
                        else RefusalCode.BUDGET_EXHAUSTED,
                        model_call=exc.model_call if isinstance(exc, ModelFailure) else None,
                        reason="visual_ocr_judge_failed",
                    )
                )
                raise
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="verdict",
                    url=final_url,
                    model=self._judge.model,
                    model_call=verdict.model_call,
                    reason="visual_ocr:" + verdict.decision + ":" + verdict.reason,
                )
            )
            accepted, reason = verdict.decision == "accept", verdict.reason
        else:
            accepted, reason = False, "no_readable_text:no_configured_vision"
        digest = hashlib.sha256(raw).hexdigest()
        ledger.append(
            LedgerRow(
                sequence=ledger.next_sequence,
                event="visual",
                url=candidate.url,
                reason=("visual_retained:" if accepted else "visual_rejected:")
                + digest
                + ":"
                + reason,
            )
        )
        if not accepted:
            return None
        return ImageEvidence(
            schema="ghimera.image-evidence/1",
            candidate=candidate,
            final_url=final_url,
            sha256=digest,
            raw=raw,
            ocr=ocr,
            config_sha256=hashlib.sha256(self.config.model_dump_json().encode()).hexdigest(),
            interpretation=interpretation,
            relevance_reason=reason,
        )

    async def collect_pdf(
        self,
        *,
        goal: Goal,
        document: Document,
        budget: RunBudget,
        ledger: Ledger,
        language_hint: str | None = None,
    ) -> tuple[ImageEvidence, ...]:
        if self._pdf_cropper is None:
            return ()
        selections = select_figures(document, self._pdf_cropper.config)
        for omission in selections.omissions:
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="visual",
                    url=document.url,
                    reason="pdf_visual_gap:" + omission,
                )
            )
        try:
            async with asyncio.timeout(budget.remaining_seconds):
                crops = await self._pdf_cropper.crop(document, selections)
                retained: dict[str, ImageEvidence] = {}
                for crop in crops:
                    image = await self._inspect(
                        goal=goal,
                        candidate=crop.candidate,
                        raw=crop.png,
                        final_url=crop.candidate.url,
                        media_type="image/png",
                        budget=budget,
                        ledger=ledger,
                        language_hint=language_hint,
                    )
                    if image is not None:
                        retained.setdefault(image.sha256, image)
                return tuple(retained.values())
        except (ValueError, GhimeraRefused) as exc:
            if isinstance(exc, GhimeraRefused) and exc.code in {
                RefusalCode.BUDGET_EXHAUSTED,
                RefusalCode.LEDGER_SINK_FAILED,
            }:
                raise
            ledger.append(
                LedgerRow(
                    sequence=ledger.next_sequence,
                    event="visual",
                    url=document.url,
                    refusal=exc.code
                    if isinstance(exc, GhimeraRefused)
                    else RefusalCode.EXTRACTION_FAILED,
                    reason="pdf_visual_refused:no_image_retained",
                )
            )
            return ()
        except TimeoutError:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED) from None

    async def collect(
        self,
        *,
        goal: Goal,
        parent: Page,
        scope: Scope,
        fetcher: FetchLadder,
        budget: RunBudget,
        ledger: Ledger,
        language_hint: str | None = None,
    ) -> tuple[ImageEvidence, ...]:
        config = self.config
        image_scope = Scope(
            allowed_hosts=tuple(dict.fromkeys(scope.allowed_hosts + config.allowed_hosts)),
            max_depth=0,
            allowed_ports=scope.allowed_ports,
            content_types=config.image_types,
        )
        retained: list[ImageEvidence] = []
        seen_urls: set[str] = set()
        attempts = 0
        for candidate in image_candidates(parent, config):
            if candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            if not admitted(candidate, config) or not image_scope.permits(candidate.url):
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="visual",
                        url=candidate.url,
                        reason="visual_prefilter_rejected:no_image_download",
                    )
                )
                continue
            if attempts >= config.max_images_per_page:
                break
            attempts += 1
            image: Page | None = None
            try:
                async with asyncio.timeout(budget.remaining_seconds):
                    image = await fetcher.fetch_image(candidate.url, image_scope, budget, ledger)
                    evidence = await self._inspect(
                        goal=goal,
                        candidate=candidate,
                        raw=image.body,
                        final_url=image.final_url,
                        media_type=image.content_type,
                        budget=budget,
                        ledger=ledger,
                        language_hint=language_hint,
                    )
                    if evidence is not None and evidence.sha256 not in {
                        item.sha256 for item in retained
                    }:
                        retained.append(evidence)
            except (GhimeraRefused, ValueError, TimeoutError) as exc:
                if isinstance(exc, GhimeraRefused) and exc.code in {
                    RefusalCode.BUDGET_EXHAUSTED,
                    RefusalCode.LEDGER_SINK_FAILED,
                }:
                    raise
                if isinstance(exc, TimeoutError):
                    raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED) from None
                ledger.append(
                    LedgerRow(
                        sequence=ledger.next_sequence,
                        event="visual",
                        url=candidate.url,
                        refusal=exc.code
                        if isinstance(exc, GhimeraRefused)
                        else RefusalCode.EXTRACTION_FAILED,
                        reason="visual_refused:no_image_retained",
                    )
                )
            finally:
                fetcher.discard_cached(
                    candidate.url, image.final_url if image is not None else candidate.url
                )
        return tuple(retained)
