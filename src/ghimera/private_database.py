"""One owner-private SQLite boundary shared by evidence and delivery stores."""

import fcntl
import math
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class PrivateDatabase:
    """Own descriptors and one thread-local connection; domain schemas stay outside."""

    def __init__(self, directory: Path, name: str, *, timeout: float, create: bool) -> None:
        if (
            not directory.is_absolute()
            or directory == Path("/")
            or not re.fullmatch(r"[a-z][a-z0-9_-]*\.sqlite", name)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("private storage needs an explicit directory, name and timeout")
        self._path, self._name = directory, name
        self._directory = self._database = -1
        self._connection: sqlite3.Connection | None = None
        self._path_check()
        if create:
            directory.mkdir(mode=0o700, exist_ok=False)
        self._directory = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.check()
            self._database = os.open(
                name,
                os.O_RDWR
                | os.O_NOFOLLOW
                | os.O_NONBLOCK
                | (os.O_CREAT | os.O_EXCL if create else 0),
                0o600,
                dir_fd=self._directory,
            )
            self.check()
            self._connection = sqlite3.connect(
                f"/proc/self/fd/{self._directory}/{name}", timeout=timeout
            )
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA trusted_schema=OFF")
            self.db.execute("PRAGMA synchronous=FULL")
        except BaseException:
            self.close()
            raise

    @property
    def db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ValueError("private storage is closed")
        return self._connection

    def _path_check(self) -> None:
        if any(item.is_symlink() for item in (self._path, *self._path.parents)):
            raise ValueError("private storage cannot traverse symlinks")

    def check(self) -> None:
        self._path_check()
        info, named = os.fstat(self._directory), self._path.lstat()
        if (
            info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ValueError("storage directory must remain at its owner-private identity")
        if self._database >= 0:
            info = os.fstat(self._database)
            named = os.stat(self._name, dir_fd=self._directory, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise ValueError("database must remain owner-private and non-linked")

    def seal_directory(self) -> None:
        """After committing a new schema, make its directory entry durable too."""
        self.check()
        os.fsync(self._directory)
        parent = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

    @contextmanager
    def writer(self) -> Iterator[None]:
        self.check()
        # A separate open description is necessary even within one process.
        fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=self._directory)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.check()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.check()
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._database >= 0:
            os.close(self._database)
            self._database = -1
        if self._directory >= 0:
            os.close(self._directory)
            self._directory = -1
