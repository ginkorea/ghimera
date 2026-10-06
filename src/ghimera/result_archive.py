"""Private immutable result archive; a receipt seals bytes, not factual accuracy."""

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ghimera.research_types import ResearchResult


class ArchiveReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.research-archive/1"] = Field(alias="schema")
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    result_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    result_bytes: Annotated[int, Field(strict=True, gt=0)]
    status: Literal["answered", "partial", "failed"]
    documents: Annotated[int, Field(strict=True, ge=0)]


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
    """Own one newly reserved directory and publish complete files without overwrite."""

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
            return result
        finally:
            archive.close()
