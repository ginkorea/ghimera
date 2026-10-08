"""Pinned local Tesseract, bounded TSV regions, no hidden language downloads."""

import asyncio
import csv
import hashlib
import io
import os
from pathlib import Path
from typing import Protocol

from ghimera.image_worker import DecodedImage, DecodeRequest
from ghimera.passive_worker import PassiveWorker, read_bounded
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.visual_config import VisualConfig
from ghimera.visual_types import ImageRegion, OcrResult, OcrSpan


class ImageOcr(Protocol):
    @property
    def config(self) -> VisualConfig: ...

    async def read(self, raw: bytes, *, language_hint: str | None = None) -> OcrResult: ...


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


class TesseractOcr:
    def __init__(self, config: VisualConfig) -> None:
        self._config = config
        if not config.tesseract.is_file() or not os.access(config.tesseract, os.X_OK):
            raise ValueError("configured OCR executable is unavailable")
        if not config.worker_python.is_file() or not os.access(config.worker_python, os.X_OK):
            raise ValueError("configured raster interpreter is unavailable")
        for language in config.languages:
            if not (config.tessdata_directory / (language + ".traineddata")).is_file():
                raise ValueError(f"configured OCR language is unavailable: {language}")
        self._engine = "tesseract:sha256:" + file_digest(config.tesseract)
        self._languages = {
            lang: file_digest(config.tessdata_directory / (lang + ".traineddata"))
            for lang in config.languages
        }
        self._slots = asyncio.Semaphore(config.max_workers)
        self._worker = PassiveWorker(
            interpreter=config.worker_python,
            module="ghimera.image_worker",
            work_directory=config.work_directory,
            max_workers=config.max_workers,
            timeout_seconds=config.timeout_seconds,
            max_output_bytes=config.max_output_bytes,
            max_diagnostic_bytes=config.max_diagnostic_bytes,
            cleanup_timeout_seconds=config.cleanup_timeout_seconds,
            environment={
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

    @property
    def config(self) -> VisualConfig:
        return self._config

    async def read(self, raw: bytes, *, language_hint: str | None = None) -> OcrResult:
        config = self.config
        selected = config.language_routes.get(language_hint or "", config.fallback_languages)
        # Refuse changed executable/language bytes rather than mislabel provenance.
        if file_digest(config.tesseract) != self._engine.rsplit(":", 1)[1] or any(
            file_digest(config.tessdata_directory / (lang + ".traineddata")) != digest
            for lang, digest in self._languages.items()
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        async with asyncio.timeout(config.timeout_seconds), self._slots:
            response = await self._worker.run(
                DecodeRequest(config=config, raw=raw).model_dump_json().encode()
            )
            image = DecodedImage.model_validate_json(response)
            if image.image_sha256 != hashlib.sha256(raw).hexdigest():
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            output = await self._run(image.png, selected)
        spans: list[OcrSpan] = []
        chars = 0
        try:
            reader = csv.DictReader(io.StringIO(output.decode("utf-8")), delimiter="\t")
            if not {"level", "left", "top", "width", "height", "conf", "text"} <= set(
                reader.fieldnames or ()
            ):
                raise ValueError("OCR did not emit the required TSV format")
            for row in reader:
                if row["level"] != "5" or not row["text"].strip():
                    continue
                confidence = float(row["conf"])
                if confidence < config.min_word_confidence:
                    continue
                left, top, width, height = (
                    int(row[key]) for key in ("left", "top", "width", "height")
                )
                if width <= 0 or height <= 0:
                    continue
                span = OcrSpan(
                    text=row["text"],
                    confidence=confidence,
                    region=ImageRegion(
                        left=left / image.width,
                        top=top / image.height,
                        right=(left + width) / image.width,
                        bottom=(top + height) / image.height,
                    ),
                )
                chars += len(span.text) + 1
                if len(spans) >= config.max_ocr_spans or chars > config.max_ocr_chars:
                    raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
                spans.append(span)
        except (ValueError, KeyError, UnicodeError):
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED) from None
        return OcrResult(
            image_sha256=image.image_sha256,
            width=image.width,
            height=image.height,
            media_type=image.media_type,
            spans=tuple(spans),
            engine_revision=self._engine,
            language_pack_sha256={lang: self._languages[lang] for lang in selected},
        )

    async def _run(self, png: bytes, languages: tuple[str, ...]) -> bytes:
        config = self.config
        process: asyncio.subprocess.Process | None = None
        start = asyncio.create_task(
            asyncio.create_subprocess_exec(
                str(config.tesseract),
                "stdin",
                "stdout",
                "--tessdata-dir",
                str(config.tessdata_directory),
                "-l",
                "+".join(languages),
                "--psm",
                str(config.page_segmentation),
                "-c",
                "tessedit_create_tsv=1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"LANG": "C.UTF-8", "OMP_THREAD_LIMIT": str(config.cpu_threads)},
            )
        )
        readers: list[asyncio.Task[bytes]] = []
        try:
            process = await asyncio.shield(start)
            readers = [
                asyncio.create_task(read_bounded(process.stdout, config.max_output_bytes)),
                asyncio.create_task(read_bounded(process.stderr, config.max_diagnostic_bytes)),
            ]
            if process.stdin is None:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            process.stdin.write(png)
            await process.stdin.drain()
            process.stdin.close()
            output, _ = await asyncio.gather(*readers)
            if await process.wait() != 0:
                raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
            return output
        finally:
            if process is None:
                async with asyncio.timeout(config.cleanup_timeout_seconds):
                    process = await asyncio.shield(start)
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.transport.abort()
            if process.returncode is None:
                process.kill()
            async with asyncio.timeout(config.cleanup_timeout_seconds):
                await process.wait()
