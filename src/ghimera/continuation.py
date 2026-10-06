"""Bounded round checkpoints; never guess or replay post-checkpoint side effects."""

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.config import GhimeraConfig
from ghimera.models import Record
from ghimera.research_types import Assessment, ResearchRequest, ResearchResult
from ghimera.session_state import SessionState

RunId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ResearchCheckpoint(Record):
    schema_version: Literal["ghimera.research-checkpoint/1"] = Field(alias="schema")
    run_id: RunId
    saved_at: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    request: ResearchRequest
    progress: ResearchResult
    session: SessionState
    admitted_hosts: tuple[str, ...]
    assessment: Assessment | None
    next_action: Literal["plan", "answer"]

    @model_validator(mode="after")
    def round_boundary(self) -> "ResearchCheckpoint":
        progress, harvest = self.progress, self.progress.harvest
        policy = harvest.receipt.effective_config.research
        if (
            policy is None
            or harvest.receipt.effective_config.continuation is None
            or progress.status != "partial"
            or progress.answer is not None
            or progress.review is not None
            or harvest.goal.text != self.request.intent
            or harvest.goal.seeds != self.request.seeds
            or not progress.rounds
            or len(progress.rounds) > policy.max_rounds
            or tuple(round_.number for round_ in progress.rounds)
            != tuple(range(1, len(progress.rounds) + 1))
            or any(row.event == "stop" for row in harvest.ledger)
            or any(round_.assessment is None for round_ in progress.rounds)
            or (self.assessment is not None and self.assessment != progress.rounds[-1].assessment)
        ):
            raise ValueError("checkpoint requires completed rounds of an unsealed original run")
        ready = self.assessment is not None and all(
            item.status == "answered" for item in self.assessment.coverage
        )
        if (self.next_action == "answer") != ready:
            raise ValueError("checkpoint next action must retain its assessment decision")
        if harvest.graph is not None and harvest.graph.run_id != self.run_id:
            raise ValueError("checkpoint graph belongs to another run")
        if len(set(self.session.visited)) != len(self.session.visited):
            raise ValueError("checkpoint visits must be unique")
        if set(self.session.reference_scopes) != set(self.session.reference_hops) or set(
            self.session.reference_scopes
        ) != set(self.session.reference_origins):
            raise ValueError("reference frontier must retain its scope and ancestry together")
        if any(not scope.permits(url) for url, scope in self.session.reference_scopes.items()):
            raise ValueError("reference scope cannot exclude its own queued URL")
        return self


class CheckpointReceipt(Record):
    schema_version: Literal["ghimera.checkpoint-receipt/1"] = Field(alias="schema")
    run_id: RunId
    sha256: Digest
    size_bytes: Annotated[int, Field(strict=True, gt=0)]
    completed_rounds: Annotated[int, Field(strict=True, gt=0)]


class ResearchSuspended(Exception):
    """Deliberate pause, not a failed/completed research result."""

    def __init__(self, receipt: CheckpointReceipt) -> None:
        self.receipt = receipt
        super().__init__("research_suspended:" + receipt.run_id)


class CheckpointStore:
    """One bounded replaceable checkpoint in the existing owner-private journal."""

    def __init__(self, config: GhimeraConfig, run_id: str) -> None:
        from ghimera.journal import _run_path

        if config.continuation is None or config.journal is None:
            raise ValueError("continuation requires its configured journal")
        self._config, self._run_id = config, run_id
        self._path = _run_path(config.journal, run_id)
        self._maximum = config.continuation.max_checkpoint_bytes

    def _check(self) -> None:
        from ghimera.journal import _private_directory, _run_path

        policy = self._config.journal
        if policy is None or _run_path(policy, self._run_id) != self._path:
            raise ValueError("checkpoint storage changed")
        _private_directory(policy.directory)
        _private_directory(self._path)

    def write(self, checkpoint: ResearchCheckpoint) -> CheckpointReceipt:
        from ghimera.journal import _directory_sync, _read_file, _write_all

        self._check()
        checkpoint = ResearchCheckpoint.model_validate(checkpoint.model_dump())
        if checkpoint.run_id != self._run_id or (
            checkpoint.progress.harvest.receipt.effective_config != self._config
        ):
            raise ValueError("checkpoint must bind its run and exact effective recipe")
        data = checkpoint.model_dump_json().encode()
        if len(data) > self._maximum:
            raise ValueError("checkpoint exceeds its configured byte allowance")
        fd, name = tempfile.mkstemp(prefix=".checkpoint-", dir=self._path)
        staging = Path(name)
        try:
            try:
                _write_all(fd, data)
            finally:
                os.close(fd)
            self._check()
            target = self._path / "checkpoint.json"
            if target.exists() or target.is_symlink():
                _read_file(target, self._maximum)
            os.replace(staging, target)
            _directory_sync(self._path)
        finally:
            staging.unlink(missing_ok=True)
        return CheckpointReceipt(
            schema="ghimera.checkpoint-receipt/1",
            run_id=self._run_id,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            completed_rounds=len(checkpoint.progress.rounds),
        )

    def read(self, expected_sha256: str) -> ResearchCheckpoint:
        from ghimera.journal import _read_file

        self._check()
        data = _read_file(self._path / "checkpoint.json", self._maximum)
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("checkpoint differs from its expected digest")
        checkpoint = ResearchCheckpoint.model_validate_json(data)
        if checkpoint.run_id != self._run_id or (
            checkpoint.progress.harvest.receipt.effective_config != self._config
        ):
            raise ValueError("checkpoint belongs to another run or effective recipe")
        return checkpoint
