"""Bounded subprocess extraction: source I/O remains owned by the fetch ladder."""

import asyncio
import codecs
import hashlib
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pydantic import model_validator

from chimera.config import ChimeraConfig
from chimera.extraction_config import ExtractionConfig
from chimera.models import Extracted, Page, Record
from chimera.refusals import ChimeraRefused, RefusalCode


class ExtractionRequest(Record):
    config: ExtractionConfig
    page: Page


class ExtractionResponse(Record):
    result: Extracted | None = None
    refusal: RefusalCode | None = None

    @model_validator(mode="after")
    def one_outcome(self) -> "ExtractionResponse":
        if (self.result is None) == (self.refusal is None):
            raise ValueError("one extraction outcome is required")
        return self


def private_directory(path: Path) -> None:
    """Never widen or reuse another owner's state directory."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)


async def read_bounded(stream: asyncio.StreamReader | None, limit: int) -> bytes:
    if stream is None:
        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    chunks: list[bytes] = []
    total = 0
    while data := await stream.read(min(65536, limit - total + 1)):
        total += len(data)
        if total > limit:
            raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
        chunks.append(data)
    return b"".join(chunks)


class HtmlExtractor:
    def __init__(self, config: ChimeraConfig) -> None:
        if config.extraction is None:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self.config = config.extraction
        try:
            actual = tuple(
                version(name) for name in ("scrapling", "crawl4ai", "lingua-language-detector")
            )
            codecs.lookup(self.config.default_encoding)
        except (PackageNotFoundError, LookupError):
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        if actual != ("0.4.2", "0.9.4", "2.1.1"):
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        from lingua import IsoCode639_1

        if any(
            not isinstance(getattr(IsoCode639_1, code.upper(), None), IsoCode639_1)
            for code in self.config.languages
        ):
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self._revision = f"scrapling@{actual[0]}+crawl4ai@{actual[1]}+lingua@{actual[2]}"
        self._slots = asyncio.Semaphore(self.config.max_workers)

    @property
    def revision(self) -> str:
        return self._revision

    def validate_config(self, config: ChimeraConfig) -> None:
        if config.extraction != self.config:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    async def extract(self, page: Page) -> Extracted:
        if page.content_type.split(";", 1)[0].strip().lower() not in {
            "text/html",
            "application/xhtml+xml",
        }:
            raise ChimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
        if not page.body or len(page.body) > self.config.max_input_bytes:
            raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
        process: asyncio.subprocess.Process | None = None
        tasks: list[asyncio.Task[bytes]] = []
        try:
            async with asyncio.timeout(self.config.timeout_seconds), self._slots:
                private_directory(self.config.work_directory)
                private_directory(self.config.locator_directory)
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "chimera.html_worker",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.config.work_directory,
                    env={
                        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                        "PYTHONNOUSERSITE": "1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "CRAWL4_AI_BASE_DIRECTORY": str(self.config.work_directory),
                        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                        "DO_NOT_TRACK": "1",
                        "HF_HUB_OFFLINE": "1",
                    },
                )
                if process.stdin is None:
                    raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                request = (
                    ExtractionRequest(config=self.config, page=page).model_dump_json().encode()
                )
                tasks = [
                    asyncio.create_task(read_bounded(process.stdout, self.config.max_output_bytes)),
                    asyncio.create_task(
                        read_bounded(process.stderr, self.config.max_diagnostic_bytes)
                    ),
                ]
                process.stdin.write(request)
                await process.stdin.drain()
                process.stdin.close()
                response, _ = await asyncio.gather(*tasks)
                if await process.wait() != 0:
                    raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
                wire = ExtractionResponse.model_validate_json(response)
                if wire.refusal is not None:
                    raise ChimeraRefused(wire.refusal)
                result = wire.result
                if result is None or result.extraction is None:
                    raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                evidence = result.extraction
                if (
                    evidence.source_sha256 != hashlib.sha256(page.body).hexdigest()
                    or evidence.source_url != page.final_url
                    or evidence.text_sha256 != hashlib.sha256(result.text.encode()).hexdigest()
                    or evidence.config_digest != self.config.content_digest()
                    or evidence.parser_revision != self.revision
                    or len(result.text) > self.config.max_text_chars
                    or len(result.links) > self.config.max_links
                ):
                    raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                return result
        except TimeoutError:
            raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED) from None
        except (ValueError, OSError):
            raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED) from None
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
