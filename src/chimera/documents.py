"""Offline binary-document adapter; fetching remains the ladder's responsibility."""

import hashlib
import os
from pathlib import Path

from chimera.config import ChimeraConfig
from chimera.document_config import DocumentExtractionConfig
from chimera.extraction import ExtractionResponse
from chimera.models import Extracted, Page, Record
from chimera.passive_worker import PassiveWorker
from chimera.ports import Extractor
from chimera.refusals import ChimeraRefused, RefusalCode

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class DocumentRequest(Record):
    config: DocumentExtractionConfig
    page: Page


def check_artifacts(config: DocumentExtractionConfig) -> None:
    root = config.artifacts_directory
    if root is None:
        return
    if not root.is_dir() or root.is_symlink():
        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    expected = {artifact.path for artifact in config.artifacts}
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != expected or any(path.is_symlink() for path in root.rglob("*")):
        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    for artifact in config.artifacts:
        path = root / artifact.path
        if path.stat().st_size != artifact.size_bytes:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                digest.update(chunk)
        if digest.hexdigest() != artifact.sha256:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)


class DocumentExtractor:
    revision = "docling@2.134.0+docling-core@2.99.0+lingua@2.1.1"

    def __init__(self, config: ChimeraConfig) -> None:
        if config.document_extraction is None:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self.config = config.document_extraction
        if not self.config.worker_python.is_file() or not os.access(
            self.config.worker_python, os.X_OK
        ):
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        check_artifacts(self.config)
        self._worker = PassiveWorker(
            interpreter=self.config.worker_python,
            module="chimera.document_worker",
            work_directory=self.config.work_directory,
            max_workers=self.config.max_workers,
            timeout_seconds=self.config.timeout_seconds,
            max_output_bytes=self.config.max_output_bytes,
            max_diagnostic_bytes=self.config.max_diagnostic_bytes,
            cleanup_timeout_seconds=self.config.cleanup_timeout_seconds,
            environment={
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "HF_HOME": str(self.config.work_directory / "hf-offline"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "DO_NOT_TRACK": "1",
                "OMP_NUM_THREADS": str(self.config.cpu_threads),
                "OPENBLAS_NUM_THREADS": str(self.config.cpu_threads),
                "CUDA_VISIBLE_DEVICES": "",
            },
        )

    def validate_config(self, config: ChimeraConfig) -> None:
        if config.document_extraction != self.config:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    async def extract(self, page: Page) -> Extracted:
        mime = page.content_type.split(";", 1)[0].strip().lower()
        if mime not in {"application/pdf", DOCX_TYPE}:
            raise ChimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
        if not page.body or len(page.body) > self.config.max_input_bytes:
            raise ChimeraRefused(RefusalCode.EXTRACTION_FAILED)
        request = DocumentRequest(config=self.config, page=page).model_dump_json().encode()
        response = await self._worker.run(request)
        try:
            wire = ExtractionResponse.model_validate_json(response)
        except ValueError:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        if wire.refusal is not None:
            raise ChimeraRefused(wire.refusal)
        result = wire.result
        if result is None or result.document_parse is None or result.document_layout is None:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        evidence = result.document_parse
        pipeline = "docx" if mime == DOCX_TYPE else self.config.pdf_pipeline
        if (
            evidence.source_sha256 != hashlib.sha256(page.body).hexdigest()
            or evidence.source_url != page.final_url
            or evidence.config_digest != self.config.content_digest()
            or evidence.parser_revision != self.revision
            or evidence.pipeline != pipeline
            or evidence.artifact_manifest_digest
            != (self.config.artifact_digest() if pipeline == "standard" else None)
            or len(result.text) > self.config.max_text_chars
            or len(result.links) > self.config.max_links
            or evidence.page_count > self.config.max_pages
        ):
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        return result


class DocumentExtractionSuite:
    """MIME dispatch over interchangeable extractors, without fetching or fallback I/O."""

    def __init__(self, *, html: Extractor, documents: DocumentExtractor) -> None:
        self._html, self._documents = html, documents

    @property
    def revision(self) -> str:
        return f"{self._html.revision}+{self._documents.revision}"

    def validate_config(self, config: ChimeraConfig) -> None:
        self._html.validate_config(config)
        self._documents.validate_config(config)

    async def extract(self, page: Page) -> Extracted:
        mime = page.content_type.split(";", 1)[0].strip().lower()
        if mime in {"text/html", "application/xhtml+xml"}:
            return await self._html.extract(page)
        return await self._documents.extract(page)
