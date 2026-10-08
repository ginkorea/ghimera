"""Native child adapter; optional runtime, no credentials or remote fallback."""

import json
import os

from ghimera.passive_worker import PassiveWorker
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.reranking_config import OfflineRerankingConfig
from ghimera.reranking_types import RerankRequest, RerankScores
from ghimera.reranking_worker import WorkerRequest, check_artifacts


class OfflineCrossEncoder:
    def __init__(self, config: OfflineRerankingConfig) -> None:
        self._config = OfflineRerankingConfig.model_validate(config.model_dump())
        if not self.config.worker_python.is_file() or not os.access(
            self.config.worker_python, os.X_OK
        ):
            raise ValueError("offline reranking requires its declared executable interpreter")
        check_artifacts(self.config)
        self._worker = PassiveWorker(
            interpreter=self.config.worker_python,
            module="ghimera.reranking_worker",
            work_directory=self.config.work_directory,
            max_workers=self.config.max_workers,
            timeout_seconds=self.config.timeout_seconds,
            max_output_bytes=self.config.max_response_bytes,
            max_diagnostic_bytes=self.config.max_diagnostic_bytes,
            cleanup_timeout_seconds=self.config.cleanup_timeout_seconds,
            environment={
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "HF_HOME": str(self.config.work_directory / "hf-offline"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
                "DO_NOT_TRACK": "1",
                "TOKENIZERS_PARALLELISM": "false",
                "CUDA_VISIBLE_DEVICES": "",
                "OMP_NUM_THREADS": str(self.config.cpu_threads),
                "OPENBLAS_NUM_THREADS": str(self.config.cpu_threads),
                "GHIMERA_RERANK_REQUEST_BYTES": str(self.config.max_request_bytes),
            },
        )

    @property
    def config(self) -> OfflineRerankingConfig:
        return self._config

    async def _call(self, envelope: WorkerRequest) -> bytes:
        payload = envelope.model_dump_json().encode()
        if len(payload) > self.config.max_request_bytes:
            raise ValueError("offline reranking input exceeds its declared byte bound")
        try:
            response = await self._worker.run(payload)
        except GhimeraRefused as exc:
            if exc.code == RefusalCode.EXTRACTION_FAILED:
                raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE) from None
            raise
        if len(response) > self.config.max_response_bytes:
            raise ValueError("offline reranking output exceeds its declared byte bound")
        return response

    async def prepare(self) -> None:
        # Do not reuse admission of mutable local artifact/runtime paths.
        response = await self._call(
            WorkerRequest(action="prepare", policy=self.config, request=None)
        )
        if json.loads(response) != {"ready": self.config.identity}:
            raise ValueError("offline worker admission does not bind its exact recipe")

    async def score(self, request: RerankRequest) -> RerankScores:
        request = RerankRequest.model_validate(request.model_dump())
        if request.policy != self.config:
            raise ValueError("offline reranker must share the exact request policy")
        if not request.candidates:
            return RerankScores(
                schema="ghimera.rerank-scores/1", request_sha256=request.sha256, scores=()
            )
        response = await self._call(
            WorkerRequest(action="score", policy=self.config, request=request)
        )
        result = RerankScores.model_validate_json(response)
        result.validate_request(request)
        return result
