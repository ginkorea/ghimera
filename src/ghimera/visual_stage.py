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
from ghimera.models import Extracted, Goal, LedgerRow, Page, Scope
from ghimera.ports import Judge
from ghimera.refusals import GhimeraRefused, ModelFailure, RefusalCode
from ghimera.visual_config import VisualConfig
from ghimera.visual_model import VisionReader
from ghimera.visual_types import ImageEvidence


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
                    if len(image.body) > config.max_image_bytes:
                        raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
                    ocr = await self._ocr.read(image.body, language_hint=language_hint)
                    if image.content_type != ocr.media_type:
                        raise GhimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
                    interpretation = None
                    if self._vision is not None:
                        interpretation = await self._vision.interpret(
                            intent=goal.text,
                            raw=image.body,
                            ocr=ocr,
                            budget=budget,
                            ledger=ledger,
                            url=image.final_url,
                        )
                        accepted = interpretation is not None
                        reason = "source_bound_visual_review"
                    elif ocr.spans:
                        extracted_image = Extracted(
                            title=candidate.caption or "image OCR",
                            text=ocr.text,
                            language="und",
                        )
                        invocation = ModelInvocation(
                            budget,
                            ledger,
                            phase="verdict",
                            model=self._judge.model,
                            url=image.final_url,
                            request=port_input(budget, goal, extracted_image, second_look=False),
                        )
                        try:
                            verdict = await invocation.invoke(
                                partial(
                                    self._judge.document, goal, extracted_image, second_look=False
                                ),
                                record_output,
                            )
                        except (GhimeraRefused, TimeoutError) as exc:
                            code = (
                                exc.code
                                if isinstance(exc, GhimeraRefused)
                                else RefusalCode.BUDGET_EXHAUSTED
                            )
                            ledger.append(
                                LedgerRow(
                                    sequence=ledger.next_sequence,
                                    event="verdict",
                                    url=image.final_url,
                                    model=self._judge.model,
                                    refusal=code,
                                    model_call=exc.model_call
                                    if isinstance(exc, ModelFailure)
                                    else None,
                                    reason="visual_ocr_judge_failed",
                                )
                            )
                            raise
                        ledger.append(
                            LedgerRow(
                                sequence=ledger.next_sequence,
                                event="verdict",
                                url=image.final_url,
                                model=self._judge.model,
                                model_call=verdict.model_call,
                                reason="visual_ocr:" + verdict.decision + ":" + verdict.reason,
                            )
                        )
                        accepted, reason = verdict.decision == "accept", verdict.reason
                    else:
                        accepted, reason = False, "no_readable_text:no_configured_vision"
                    digest = hashlib.sha256(image.body).hexdigest()
                    if accepted and digest not in {item.sha256 for item in retained}:
                        retained.append(
                            ImageEvidence(
                                schema="ghimera.image-evidence/1",
                                candidate=candidate,
                                final_url=image.final_url,
                                sha256=digest,
                                raw=image.body,
                                ocr=ocr,
                                config_sha256=hashlib.sha256(
                                    config.model_dump_json().encode()
                                ).hexdigest(),
                                interpretation=interpretation,
                                relevance_reason=reason,
                            )
                        )
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
