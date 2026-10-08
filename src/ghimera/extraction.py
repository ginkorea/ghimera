"""Bounded subprocess extraction: source I/O remains owned by the fetch ladder."""

import asyncio
import codecs
import hashlib
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import model_validator

from ghimera.config import GhimeraConfig
from ghimera.extraction_attempts import (
    ExtractionCancelled,
    ExtractionFailure,
    HtmlExtractionAttempt,
    InvalidExtractionResponse,
)
from ghimera.extraction_config import ExtractionConfig
from ghimera.extraction_types import ExtractionEvidence, LocatorEvent
from ghimera.locator_health import LocatorHealthStore
from ghimera.models import Extracted, Page, Record
from ghimera.passive_worker import PassiveWorker
from ghimera.passive_worker import private_directory as private_directory
from ghimera.passive_worker import read_bounded as read_bounded
from ghimera.refusals import GhimeraRefused, RefusalCode


class ExtractionRequest(Record):
    config: ExtractionConfig
    page: Page
    generic_only: bool = False


class ExtractionResponse(Record):
    result: Extracted | None = None
    refusal: RefusalCode | None = None
    locator_events: tuple[LocatorEvent, ...] = ()

    @model_validator(mode="after")
    def one_outcome(self) -> "ExtractionResponse":
        if (self.result is None) == (self.refusal is None):
            raise ValueError("one extraction outcome is required")
        return self


