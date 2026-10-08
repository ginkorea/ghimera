"""Package-only, immutable code projection for an owned native worker.

The invoking installation owns Ghimera code; the selected interpreter owns its
dependencies. Never expose the invoking installation's site-packages root.
"""

import asyncio
import hashlib
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from ghimera.refusals import GhimeraRefused, RefusalCode


@dataclass(frozen=True)
class PackageFile:
    path: PurePosixPath
    body: bytes
    sha256: str

    def __post_init__(self) -> None:
        if (
            self.path.is_absolute()
            or ".." in self.path.parts
            or not self.path.parts
            or self.path.parts[0] not in {"ghimera", "chimera"}
            or hashlib.sha256(self.body).hexdigest() != self.sha256
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)


@dataclass(frozen=True)
class PackageInventory:
    """Exact admitted regular code bytes, not a mutable source-directory link."""

    files: tuple[PackageFile, ...]

    def __post_init__(self) -> None:
        if not self.files or len({item.path for item in self.files}) != len(self.files):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    @property
    def size_bytes(self) -> int:
        return sum(len(item.body) for item in self.files)

    @property
    def sha256(self) -> str:
        digest = hashlib.sha256()
        for item in self.files:
            digest.update(item.path.as_posix().encode() + b"\0")
            digest.update(str(len(item.body)).encode() + b"\0")
            digest.update(item.sha256.encode() + b"\0")
        return digest.hexdigest()

    @asynccontextmanager
    async def project(self, work_directory: Path) -> AsyncIterator[Path]:
        """Copy only the admitted inventory; reap it after the owned child."""
        with TemporaryDirectory(prefix="package-", dir=work_directory) as directory:
            root = Path(directory)
            directories = {root}
            try:
                for item in self.files:
                    await asyncio.sleep(0)
                    target = root.joinpath(*item.path.parts)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    directories.update(target.parents)
                    descriptor = os.open(
                        target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400
                    )
                    with os.fdopen(descriptor, "wb") as stream:
                        if stream.write(item.body) != len(item.body):
                            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                    if hashlib.sha256(item.body).hexdigest() != item.sha256:
                        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                for path in directories:
                    if path == root or path.is_relative_to(root):
                        path.chmod(0o500)
                yield root
            finally:
                # Only this newly created private projection is eligible for cleanup.
                for path in directories:
                    if (path == root or path.is_relative_to(root)) and path.is_dir():
                        path.chmod(0o700)


async def admit_package(package: Path | None = None) -> PackageInventory:
    """Enumerate exact code sizes first, then admit immutable bytes to those bounds.

    No operational cap is invented: the maximum count/bytes is the observed
    regular library-code inventory. Reads refuse replacement, growth, truncation
    or links. No adjacent dependencies, distribution metadata or cache is read.
    """
    owner = package if package is not None else Path(__file__).absolute().parent
    roots = [owner]
    facade = owner.parent / "chimera"
    if facade.exists() or facade.is_symlink():
        roots.append(facade)
    admitted: list[tuple[Path, PurePosixPath, os.stat_result]] = []
    for root in roots:
        if root.name not in {"ghimera", "chimera"} or root.is_symlink() or not root.is_dir():
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        for directory, subdirectories, filenames in os.walk(root, followlinks=False):
            await asyncio.sleep(0)
            current = Path(directory)
            for name in tuple(subdirectories):
                child = current / name
                if child.is_symlink():
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                if name == "__pycache__":
                    subdirectories.remove(name)
            for name in sorted(filenames):
                path = current / name
                if path.suffix != ".py" and name != "py.typed":
                    continue
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                relative = PurePosixPath(root.name) / path.relative_to(root).as_posix()
                if root.name == "chimera" and relative.as_posix() not in {
                    "chimera/__init__.py",
                    "chimera/__main__.py",
                }:
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                admitted.append((path, relative, info))
    if not any(relative == PurePosixPath("ghimera/__init__.py") for _, relative, _ in admitted):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    files: list[PackageFile] = []
    for path, relative, info in sorted(admitted, key=lambda item: item[1].as_posix()):
        await asyncio.sleep(0)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            body = stream.read(info.st_size + 1)
            after = os.fstat(stream.fileno())
        if (
            identity(before) != identity(info)
            or identity(after) != identity(before)
            or len(body) != info.st_size
            or not stat.S_ISREG(before.st_mode)
        ):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        files.append(PackageFile(relative, body, hashlib.sha256(body).hexdigest()))
    return PackageInventory(tuple(files))


def identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    """Reading may change atime, but must not change the admitted code identity."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )
