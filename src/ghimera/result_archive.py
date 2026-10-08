"""Private immutable result archive; a receipt seals bytes, not factual accuracy."""

import fcntl
import hashlib
import os
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ghimera.research_types import ResearchResult

if TYPE_CHECKING:
    from ghimera.config import GhimeraConfig
    from ghimera.research_types import ResearchRequest


class ArchiveReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.research-archive/1"] = Field(alias="schema")
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    result_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    result_bytes: Annotated[int, Field(strict=True, gt=0)]
    status: Literal["answered", "partial", "failed"]
    documents: Annotated[int, Field(strict=True, ge=0)]


class ArchiveReservation(BaseModel):
    """Non-secret output identity retained across explicit command invocations."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.command-output/1"] = Field(alias="schema")
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    config_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    request_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def bounded_file(path: Path, max_bytes: int) -> bytes:
    """Bound the read before parsing and refuse special files/symlink leaves."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("a positive byte allowance is required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise ValueError("input must be a bounded regular file")
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("input exceeds its byte allowance")
    return data


class ResearchResultArchive:
    """Own an explicit output identity; publish complete files without overwrite."""

    def __init__(self, path: Path, run_id: str) -> None:
        self._path, self._run_id = path, run_id
        self._fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.check()
        except BaseException:
            self.close()
            raise

    @classmethod
    def create(cls, path: Path, *, run_id: str) -> "ResearchResultArchive":
        cls._path_check(path)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", run_id):
            raise ValueError("invalid run identity")
        # The parent is chosen and created by the caller, not an implicit root.
        path.mkdir(mode=0o700, exist_ok=False)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        return cls(path, run_id)

    @classmethod
    def reserve(
        cls, path: Path, *, reservation: ArchiveReservation, max_bytes: int
    ) -> "ResearchResultArchive":
        reservation = ArchiveReservation.model_validate(reservation.model_dump())
        data = reservation.model_dump_json().encode()
        if type(max_bytes) is not int or max_bytes <= 0 or len(data) > max_bytes:
            raise ValueError("reservation exceeds its positive byte allowance")
        archive = cls.create(path, run_id=reservation.run_id)
        try:
            fcntl.flock(archive._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            archive._publish("reservation.json", data)
            return archive
        except BaseException:
            archive.close()
            raise

    @classmethod
    def resume(
        cls, path: Path, *, reservation: ArchiveReservation, max_bytes: int
    ) -> "ResearchResultArchive":
        reservation = ArchiveReservation.model_validate(reservation.model_dump())
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("a positive reservation byte allowance is required")
        cls._path_check(path)
        archive = cls(path, reservation.run_id)
        try:
            fcntl.flock(archive._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed = ArchiveReservation.model_validate_json(
                archive._read("reservation.json", max_bytes)
            )
            if observed != reservation or set(os.listdir(archive._fd)) != {"reservation.json"}:
                raise ValueError("resume requires its exact unfinished output reservation")
            return archive
        except BaseException:
            archive.close()
            raise

    @staticmethod
    def _path_check(path: Path) -> None:
        if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("archive needs an absolute non-symlink path")

    def check(self) -> None:
        self._path_check(self._path)
        info = os.fstat(self._fd)
        named = self._path.lstat()
        if (
            info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise ValueError("archive must remain owner-private at its reserved path")

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def _publish(self, name: str, data: bytes) -> None:
        self.check()
        pending = "." + name + ".pending"
        fd = os.open(
            pending,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self._fd,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self.check()
            os.link(pending, name, src_dir_fd=self._fd, dst_dir_fd=self._fd, follow_symlinks=False)
        finally:
            os.unlink(pending, dir_fd=self._fd)
        os.fsync(self._fd)

    def write(self, result: ResearchResult, *, max_bytes: int) -> ArchiveReceipt:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("a positive result byte allowance is required")
        self.check()
        result = ResearchResult.model_validate(result.model_dump())
        data = result.model_dump_json().encode()
        if len(data) > max_bytes:
            raise ValueError("complete result exceeds its output allowance")
        receipt = ArchiveReceipt(
            schema="chimera.research-archive/1",
            run_id=self._run_id,
            result_sha256=hashlib.sha256(data).hexdigest(),
            result_bytes=len(data),
            status=result.status,
            documents=len(result.harvest.documents),
        )
        self._publish("result.json", data)
        # No receipt means no complete archive, including interruption between files.
        self._publish("receipt.json", receipt.model_dump_json().encode())
        return receipt

    def _read(self, name: str, max_bytes: int) -> bytes:
        self.check()
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._fd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
                or info.st_size > max_bytes
            ):
                raise ValueError("archive file is not bounded owner-private evidence")
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("archive file exceeds its read allowance")
        return data

    @classmethod
    def acknowledged(
        cls,
        path: Path,
        *,
        reservation: ArchiveReservation,
        max_bytes: int,
        max_reservation_bytes: int,
    ) -> tuple[ArchiveReceipt, ResearchResult]:
        """Verify a completed original command under the archive's writer lock.

        This admits only existing sealed evidence. It never finishes a partial
        write, changes a reservation, recreates a receipt or launches work.
        """
        reservation = ArchiveReservation.model_validate(reservation.model_dump())
        if (
            type(max_bytes) is not int
            or max_bytes <= 0
            or type(max_reservation_bytes) is not int
            or max_reservation_bytes <= 0
        ):
            raise ValueError("positive archive and reservation read allowances are required")
        cls._path_check(path)
        archive = cls(path, reservation.run_id)
        try:
            fcntl.flock(archive._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if set(os.listdir(archive._fd)) != {
                "reservation.json",
                "result.json",
                "receipt.json",
            }:
                raise ValueError("acknowledgement requires exactly the original sealed output")
            observed = ArchiveReservation.model_validate_json(
                archive._read("reservation.json", max_reservation_bytes)
            )
            if observed != reservation:
                raise ValueError("acknowledgement requires the exact original reservation")
            result = cls.read(path, max_bytes=max_bytes)
            receipt = ArchiveReceipt.model_validate_json(
                archive._read("receipt.json", max_reservation_bytes)
            )
            if receipt.run_id != reservation.run_id:
                raise ValueError("acknowledgement changed its original run identity")
            return receipt, result
        finally:
            archive.close()

    @classmethod
    async def acknowledged_research(
        cls,
        path: Path,
        *,
        reservation: ArchiveReservation,
        config: "GhimeraConfig",
        request: "ResearchRequest",
        max_bytes: int,
        max_reservation_bytes: int,
    ) -> tuple[ArchiveReceipt, ResearchResult]:
        """Prove completed original research for archive-only native handoff, without contact."""
        from ghimera.config import GhimeraConfig
        from ghimera.graph import DirectoryGraphSink, ResearchGraph
        from ghimera.journal import read_journal
        from ghimera.journal_types import JournalDocument, JournalRetainedDocument
        from ghimera.model_reconciliation import unreconciled_model_sequences
        from ghimera.query_work import validate_query_rows
        from ghimera.research_types import ResearchRequest
        from ghimera.source_work import read_source_work

        config = GhimeraConfig.model_validate(config.model_dump())
        request = ResearchRequest.model_validate(request.model_dump())
        reservation = ArchiveReservation.model_validate(reservation.model_dump())
        if (
            reservation.config_sha256
            != hashlib.sha256(config.model_dump_json().encode()).hexdigest()
            or reservation.request_sha256 != request.content_digest()
            or config.journal is None
        ):
            raise ValueError("completed research lost its original request/recipe reservation")
        receipt, result = cls.acknowledged(
            path,
            reservation=reservation,
            max_bytes=max_bytes,
            max_reservation_bytes=max_reservation_bytes,
        )
        if (
            result.harvest.receipt.effective_config != config
            or result.harvest.goal.text != request.intent
            or result.harvest.goal.seeds != request.seeds
        ):
            raise ValueError("archive changed its original request or recipe")
        report = read_journal(config.journal, reservation.run_id)
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
            report.state != "complete"
            or report.incomplete_tail
            or unreconciled_model_sequences(report.rows)
            or validate_query_rows(config, report.rows)
            or report.header.config != config
            or report.header.goal != result.harvest.goal
            or report.rows != result.harvest.ledger
            or report.summary is None
            or report.summary.receipt != result.harvest.receipt
            or report.summary.documents != documents
            or report.summary.retained_documents != retained
        ):
            raise ValueError("archive lost its original journal/source proof")
        if config.source_work is not None:
            sources = read_source_work(config, reservation.run_id)
            if sources.writer_active or any(item.ledger_end is None for item in sources.operations):
                raise ValueError("archive has unfinished source work")
        if config.graph is not None and config.graph.enabled:
            if result.harvest.graph is None:
                raise ValueError("archive lost its native graph")
            graph = ResearchGraph(
                config.graph,
                reservation.run_id,
                DirectoryGraphSink(config.graph, reservation.run_id),
            )
            await graph.start(result.harvest.goal.text, expected=result.harvest.graph)
        return receipt, result

    @classmethod
    def read(cls, path: Path, *, max_bytes: int) -> ResearchResult:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("a positive result byte allowance is required")
        cls._path_check(path)
        archive = cls(path, "read-only")
        try:
            receipt = ArchiveReceipt.model_validate_json(archive._read("receipt.json", max_bytes))
            data = archive._read("result.json", max_bytes)
            if (
                len(data) != receipt.result_bytes
                or hashlib.sha256(data).hexdigest() != receipt.result_sha256
            ):
                raise ValueError("archive bytes do not match their receipt")
            result = ResearchResult.model_validate_json(data)
            if receipt.status != result.status or receipt.documents != len(
                result.harvest.documents
            ):
                raise ValueError("archive receipt does not describe the validated result")
            if "reservation.json" in os.listdir(archive._fd):
                reservation = ArchiveReservation.model_validate_json(
                    archive._read("reservation.json", max_bytes)
                )
                observed_config = hashlib.sha256(
                    result.harvest.receipt.effective_config.model_dump_json().encode()
                ).hexdigest()
                if (
                    reservation.run_id != receipt.run_id
                    or reservation.config_sha256 != observed_config
                ):
                    raise ValueError("archive result differs from its command reservation")
            return result
        finally:
            archive.close()