class HtmlExtractor:
    def __init__(self, config: GhimeraConfig) -> None:
        if config.extraction is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self.config = config.extraction
        self._health = (
            LocatorHealthStore(self.config.locator_directory, self.config.locator_drift)
            if self.config.locator_drift is not None
            else None
        )
        try:
            actual = tuple(
                version(name) for name in ("scrapling", "crawl4ai", "lingua-language-detector")
            )
            codecs.lookup(self.config.default_encoding)
        except (PackageNotFoundError, LookupError):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        if actual != ("0.4.2", "0.9.4", "2.1.1"):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        from lingua import IsoCode639_1

        if any(
            not isinstance(getattr(IsoCode639_1, code.upper(), None), IsoCode639_1)
            for code in self.config.languages
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self._revision = f"scrapling@{actual[0]}+crawl4ai@{actual[1]}+lingua@{actual[2]}"
        self._worker = PassiveWorker(
            interpreter=Path(sys.executable),
            module="ghimera.html_worker",
            work_directory=self.config.work_directory,
            max_workers=self.config.max_workers,
            timeout_seconds=self.config.timeout_seconds,
            max_output_bytes=self.config.max_output_bytes,
            max_diagnostic_bytes=self.config.max_diagnostic_bytes,
            cleanup_timeout_seconds=self.config.cleanup_timeout_seconds,
            environment={
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "CRAWL4_AI_BASE_DIRECTORY": str(self.config.work_directory),
                "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                "DO_NOT_TRACK": "1",
                "HF_HUB_OFFLINE": "1",
            },
        )

    @property
    def revision(self) -> str:
        return self._revision

    def validate_config(self, config: GhimeraConfig) -> None:
        if config.extraction != self.config:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    async def extract(self, page: Page) -> Extracted:
        if page.content_type.split(";", 1)[0].strip().lower() not in {
            "text/html",
            "application/xhtml+xml",
        }:
            raise GhimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
        if (
            not page.body
            or len(page.body) > self.config.max_input_bytes
            or (page.rendered is not None and len(page.rendered.html) > self.config.max_input_bytes)
        ):
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
        private_directory(self.config.locator_directory)
        profile = next(
            (p for p in self.config.profiles if p.host == urlsplit(page.final_url).hostname), None
        )
        before = (
            await asyncio.to_thread(self._health.read, profile)
            if self._health is not None and profile is not None
            else None
        )
        generic = before.generic_only if before else False
        attempts: list[HtmlExtractionAttempt] = []
        retry = self.config.recovery is not None and self.config.recovery.mode == "generic_once"
        try:
            # One deadline covers both slot waits and parsing. Owned child
            # cleanup remains separately bounded by cleanup_timeout_seconds.
            async with asyncio.timeout(self.config.timeout_seconds):
                for sequence in range(2 if retry else 1):
                    responses: list[str] = []
                    locators: list[LocatorEvent] = []
                    started = time.monotonic()
                    result = None
                    failure = None
                    try:
                        result = await self._parse(page, generic, responses, locators)
                    except asyncio.CancelledError:
                        attempts.append(
                            self._observation(
                                page,
                                sequence,
                                generic,
                                "cancelled",
                                None,
                                responses,
                                started,
                                locators,
                            )
                        )
                        raise
                    except GhimeraRefused as exc:
                        failure = exc
                        attempts.append(
                            self._observation(
                                page,
                                sequence,
                                generic,
                                "invalid_response"
                                if isinstance(exc, InvalidExtractionResponse)
                                else "refused",
                                exc.code,
                                responses,
                                started,
                                locators,
                            )
                        )
                    else:
                        attempts.append(
                            self._observation(
                                page,
                                sequence,
                                generic,
                                "success",
                                None,
                                responses,
                                started,
                                locators,
                            )
                        )
                    if result is not None and result.extraction is not None:
                        values = result.extraction.model_dump(by_alias=True)
                        values["attempts"] = tuple(attempts)
                        updated = result.model_dump(by_alias=True)
                        updated["extraction"] = ExtractionEvidence.model_validate(values)
                        return Extracted.model_validate(updated)
                    if failure is None:
                        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                    if (
                        sequence == 0
                        and retry
                        and (
                            failure.code == RefusalCode.EXTRACTION_FAILED
                            or isinstance(failure, InvalidExtractionResponse)
                        )
                    ):
                        generic = True
                        continue
                    raise ExtractionFailure(failure.code, tuple(attempts)) from None
        except TimeoutError:
            if attempts and attempts[-1].outcome == "cancelled":
                values = attempts[-1].model_dump(by_alias=True)
                values.update(outcome="refused", refusal=RefusalCode.BUDGET_EXHAUSTED)
                attempts[-1] = HtmlExtractionAttempt.model_validate(values)
            raise ExtractionFailure(RefusalCode.BUDGET_EXHAUSTED, tuple(attempts)) from None
        except asyncio.CancelledError:
            raise ExtractionCancelled(tuple(attempts)) from None
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    def _observation(
        self,
        page: Page,
        sequence: int,
        generic: bool,
        outcome: Literal["success", "refused", "invalid_response", "cancelled"],
        refusal: RefusalCode | None,
        responses: list[str],
        started: float,
        locators: list[LocatorEvent],
    ) -> HtmlExtractionAttempt:
        return HtmlExtractionAttempt(
            schema="chimera.html-extraction-attempt/1",
            sequence=sequence,
            phase="initial" if sequence == 0 else "generic_retry",
            generic_only=generic,
            source_url=page.final_url,
            source_sha256=hashlib.sha256(page.body).hexdigest(),
            rendered_sha256=page.rendered.html_sha256 if page.rendered else None,
            config_digest=self.config.content_digest(),
            parser_revision=self.revision,
            outcome=outcome,
            refusal=refusal,
            response_sha256=responses[0] if responses else None,
            latency_seconds=max(0.0, time.monotonic() - started),
            locators=tuple(locators),
        )

    async def _parse(
        self, page: Page, generic: bool, responses: list[str], locators: list[LocatorEvent]
    ) -> Extracted:
        profile = next(
            (p for p in self.config.profiles if p.host == urlsplit(page.final_url).hostname), None
        )
        request = (
            ExtractionRequest(config=self.config, page=page, generic_only=generic)
            .model_dump_json()
            .encode()
        )
        response = await self._worker.run(request)
        responses.append(hashlib.sha256(response).hexdigest())
        try:
            wire = ExtractionResponse.model_validate_json(response)
        except ValueError:
            raise InvalidExtractionResponse() from None
        expected = (
            {}
            if profile is None
            else {
                "body": profile.body,
                "title": profile.title,
                "byline": profile.byline,
                "date": profile.date,
            }
        )
        if (
            (generic and wire.locator_events)
            or len({event.field for event in wire.locator_events}) != len(wire.locator_events)
            or any(expected.get(event.field) != event.selector for event in wire.locator_events)
        ):
            raise InvalidExtractionResponse()
        locators.extend(wire.locator_events)
        if wire.refusal is not None:
            if self._health is not None and profile is not None:
                await asyncio.to_thread(
                    self._health.observe,
                    profile,
                    hashlib.sha256(page.body).hexdigest(),
                    wire.locator_events,
                    completed=False,
                )
            raise GhimeraRefused(wire.refusal)
        result = wire.result
        if result is None or result.extraction is None:
            raise InvalidExtractionResponse()
        evidence = result.extraction
        if (
            evidence.source_sha256 != hashlib.sha256(page.body).hexdigest()
            or evidence.source_url != page.final_url
            or evidence.text_sha256 != hashlib.sha256(result.text.encode()).hexdigest()
            or evidence.config_digest != self.config.content_digest()
            or evidence.parser_revision != self.revision
            or evidence.rendered_sha256
            != (page.rendered.html_sha256 if page.rendered is not None else None)
            or len(result.text) > self.config.max_text_chars
            or len(result.links) > self.config.max_links
            or wire.locator_events != evidence.locators
            or evidence.locator_health is not None
            or evidence.attempts
        ):
            raise InvalidExtractionResponse()
        if self._health is not None and profile is not None:
            health = await asyncio.to_thread(
                self._health.observe,
                profile,
                hashlib.sha256(page.body).hexdigest(),
                wire.locator_events,
            )
            values = evidence.model_dump(by_alias=True)
            values["locator_health"] = health.model_dump(by_alias=True)
            revised = result.model_dump(by_alias=True)
            revised["extraction"] = ExtractionEvidence.model_validate(values)
            return Extracted.model_validate(revised)
        return result
