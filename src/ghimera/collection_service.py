"""Bounded unattended operations over native commands, archives and checkpoints."""

import asyncio
import fcntl
import hashlib
import os
import shutil
import tempfile
import time
import tomllib
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.collector import Collector
from ghimera.command import (
    CommandExecution,
    CommandOptions,
    CredentialBindings,
    assemble_collector,
    execute,
)
from ghimera.config import GhimeraConfig
from ghimera.continuation import CheckpointReceipt
from ghimera.corpus import EvidenceCorpus
from ghimera.corpus_config import CorpusConfig
from ghimera.corpus_search_config import CorpusSearchConfig
from ghimera.corpus_types import CorpusQuery, CorpusReceipt
from ghimera.delivery_config import DeliveryOutboxConfig
from ghimera.delivery_outbox import DeliveryOutbox
from ghimera.embedding import SelfHostedEncoder
from ghimera.embedding_types import EmbeddingReferences
from ghimera.journal import _directory_sync, _private_directory, _read_file, _write_all
from ghimera.models import Record
from ghimera.persistent_collector import PersistentCollection
from ghimera.research_types import ResearchRequest
from ghimera.result_archive import ArchiveReceipt, ResearchResultArchive, bounded_file

Positive = Annotated[int, Field(strict=True, gt=0)]
Phase = Literal[
    "queued",
    "running",
    "pausing",
    "paused",
    "handoff_pending",
    "completed",
    "cancelled",
    "failed",
    "held",
]
Executor = Callable[[CommandOptions], Awaitable[ArchiveReceipt | CheckpointReceipt]]


class CollectionServiceConfig(Record):
    schema_version: Literal["ghimera.collection-service/1"] = Field(alias="schema")
    directory: Path
    command: CommandOptions
    max_jobs: Positive
    max_active_jobs: Positive
    max_manifest_bytes: Positive
    rounds_per_checkpoint: Positive
    handoff_retry_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_handoff_attempts: Positive
    shutdown_grace_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    corpus: CorpusConfig | None = None
    outbox: DeliveryOutboxConfig | None = None

    @model_validator(mode="after")
    def configured(self) -> "CollectionServiceConfig":
        if (
            not self.directory.is_absolute()
            or self.directory == Path("/")
            or ".." in self.directory.parts
        ):
            raise ValueError("service requires an explicit private directory")
        if (
            self.command.human_assistance is not None
            or self.command.schema_version != "ghimera.collector-command/2"
        ):
            raise ValueError(
                "unattended service requires checkpoint command /2 without terminal assistance"
            )
        if self.max_active_jobs > self.max_jobs:
            raise ValueError("active capacity must fit retained job capacity")
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class CollectionJob(Record):
    schema_version: Literal["ghimera.collection-job/1"] = Field(alias="schema")
    run_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    policy_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    recipe_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    phase: Phase
    updated_at: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    checkpoint: CheckpointReceipt | None = None
    archive: ArchiveReceipt | None = None
    corpus: CorpusReceipt | None = None
    delivery_id: str | None = None
    handoff_attempts: Annotated[int, Field(strict=True, ge=0)] = 0
    failure: Literal["execution_failed", "interrupted", "handoff_failed"] | None = None


