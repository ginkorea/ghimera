"""Bounded regular-file snapshot; no source I/O, parser or model is hidden here."""

import hashlib
import os
import stat
from dataclasses import dataclass

from ghimera.local_input_types import LocalDocumentSeed, LocalInputConfig, LocalInputEvidence
from ghimera.refusals import GhimeraRefused, RefusalCode


class LocalInputFailure(GhimeraRefused):
    def __init__(self, code: RefusalCode, bytes_read: int) -> None:
        self.bytes_read = bytes_read
        super().__init__(code)


@dataclass(frozen=True)
class LocalInputSnapshot:
    raw: bytes
    evidence: LocalInputEvidence


class LocalInputLoader:
    def __init__(self, policy: LocalInputConfig) -> None:
        self.policy = LocalInputConfig.model_validate(policy.model_dump())

    def read(self, seed: LocalDocumentSeed, *, max_bytes: int) -> LocalInputSnapshot:
        seed = LocalDocumentSeed.model_validate(seed.model_dump())
        if not self.policy.permits(seed.path):
            raise LocalInputFailure(RefusalCode.LOCAL_INPUT_FAILED, 0)
        allowance = min(max_bytes, self.policy.max_input_bytes)
        if allowance <= 0:
            raise LocalInputFailure(RefusalCode.BUDGET_EXHAUSTED, 0)
        retained = b""
        directory = -1
        try:
            # Open each directory component without following symlinks. Merely
            # checking Path.resolve before opening the leaf leaves a rename race.
            directory = os.open(seed.path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for component in seed.path.parts[1:-1]:
                following = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                os.close(directory)
                directory = following
            fd = os.open(
                seed.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
                    raise LocalInputFailure(RefusalCode.LOCAL_INPUT_FAILED, 0)
                if before.st_size > allowance:
                    raise LocalInputFailure(RefusalCode.BUDGET_EXHAUSTED, 0)
                retained = stream.read(before.st_size)
                after = os.fstat(stream.fileno())
            if (
                len(retained) != before.st_size
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or hashlib.sha256(retained).hexdigest() != seed.sha256
            ):
                raise LocalInputFailure(RefusalCode.LOCAL_INPUT_FAILED, len(retained))
            return LocalInputSnapshot(
                retained,
                LocalInputEvidence(
                    schema="ghimera.local-input-evidence/1",
                    source_id=seed.source_id,
                    sha256=seed.sha256,
                    size_bytes=len(retained),
                    content_type=seed.content_type,
                    policy_digest=self.policy.content_digest(),
                    reader_revision="bounded-local-file/1",
                ),
            )
        except OSError:
            raise LocalInputFailure(RefusalCode.LOCAL_INPUT_FAILED, len(retained)) from None
        finally:
            if directory >= 0:
                os.close(directory)
