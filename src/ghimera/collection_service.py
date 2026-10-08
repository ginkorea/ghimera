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
from ghimera.graph import DirectoryGraphSink, ResearchGraph
from ghimera.journal import (
    DirectoryLedgerSink,
    _directory_sync,
    _private_directory,
    _read_file,
    _run_path,
    _write_all,
    read_journal,
)
from ghimera.journal_types import JournalDocument, JournalRetainedDocument
from ghimera.model_reconciliation import unreconciled_model_sequences
from ghimera.model_reconciliation_types import (
    ModelAttemptAuthorization,
    ModelReconciliationDecision,
    ModelUnknownObservation,
)
from ghimera.models import Record
from ghimera.persistent_collector import PersistentCollection
from ghimera.research_recovery_store import ResearchRecoveryStore
from ghimera.research_recovery_types import ResearchRecoveryModels
from ghimera.research_types import ResearchRequest
from ghimera.result_archive import (
    ArchiveReceipt,
    ArchiveReservation,
    ResearchResultArchive,
    bounded_file,
)
from ghimera.source_work import SourceWorkStore, read_source_work

Positive = Annotated[int, Field(strict=True, gt=0)]
Phase = Literal[
    "queued",
    "running",
    "recovering",
    "pausing",
    "paused",
    "handoff_pending",
    "completed",
    "cancelled",
    "failed",
    "held",
]
Executor = Callable[
    [CommandOptions],
    Awaitable[
        ArchiveReceipt | CheckpointReceipt | ModelUnknownObservation | ModelAttemptAuthorization
    ],
]
Failure = Literal["execution_failed", "interrupted", "handoff_failed", "recovery_failed"]
Hold = Literal[
    "policy_changed",
    "recipe_changed",
    "not_admissible",
    "attempts_exhausted",
    "manual_required",
    "recovery_failed",
]


