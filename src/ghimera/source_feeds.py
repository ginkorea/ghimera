"""Feed extraction composes with the existing collector; it never owns fetch I/O."""

import os
from pathlib import Path

from ghimera.config import GhimeraConfig
from ghimera.models import Extracted, LinkCandidate, Page, Record
from ghimera.passive_worker import PassiveWorker
from ghimera.ports import Extractor
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.source_feed_config import SourceFeedConfig
from ghimera.source_feed_types import SourceFeedEvidence


class SourceFeedRequest(Record):
    policy: SourceFeedConfig
    page: Page


class SourceFeedExtractor:
    revision = "source-feed-parser/1"

    def __init__(self, config: GhimeraConfig) -> None:
        if config.source_feeds is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self.policy = config.source_feeds
        policy = self.policy
        if not policy.worker_python.is_file() or not os.access(policy.worker_python, os.X_OK):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self._worker = PassiveWorker(
            interpreter=policy.worker_python,
            module="ghimera.source_feed_worker",
            work_directory=policy.work_directory,
            max_workers=policy.max_workers,
            timeout_seconds=policy.timeout_seconds,
            max_output_bytes=policy.max_output_bytes,
            max_diagnostic_bytes=policy.max_diagnostic_bytes,
            cleanup_timeout_seconds=policy.cleanup_timeout_seconds,
            environment={
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

    def validate_config(self, config: GhimeraConfig) -> None:
        if config.source_feeds != self.policy:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    async def extract(self, page: Page) -> Extracted:
        if len(page.body) > self.policy.max_input_bytes or page.rendered is not None:
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
        try:
            evidence = SourceFeedEvidence.model_validate_json(
                await self._worker.run(
                    SourceFeedRequest(policy=self.policy, page=page).model_dump_json().encode()
                )
            )
            if evidence.policy != self.policy or evidence.source_url != page.final_url:
                raise ValueError("feed worker changed its admitted source policy")
            evidence.validate_source(page.body)
            return Extracted(
                title=evidence.reading_title,
                text=evidence.text,
                language="und",
                links=tuple(
                    LinkCandidate(url=url, anchor=anchor) for url, anchor in evidence.links
                ),
                source_feed=evidence,
            )
        except ValueError:
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED) from None


class SourceFeedExtractionSuite:
    def __init__(self, *, fallback: Extractor, feeds: SourceFeedExtractor) -> None:
        self._fallback, self._feeds = fallback, feeds

    @property
    def revision(self) -> str:
        return self._fallback.revision + "+" + self._feeds.revision

    def validate_config(self, config: GhimeraConfig) -> None:
        self._fallback.validate_config(config)
        self._feeds.validate_config(config)

    async def extract(self, page: Page) -> Extracted:
        if page.content_type.split(";", 1)[0].strip().lower() in self._feeds.policy.content_types:
            return await self._feeds.extract(page)
        return await self._fallback.extract(page)