class CollectionService:
    """One explicit process owner; native research owns all source/model scheduling.

    Clean checkpoints can resume. An interrupted invocation is held for native
    reconciliation; service restart never blindly repeats external work.
    """

    def __init__(
        self,
        config: CollectionServiceConfig,
        *,
        executor: Executor | None = None,
        corpus: EvidenceCorpus | None = None,
        outbox: DeliveryOutbox | None = None,
    ) -> None:
        self.config = CollectionServiceConfig.model_validate(config.model_dump())
        self._executor = executor
        self._provided_executor = executor is not None
        self._corpus, self._outbox = corpus, outbox
        self._owns_corpus = False
        self._jobs: dict[str, CollectionJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._slots = asyncio.Semaphore(config.max_active_jobs)
        self._fd = -1
        self._closing = False
        self._recipe_sha256 = ""
        self._validator: Collector | None = None
        self._recipe: GhimeraConfig | None = None

    def _save(self, job: CollectionJob) -> None:
        ResearchResultArchive._path_check(self.config.directory)
        _private_directory(self.config.directory)
        if self._fd < 0 or os.fstat(self._fd).st_ino != self.config.directory.lstat().st_ino:
            raise ValueError("service storage changed identity")
        data = job.model_dump_json().encode()
        if len(data) > self.config.max_manifest_bytes:
            raise ValueError("service manifest exceeds configured allowance")
        fd, name = tempfile.mkstemp(prefix=".manifest-", dir=self.config.directory)
        staging = Path(name)
        try:
            try:
                _write_all(fd, data)
            finally:
                os.close(fd)
            os.replace(staging, self.config.directory / (job.run_id + ".json"))
            _directory_sync(self.config.directory)
        finally:
            staging.unlink(missing_ok=True)
        self._jobs[job.run_id] = job

    def _update(
        self,
        run_id: str,
        phase: Phase,
        *,
        checkpoint: CheckpointReceipt | None = None,
        archive: ArchiveReceipt | None = None,
        corpus: CorpusReceipt | None = None,
        delivery_id: str | None = None,
        handoff_attempts: int | None = None,
        failure: Literal["execution_failed", "interrupted", "handoff_failed"] | None = None,
    ) -> CollectionJob:
        previous = self.status(run_id)
        job = CollectionJob.model_validate(
            dict(
                previous.model_dump(),
                phase=phase,
                updated_at=time.time(),
                checkpoint=checkpoint or previous.checkpoint,
                archive=archive or previous.archive,
                corpus=corpus or previous.corpus,
                delivery_id=delivery_id or previous.delivery_id,
                handoff_attempts=previous.handoff_attempts
                if handoff_attempts is None
                else handoff_attempts,
                failure=failure,
            )
        )
        self._save(job)
        return job

    async def start(self, *, create: bool = False) -> None:
        if self._fd >= 0:
            raise ValueError("service already started")
        recipe = GhimeraConfig.model_validate(
            tomllib.loads(
                bounded_file(
                    self.config.command.config_path, self.config.command.max_input_bytes
                ).decode()
            )
        )
        self._recipe_sha256 = hashlib.sha256(recipe.model_dump_json().encode()).hexdigest()
        self._recipe = recipe
        if recipe.continuation is None or recipe.journal is None:
            raise ValueError("service requires durable native journal and continuation")
        bindings = (
            CredentialBindings.model_validate_json(
                bounded_file(self.config.command.bindings_path, self.config.command.max_input_bytes)
            )
            if self.config.command.bindings_path
            else CredentialBindings(schema="chimera.command-credentials/1")
        )
        credentials = bindings.resolve()
        credentials.completion_roles(recipe)
        if create:
            ResearchResultArchive._path_check(self.config.directory)
            self.config.directory.mkdir(mode=0o700, exist_ok=False)
            _directory_sync(self.config.directory.parent)
        ResearchResultArchive._path_check(self.config.directory)
        _private_directory(self.config.directory)
        self._fd = os.open(self.config.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self._corpus is None and self.config.corpus is not None:
                cfg = self.config.corpus
                self._corpus = EvidenceCorpus(
                    cfg,
                    encoder=SelfHostedEncoder(cfg.encoder, credential=credentials.encoder),
                    query_encoder=SelfHostedEncoder(
                        cfg.query_encoder, credential=credentials.encoder
                    ),
                )
                self._owns_corpus = True
            if self._corpus is not None:
                if self.config.corpus is None or self._corpus.config != self.config.corpus:
                    raise ValueError("service corpus requires its exact configured binding")
                self._corpus.check_ready()
            if self._outbox is None and self.config.outbox is not None:
                self._outbox = DeliveryOutbox(self.config.outbox)
            if self._outbox is not None:
                if self.config.outbox != self._outbox.config:
                    raise ValueError("service outbox requires its exact configured binding")
                await self._outbox.check_ready()
            if not self._provided_executor:
                references = (
                    EmbeddingReferences.model_validate_json(
                        bounded_file(
                            self.config.command.references_path, self.config.command.max_input_bytes
                        )
                    )
                    if self.config.command.references_path
                    else None
                )
                discovery_corpus = (
                    self._corpus if isinstance(recipe.search, CorpusSearchConfig) else None
                )
                self._validator = assemble_collector(
                    recipe, credentials, references, corpus=discovery_corpus
                )

                async def native(options: CommandOptions) -> ArchiveReceipt | CheckpointReceipt:
                    return await execute(options, corpus=discovery_corpus)

                self._executor = native
            files = tuple(self.config.directory.glob("*.json"))
            if len(files) > self.config.max_jobs:
                raise ValueError("retained jobs exceed configured capacity")
            for path in files:
                job = CollectionJob.model_validate_json(
                    _read_file(path, self.config.max_manifest_bytes)
                )
                if (
                    path.name != job.run_id + ".json"
                    or job.policy_sha256 != self.config.identity
                    or job.recipe_sha256 != self._recipe_sha256
                ):
                    raise ValueError("service job belongs to a different policy")
                self._jobs[job.run_id] = job
                if job.phase in {"running", "pausing"}:
                    self._update(job.run_id, "held", failure="interrupted")
            self._closing = False
            for job in tuple(self._jobs.values()):
                if job.phase in {"queued", "handoff_pending"}:
                    self._launch(job.run_id)
        except BaseException:
            self._release()
            raise

    def _release(self) -> None:
        if self._owns_corpus and self._corpus is not None:
            self._corpus.close()
            self._corpus = None
            self._owns_corpus = False
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def status(self, run_id: str) -> CollectionJob:
        if run_id not in self._jobs:
            raise ValueError("unknown collection job")
        return self._jobs[run_id]

    def health(self) -> dict[str, object]:
        return {
            "schema": "ghimera.collection-health/1",
            "policy_sha256": self.config.identity,
            "phase": "stopped" if self._fd < 0 else "stopping" if self._closing else "running",
            "jobs": len(self._jobs),
            "active": sum(job.phase in {"running", "pausing"} for job in self._jobs.values()),
            "tasks": sum(not task.done() for task in self._tasks.values()),
            "active_capacity": self.config.max_active_jobs,
            "capacity": self.config.max_jobs,
            "retained_archive_bytes": sum(
                job.archive.result_bytes for job in self._jobs.values() if job.archive
            ),
            "retained_checkpoint_bytes": sum(
                job.checkpoint.size_bytes for job in self._jobs.values() if job.checkpoint
            ),
            "disk_free_bytes": shutil.disk_usage(self.config.directory).free
            if self._fd >= 0
            else None,
            "states": {
                phase: sum(job.phase == phase for job in self._jobs.values())
                for phase in (
                    "queued",
                    "running",
                    "paused",
                    "handoff_pending",
                    "held",
                    "completed",
                    "failed",
                    "cancelled",
                )
            },
        }

    def manifest(self) -> dict[str, object]:
        return {
            "schema": "ghimera.collection-manifest/1",
            "service": self.config.model_dump(mode="json"),
            "collector": self._recipe.model_dump(mode="json") if self._recipe is not None else None,
        }

    def submit(self, request: ResearchRequest) -> CollectionJob:
        if self._fd < 0 or self._closing or len(self._jobs) >= self.config.max_jobs:
            raise ValueError("service unavailable or retained capacity exhausted")
        request = ResearchRequest.model_validate(request.model_dump())
        if self._validator is not None:
            request = self._validator.validate_request(request)
        # HTTP callers cannot select local filesystem inputs or credential/session paths.
        if request.local_documents:
            raise ValueError("service accepts network research requests only")
        run_id = uuid.uuid4().hex
        request_data = request.model_dump_json().encode()
        if len(request_data) > self.config.command.max_input_bytes:
            raise ValueError("request exceeds configured allowance")
        job = CollectionJob(
            schema="ghimera.collection-job/1",
            run_id=run_id,
            policy_sha256=self.config.identity,
            recipe_sha256=self._recipe_sha256,
            phase="queued",
            updated_at=time.time(),
        )
        self._save(job)
        # The durable bounded admission precedes request bytes. A crash here
        # retains a recoverable queued/failed identity, never an orphan payload
        # outside the job-capacity allowance.
        path = self.config.directory / (run_id + ".request")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            _write_all(fd, request_data)
        finally:
            os.close(fd)
        _directory_sync(self.config.directory)
        self._launch(run_id)
        return job

    def _launch(self, run_id: str) -> None:
        if self._fd < 0 or self._closing:
            raise ValueError("service is not accepting work")
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            raise ValueError("job already active")
        self._tasks[run_id] = asyncio.create_task(self._run(run_id))

    def pause(self, run_id: str) -> CollectionJob:
        job = self.status(run_id)
        if job.phase == "queued":
            return self._update(run_id, "paused")
        if job.phase != "running":
            raise ValueError("only eligible work may pause")
        return self._update(run_id, "pausing")

    def resume(self, run_id: str) -> CollectionJob:
        if self.status(run_id).phase not in {"paused", "handoff_pending"}:
            raise ValueError("resume requires a clean pause or archive handoff")
        job = self._update(
            run_id,
            "queued" if self.status(run_id).archive is None else "handoff_pending",
            handoff_attempts=0,
        )
        task = self._tasks.get(run_id)
        if task is None or task.done():
            self._launch(run_id)
        return job

    async def query(self, text: str, *, top_k: int, languages: tuple[str, ...] = ()) -> CorpusQuery:
        if self._fd < 0 or self._closing or self._corpus is None:
            raise ValueError("service corpus is unavailable")
        return await self._corpus.search(text, top_k=top_k, languages=languages)

    async def cancel(self, run_id: str) -> CollectionJob:
        job = self.status(run_id)
        if job.phase in {"completed", "cancelled"}:
            raise ValueError("terminal work cannot cancel")
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self._update(run_id, "cancelled")

    async def _handoff(self, run_id: str) -> None:
        job = self.status(run_id)
        if job.archive is None:
            raise ValueError("handoff requires completed native archive")
        result = ResearchResultArchive.read(
            self.config.directory / (run_id + ".output"),
            max_bytes=self.config.command.max_result_bytes,
        )
        data = result.model_dump_json().encode()
        if (
            job.archive.run_id != run_id
            or job.archive.result_bytes != len(data)
            or job.archive.result_sha256 != hashlib.sha256(data).hexdigest()
        ):
            raise ValueError("archive differs from the service's durable result receipt")
        delivery_result = result
        if self._corpus is not None:
            receipt = job.corpus
            if receipt is None:
                receipt = await self._corpus.append(result.harvest)
                # Save the exact corpus ACK before queue admission: restart must
                # reconstruct the same delivery identity after a lost enqueue ACK.
                self._update(run_id, "handoff_pending", corpus=receipt)
            persistent = PersistentCollection(
                schema="ghimera.persistent-collection/1", result=result, corpus=receipt
            )
            if self._outbox is not None:
                delivered = await self._outbox.enqueue(persistent)
                self._update(run_id, "completed", delivery_id=delivered.delivery_id)
                return
        if self._outbox is not None:
            delivered = await self._outbox.enqueue(delivery_result)
            self._update(run_id, "completed", delivery_id=delivered.delivery_id)
        else:
            self._update(run_id, "completed")

    async def _retry_handoff(self, run_id: str) -> None:
        while not self._closing:
            job = self.status(run_id)
            if job.handoff_attempts >= self.config.max_handoff_attempts:
                return
            self._update(run_id, "handoff_pending", handoff_attempts=job.handoff_attempts + 1)
            try:
                await self._handoff(run_id)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                self._update(run_id, "handoff_pending", failure="handoff_failed")
                if self.status(run_id).handoff_attempts >= self.config.max_handoff_attempts:
                    return
                await asyncio.sleep(self.config.handoff_retry_seconds)

    async def _run(self, run_id: str) -> None:
        try:
            async with self._slots:
                job = self.status(run_id)
                if job.phase == "paused":
                    return
                if job.archive is not None:
                    await self._retry_handoff(run_id)
                    return
                self._update(run_id, "running")
                while True:
                    job = self.status(run_id)
                    recipe = GhimeraConfig.model_validate(
                        tomllib.loads(
                            bounded_file(
                                self.config.command.config_path, self.config.command.max_input_bytes
                            ).decode()
                        )
                    )
                    if (
                        hashlib.sha256(recipe.model_dump_json().encode()).hexdigest()
                        != job.recipe_sha256
                    ):
                        raise ValueError("service recipe changed after admission")
                    operation: Literal["run", "resume"] = (
                        "resume" if job.checkpoint is not None else "run"
                    )
                    options = CommandOptions.model_validate(
                        dict(
                            self.config.command.model_dump(),
                            run_id=run_id,
                            output_directory=self.config.directory / (run_id + ".output"),
                            request_path=None
                            if operation == "resume"
                            else self.config.directory / (run_id + ".request"),
                            execution=CommandExecution(
                                schema="ghimera.command-execution/1",
                                operation=operation,
                                checkpoint_sha256=job.checkpoint.sha256 if job.checkpoint else None,
                                suspend_after_rounds=self.config.rounds_per_checkpoint,
                            ),
                        )
                    )
                    if self._executor is None:
                        raise ValueError("service not started")
                    receipt = await self._executor(options)
                    if isinstance(receipt, CheckpointReceipt):
                        phase: Phase = (
                            "paused"
                            if self._closing or self.status(run_id).phase == "pausing"
                            else "running"
                        )
                        self._update(run_id, phase, checkpoint=receipt)
                        if phase == "paused":
                            return
                    else:
                        self._update(run_id, "handoff_pending", archive=receipt)
                        await self._retry_handoff(run_id)
                        return
        except asyncio.CancelledError:
            job = self.status(run_id)
            interrupted_phase: Phase = (
                "handoff_pending"
                if job.archive is not None
                else "queued"
                if job.phase == "queued"
                else "held"
            )
            self._update(run_id, interrupted_phase, failure="interrupted")
            raise
        except Exception:
            archived = self.status(run_id).archive is not None
            self._update(
                run_id,
                "handoff_pending" if archived else "failed",
                failure="handoff_failed" if archived else "execution_failed",
            )

    async def stop(self) -> None:
        self._closing = True
        tasks = tuple(task for task in self._tasks.values() if not task.done())
        try:
            if tasks:
                done, pending = await asyncio.wait(
                    tasks, timeout=self.config.shutdown_grace_seconds
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*done, *pending, return_exceptions=True)
        finally:
            self._release()