class ServiceRecoveryPolicy(Record):
    """Service admission only; native research owns recovery and all run bounds."""

    schema_version: Literal[
        "ghimera.service-recovery/1", "ghimera.service-recovery/2", "ghimera.service-recovery/3"
    ] = Field(alias="schema")
    on_restart: Literal["hold", "adopt_acknowledged"]
    max_adoption_attempts: Positive
    boundary: Literal["source_completion"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    model_reconciliation: Literal["caller_only"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def versioned(self) -> "ServiceRecoveryPolicy":
        if self.schema_version == "ghimera.service-recovery/3":
            if self.model_reconciliation != "caller_only" or self.boundary is not None:
                raise ValueError("service /3 requires explicit caller-only model reconciliation")
            return self
        if self.model_reconciliation is not None:
            raise ValueError("caller-only model reconciliation requires service recovery /3")
        if (self.schema_version == "ghimera.service-recovery/2") != (self.boundary is not None):
            raise ValueError("source service recovery requires /2 and explicit boundary")
        return self


class CollectionServiceConfig(Record):
    schema_version: Literal["ghimera.collection-service/1", "ghimera.collection-service/2"] = Field(
        alias="schema"
    )
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
    recovery: ServiceRecoveryPolicy | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def configured(self) -> "CollectionServiceConfig":
        if (self.schema_version == "ghimera.collection-service/2") != (self.recovery is not None):
            raise ValueError("service /2 requires explicit recovery policy; /1 forbids it")
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
    schema_version: Literal[
        "ghimera.collection-job/1",
        "ghimera.collection-job/2",
        "ghimera.collection-job/3",
        "ghimera.collection-job/4",
    ] = Field(alias="schema")
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
    failure: Failure | None = None
    request_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    snapshot_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    adoption_attempts: Annotated[int, Field(strict=True, ge=0)] = Field(
        default=0, exclude_if=lambda v: v == 0
    )
    recovery_hold: Hold | None = Field(default=None, exclude_if=lambda v: v is None)
    recovery_boundary: Literal["source_completion"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    model_attempt: ModelAttemptAuthorization | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def recovery_binding(self) -> "CollectionJob":
        if (self.schema_version == "ghimera.collection-job/4") != (self.model_attempt is not None):
            raise ValueError("caller attempt requires explicit job /4")
        if self.model_attempt is not None and self.model_attempt.run_id != self.run_id:
            raise ValueError("service attempt lost its original run or snapshot")
        if (self.schema_version == "ghimera.collection-job/3") != (
            self.recovery_boundary is not None
        ):
            raise ValueError("source jobs require explicit versioned boundary")
        if self.schema_version == "ghimera.collection-job/1":
            if (
                self.request_sha256
                or self.snapshot_sha256
                or self.adoption_attempts
                or self.recovery_hold
                or self.phase == "recovering"
            ):
                raise ValueError("legacy jobs cannot acquire recovery state")
        elif self.request_sha256 is None:
            raise ValueError("recovery jobs retain their original request digest")
        if self.adoption_attempts and self.snapshot_sha256 is None:
            raise ValueError("adoption attempts require a durable snapshot digest")
        if self.phase == "recovering" and not self.adoption_attempts:
            raise ValueError("recovery launch requires a durable attempt reservation")
        return self


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
        failure: Failure | None = None,
        snapshot_sha256: str | None = None,
        adoption_attempts: int | None = None,
        recovery_hold: Hold | None = None,
        model_attempt: ModelAttemptAuthorization | None = None,
    ) -> CollectionJob:
        previous = self.status(run_id)
        job = CollectionJob.model_validate(
            dict(
                previous.model_dump(),
                schema="ghimera.collection-job/4"
                if model_attempt is not None
                else previous.schema_version,
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
                snapshot_sha256=snapshot_sha256 or previous.snapshot_sha256,
                adoption_attempts=previous.adoption_attempts
                if adoption_attempts is None
                else adoption_attempts,
                recovery_hold=recovery_hold,
                model_attempt=model_attempt or previous.model_attempt,
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
        if self.config.recovery is not None and recipe.research_recovery is None:
            raise ValueError("service recovery requires the native research recovery policy")
        if self.config.recovery is not None and self.config.recovery.boundary is not None:
            if (
                recipe.research_recovery is None
                or recipe.research_recovery.source_completion is None
            ):
                raise ValueError("source service adoption requires native source-completion policy")
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
            if (
                self.config.recovery is not None
                and self.config.recovery.model_reconciliation is not None
                and (
                    recipe.research_recovery is None
                    or recipe.research_recovery.model_reconciliation is None
                )
            ):
                raise ValueError(
                    "caller service route requires original model reconciliation policy"
                )
            if not self._provided_executor or self.config.recovery is not None:
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

                async def native(
                    options: CommandOptions,
                ) -> (
                    ArchiveReceipt
                    | CheckpointReceipt
                    | ModelUnknownObservation
                    | ModelAttemptAuthorization
                ):
                    return await execute(options, corpus=discovery_corpus)

                if not self._provided_executor:
                    self._executor = native
            files = tuple(self.config.directory.glob("*.json"))
            if len(files) > self.config.max_jobs:
                raise ValueError("retained jobs exceed configured capacity")
            for path in files:
                job = CollectionJob.model_validate_json(
                    _read_file(path, self.config.max_manifest_bytes)
                )
                if path.name != job.run_id + ".json":
                    raise ValueError("service job filename differs from its identity")
                mismatch: Hold | None = (
                    "policy_changed"
                    if job.policy_sha256 != self.config.identity
                    else "recipe_changed"
                    if job.recipe_sha256 != self._recipe_sha256
                    else None
                )
                if mismatch is not None and (
                    self.config.recovery is None
                    or job.schema_version
                    not in {
                        "ghimera.collection-job/2",
                        "ghimera.collection-job/3",
                        "ghimera.collection-job/4",
                    }
                ):
                    raise ValueError("service job belongs to a different policy")
                self._jobs[job.run_id] = job
                if mismatch is not None:
                    # Never reset terminal failures or accept a changed admission policy.
                    phase: Phase = (
                        job.phase if job.phase in {"completed", "cancelled", "failed"} else "held"
                    )
                    self._update(job.run_id, phase, failure=job.failure, recovery_hold=mismatch)
                elif job.phase in {"running", "pausing", "recovering"}:
                    self._update(job.run_id, "held", failure="interrupted")
            self._closing = False
            for job in tuple(self._jobs.values()):
                if job.phase in {"queued", "handoff_pending"}:
                    self._launch(job.run_id)
                elif (
                    job.phase == "held"
                    and job.failure == "interrupted"
                    and self.config.recovery is not None
                    and job.recovery_hold not in {"policy_changed", "recipe_changed"}
                ):
                    if await self._adopt_completed_output(job):
                        continue
                    if self.config.recovery.on_restart == "adopt_acknowledged":
                        await self.recover(job.run_id)
                    else:
                        self._update(
                            job.run_id,
                            "held",
                            failure="interrupted",
                            recovery_hold="manual_required",
                        )
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
            "active": sum(
                job.phase in {"running", "pausing", "recovering"} for job in self._jobs.values()
            ),
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
                + (("recovering",) if self.config.recovery is not None else ())
            },
            **(
                {
                    "adoption_attempts": sum(job.adoption_attempts for job in self._jobs.values()),
                    "recovery_holds": {
                        reason: sum(job.recovery_hold == reason for job in self._jobs.values())
                        for reason in (
                            "policy_changed",
                            "recipe_changed",
                            "not_admissible",
                            "attempts_exhausted",
                            "manual_required",
                            "recovery_failed",
                        )
                    },
                }
                if self.config.recovery is not None
                else {}
            ),
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
            schema="ghimera.collection-job/3"
            if self.config.recovery is not None and self.config.recovery.boundary is not None
            else "ghimera.collection-job/2"
            if self.config.recovery is not None
            else "ghimera.collection-job/1",
            run_id=run_id,
            policy_sha256=self.config.identity,
            recipe_sha256=self._recipe_sha256,
            phase="queued",
            updated_at=time.time(),
            request_sha256=request.content_digest() if self.config.recovery is not None else None,
            recovery_boundary=self.config.recovery.boundary
            if self.config.recovery is not None
            else None,
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

    async def recover(
        self, run_id: str, *, attempt: ModelAttemptAuthorization | None = None
    ) -> CollectionJob:
        """Admit only native evidence; no reconciliation, fresh run or failed-outcome reset."""
        job, policy = self.status(run_id), self.config.recovery
        task = self._tasks.get(run_id)
        if (
            self._fd < 0
            or self._closing
            or policy is None
            or job.phase != "held"
            or job.failure != "interrupted"
            or job.archive is not None
            or (task is not None and not task.done())
        ):
            raise ValueError("recovery requires an inactive interrupted original job")
        if job.policy_sha256 != self.config.identity or job.recipe_sha256 != self._recipe_sha256:
            return self._update(
                run_id, "held", failure="interrupted", recovery_hold="policy_changed"
            )
        if attempt is not None and (
            policy.model_reconciliation != "caller_only"
            or self._validator is None
            or attempt not in self._validator.model_attempt_history(run_id)
            or job.model_attempt is not None
            and attempt != job.model_attempt
        ):
            raise ValueError("caller recovery requires the original durable service attempt")
        if await self._adopt_completed_output(job):
            return self.status(run_id)
        self._admission_unchanged(job)
        if job.adoption_attempts >= policy.max_adoption_attempts:
            return self._update(
                run_id, "held", failure="interrupted", recovery_hold="attempts_exhausted"
            )
        try:
            recipe = GhimeraConfig.model_validate(
                tomllib.loads(
                    bounded_file(
                        self.config.command.config_path, self.config.command.max_input_bytes
                    ).decode()
                )
            )
            if hashlib.sha256(recipe.model_dump_json().encode()).hexdigest() != job.recipe_sha256:
                raise ValueError("recipe changed")
            if recipe.journal is None or recipe.research_recovery is None:
                raise ValueError("native recovery policy unavailable")
            request = ResearchRequest.model_validate_json(
                _read_file(
                    self.config.directory / (run_id + ".request"),
                    self.config.command.max_input_bytes,
                )
            )
            if request.content_digest() != job.request_sha256:
                raise ValueError("original request changed")
            if job.recovery_boundary == "source_completion":
                source_saved = SourceWorkStore.completion(
                    recipe,
                    run_id,
                    expected_request=request,
                    expected_models=self._validator.recovery_models()
                    if self._validator is not None
                    else None,
                    expected_runtime=self._validator.source_runtime()
                    if self._validator is not None
                    else None,
                )
                pin = source_saved.sha256
                harvest, journal_rows = (
                    source_saved.snapshot.progress.harvest,
                    source_saved.journal.rows,
                )
            else:
                pin = hashlib.sha256(
                    _read_file(
                        _run_path(recipe.journal, run_id) / "research-control.json",
                        recipe.research_recovery.max_snapshot_bytes,
                    )
                ).hexdigest()
                saved = ResearchRecoveryStore(recipe, run_id, recipe.research_recovery).read(
                    pin,
                    expected_request=request,
                    expected_models=self._validator.recovery_models()
                    if self._validator is not None
                    else None,
                    attempt=attempt,
                )
                # Service adoption requires an actual retained ACK, not a no-call snapshot.
                if saved.intent_sequence is None and saved.attempt is None:
                    raise ValueError("no retained original model return")
                harvest, journal_rows = saved.snapshot.progress.harvest, saved.journal.rows
            archive = ResearchResultArchive.resume(
                self.config.directory / (run_id + ".output"),
                reservation=ArchiveReservation(
                    schema="ghimera.command-output/1",
                    run_id=run_id,
                    config_sha256=job.recipe_sha256,
                    request_sha256=request.content_digest(),
                ),
                max_bytes=self.config.command.max_input_bytes,
            )
            try:
                ledger = DirectoryLedgerSink(
                    recipe,
                    run_id,
                    harvest.goal,
                    harvest.receipt.judge,
                    resume_rows=journal_rows,
                )
                try:
                    if recipe.source_work is not None:
                        source = SourceWorkStore.resume(recipe, run_id, len(journal_rows))
                        source.close()
                    if recipe.graph is not None and recipe.graph.enabled:
                        if harvest.graph is None:
                            raise ValueError("recovery lost its original graph snapshot")
                        graph = ResearchGraph(
                            recipe.graph, run_id, DirectoryGraphSink(recipe.graph, run_id)
                        )
                        await graph.start(harvest.goal.text, expected=harvest.graph)
                finally:
                    ledger.close()
            finally:
                archive.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._admission_unchanged(job)
            return self._update(
                run_id, "held", failure="interrupted", recovery_hold="not_admissible"
            )
        self._admission_unchanged(job)
        # Durable before launch, including a crash between this save and create_task.
        admitted = self._update(
            run_id,
            "recovering",
            snapshot_sha256=pin,
            adoption_attempts=job.adoption_attempts + 1,
            model_attempt=attempt,
        )
        self._launch(run_id)
        return admitted

    def _caller_options(
        self,
        job: CollectionJob,
        operation: Literal["observe_model_unknown", "reconcile_model"],
        pin: str,
        decision: ModelReconciliationDecision | None = None,
    ) -> CommandOptions:
        task, policy = self._tasks.get(job.run_id), self.config.recovery
        if (
            self._fd < 0
            or self._closing
            or policy is None
            or policy.model_reconciliation != "caller_only"
            or job.phase != "held"
            or job.failure != "interrupted"
            or job.archive is not None
            or job.policy_sha256 != self.config.identity
            or job.recipe_sha256 != self._recipe_sha256
            or job.adoption_attempts >= policy.max_adoption_attempts
            or task is not None
            and not task.done()
        ):
            raise ValueError("caller decision requires an inactive original interrupted job")
        return CommandOptions.model_validate(
            dict(
                self.config.command.model_dump(),
                schema="ghimera.collector-command/5",
                run_id=job.run_id,
                request_path=None,
                output_directory=self.config.directory / (job.run_id + ".output"),
                execution=CommandExecution(
                    schema="ghimera.command-execution/4",
                    operation=operation,
                    snapshot_sha256=pin,
                    model_decision=decision,
                ),
            )
        )

    async def observe_model_unknown(
        self, run_id: str, *, snapshot_sha256: str
    ) -> ModelUnknownObservation:
        job = self.status(run_id)
        options = self._caller_options(job, "observe_model_unknown", snapshot_sha256)
        if self._executor is None:
            raise ValueError("service not started")
        self._original_caller_request(job, snapshot_sha256)
        observation = await self._executor(options)
        self._admission_unchanged(job)
        if not isinstance(observation, ModelUnknownObservation):
            raise ValueError("native observation returned a different record")
        return observation

    def _original_caller_request(self, job: CollectionJob, pin: str) -> None:
        if self._validator is None or self._validator.config.research_recovery is None:
            raise ValueError("native validator unavailable")
        request = ResearchRequest.model_validate_json(
            _read_file(
                self.config.directory / (job.run_id + ".request"),
                self.config.command.max_input_bytes,
            )
        )
        if request.content_digest() != job.request_sha256:
            raise ValueError("original service request changed")
        ResearchRecoveryStore(
            self._validator.config, job.run_id, self._validator.config.research_recovery
        ).read(
            pin,
            expected_request=request,
            expected_models=self._validator.recovery_models(),
            observe_unknown=True,
        )

    async def reconcile_model(
        self, run_id: str, decision: ModelReconciliationDecision
    ) -> ModelAttemptAuthorization:
        job = self.status(run_id)
        if decision.observed.run_id != run_id:
            raise ValueError("decision belongs to another original job")
        options = self._caller_options(
            job, "reconcile_model", decision.observed.snapshot_sha256, decision
        )
        if self._executor is None:
            raise ValueError("service not started")
        self._original_caller_request(job, decision.observed.snapshot_sha256)
        receipt = await self._executor(options)
        self._admission_unchanged(job)
        if not isinstance(receipt, ModelAttemptAuthorization):
            raise ValueError("native decision returned a different record")
        self._update(
            run_id,
            "held",
            failure="interrupted",
            snapshot_sha256=receipt.snapshot_sha256,
            model_attempt=receipt,
            recovery_hold="manual_required",
        )
        return receipt

    def model_attempt_history(self, run_id: str) -> tuple[ModelAttemptAuthorization, ...]:
        job = self.status(run_id)
        if (
            self._fd < 0
            or self._closing
            or job.policy_sha256 != self.config.identity
            or job.recipe_sha256 != self._recipe_sha256
            or self._validator is None
        ):
            raise ValueError("attempt history requires its original active service owner")
        return self._validator.model_attempt_history(run_id)

    def _admission_unchanged(self, job: CollectionJob) -> None:
        if self.status(job.run_id) != job or self._fd < 0 or self._closing:
            raise ValueError("job changed during native recovery admission")

    async def _adopt_completed_output(self, job: CollectionJob) -> bool:
        """Read back a proved original command ACK, never recreate or recollect it."""
        if (
            job.policy_sha256 != self.config.identity
            or job.recipe_sha256 != self._recipe_sha256
            or job.phase != "held"
            or job.failure != "interrupted"
            or job.archive is not None
        ):
            return False
        output = self.config.directory / (job.run_id + ".output")
        seal = output / "receipt.json"
        if not seal.exists() and not seal.is_symlink():
            return False
        try:
            recipe = GhimeraConfig.model_validate(
                tomllib.loads(
                    bounded_file(
                        self.config.command.config_path, self.config.command.max_input_bytes
                    ).decode()
                )
            )
            if (
                recipe.journal is None
                or job.request_sha256 is None
                or hashlib.sha256(recipe.model_dump_json().encode()).hexdigest()
                != job.recipe_sha256
            ):
                raise ValueError("completed output lost its original policy")
            request = ResearchRequest.model_validate_json(
                _read_file(
                    self.config.directory / (job.run_id + ".request"),
                    self.config.command.max_input_bytes,
                )
            )
            if request.content_digest() != job.request_sha256:
                raise ValueError("completed output request changed")
            expected = ArchiveReservation(
                schema="ghimera.command-output/1",
                run_id=job.run_id,
                config_sha256=job.recipe_sha256,
                request_sha256=job.request_sha256,
            )
            receipt, result = ResearchResultArchive.acknowledged(
                output,
                reservation=expected,
                max_bytes=self.config.command.max_result_bytes,
                max_reservation_bytes=self.config.command.max_input_bytes,
            )
            observed_models = ResearchRecoveryModels(
                planner=result.planner,
                analyst=result.analyst,
                reviewer=result.reviewer,
                search_provider=result.search_provider,
                search_revision=result.search_revision,
            )
            if (
                result.harvest.goal.text != request.intent
                or result.harvest.goal.seeds != request.seeds
                or self._validator is None
                or self._validator.recovery_models() != observed_models
            ):
                raise ValueError(
                    "completed output differs from original intent or runtime identities"
                )
            report = read_journal(recipe.journal, job.run_id)
            documents = tuple(
                JournalDocument(
                    url=doc.url,
                    sha256=doc.sha256,
                    native_text_sha256=hashlib.sha256(doc.extracted.text.encode()).hexdigest(),
                    raw_bytes=len(doc.raw),
                )
                for doc in result.harvest.documents
            )
            retained = tuple(
                JournalRetainedDocument(
                    url=item.document.url,
                    sha256=item.document.sha256,
                    native_text_sha256=hashlib.sha256(
                        item.document.extracted.text.encode()
                    ).hexdigest(),
                    raw_bytes=len(item.document.raw),
                    origin=item.origin,
                )
                for item in result.harvest.retained_sources
            )
            if (
                receipt.run_id != job.run_id
                or report.state != "complete"
                or report.incomplete_tail
                or unreconciled_model_sequences(report.rows)
                or report.header.config != recipe
                or report.header.goal != result.harvest.goal
                or report.rows != result.harvest.ledger
                or report.summary is None
                or report.summary.receipt != result.harvest.receipt
                or report.summary.documents != documents
                or report.summary.retained_documents != retained
            ):
                raise ValueError("completed output lost its sealed original journal")
            if recipe.source_work is not None:
                sources = read_source_work(recipe, job.run_id)
                if sources.writer_active or any(
                    item.ledger_end is None for item in sources.operations
                ):
                    raise ValueError("completed output has unresolved source work")
            if recipe.graph is not None and recipe.graph.enabled:
                if result.harvest.graph is None:
                    raise ValueError("completed output lost its graph")
                graph = ResearchGraph(
                    recipe.graph, job.run_id, DirectoryGraphSink(recipe.graph, job.run_id)
                )
                await graph.start(result.harvest.goal.text, expected=result.harvest.graph)
            self._admission_unchanged(job)
            self._update(job.run_id, "handoff_pending", archive=receipt)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._admission_unchanged(job)
            # Keep interrupted evidence available for the ordinary hold admission.
            return False
        self._launch(job.run_id)
        return True

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
                recovering = job.phase == "recovering"
                if not recovering:
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
                    if recovering:
                        if recipe.research_recovery is None or job.snapshot_sha256 is None:
                            raise ValueError("recovery lost its original policy or snapshot")
                        request = ResearchRequest.model_validate_json(
                            _read_file(
                                self.config.directory / (run_id + ".request"),
                                self.config.command.max_input_bytes,
                            )
                        )
                        if request.content_digest() != job.request_sha256:
                            raise ValueError("original service request changed before recovery")
                        selected_attempt = (
                            job.model_attempt
                            if job.model_attempt is not None
                            and job.model_attempt.snapshot_sha256 == job.snapshot_sha256
                            else None
                        )
                        if job.recovery_boundary == "source_completion":
                            SourceWorkStore.completion(
                                recipe,
                                run_id,
                                job.snapshot_sha256,
                                expected_request=request,
                                expected_models=self._validator.recovery_models()
                                if self._validator is not None
                                else None,
                                expected_runtime=self._validator.source_runtime()
                                if self._validator is not None
                                else None,
                            )
                        else:
                            ResearchRecoveryStore(recipe, run_id, recipe.research_recovery).read(
                                job.snapshot_sha256,
                                expected_request=request,
                                expected_models=self._validator.recovery_models()
                                if self._validator is not None
                                else None,
                                attempt=selected_attempt,
                            )
                        options = CommandOptions.model_validate(
                            dict(
                                options.model_dump(),
                                schema="ghimera.collector-command/5"
                                if selected_attempt is not None
                                else "ghimera.collector-command/4",
                                request_path=None,
                                execution=CommandExecution(
                                    schema="ghimera.command-execution/4"
                                    if selected_attempt is not None
                                    else "ghimera.command-execution/3"
                                    if job.recovery_boundary is not None
                                    else "ghimera.command-execution/2",
                                    operation="recover",
                                    snapshot_sha256=job.snapshot_sha256,
                                    recovery_boundary=job.recovery_boundary,
                                    model_attempt=selected_attempt,
                                ),
                            )
                        )
                    if self._executor is None:
                        raise ValueError("service not started")
                    receipt = await self._executor(options)
                    if isinstance(receipt, (ModelUnknownObservation, ModelAttemptAuthorization)):
                        raise ValueError("run execution must return output or a round checkpoint")
                    if isinstance(receipt, CheckpointReceipt):
                        if recovering:
                            raise ValueError("model recovery cannot introduce a round checkpoint")
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
            failed = self.status(run_id)
            archived = failed.archive is not None
            if failed.phase == "recovering" and not archived:
                self._update(
                    run_id, "held", failure="recovery_failed", recovery_hold="recovery_failed"
                )
                return
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
