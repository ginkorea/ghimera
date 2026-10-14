"""Owned long-lived parser interpreters: import the vendor stack once, serve many pages.

Each worker is the same bounded child as ``PassiveWorker`` — the selected
interpreter, a scrubbed environment, an immutable package-only projection and
the passive audit guard — but it stays alive and reads length-prefixed requests
from stdin. One request occupies one worker at a time. A worker that exceeds
the deadline, overflows its output bound, exits or answers out of protocol is
killed and replaced; it never serves another page. Workers outlive any one
asyncio loop; ``close()`` (or interpreter exit) kills them and removes their
package copies.
"""

import asyncio
import errno
import hashlib
import os
import shutil
import signal
import struct
import subprocess
import tempfile
import threading
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from ghimera.passive_worker import private_directory
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.worker_package import PackageInventory, admit_package

FRAME = struct.Struct(">Q")


def _project(inventory: PackageInventory, work_directory: Path) -> Path:
    """Synchronous copy of the admitted inventory, owned until the worker retires."""
    root = Path(tempfile.mkdtemp(prefix="package-", dir=work_directory))
    directories = {root}
    for item in inventory.files:
        target = root.joinpath(*item.path.parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directories.update(target.parents)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(descriptor, "wb") as stream:
            if stream.write(item.body) != len(item.body):
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT, "worker package copy truncated")
        if hashlib.sha256(item.body).hexdigest() != item.sha256:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT, "worker package bytes changed")
    for path in directories:
        if path == root or path.is_relative_to(root):
            path.chmod(0o500)
    return root


def _reap(root: Path) -> None:
    if not root.is_dir():
        return
    for path in (root, *root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)
    shutil.rmtree(root, ignore_errors=True)


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _read_exactly(stream: IO[bytes], size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 1 << 20))
        if not chunk:
            raise EOFError
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


@dataclass
class _Worker:
    process: subprocess.Popen[bytes]
    root: Path
    served: int = field(default=0)


class PersistentWorkerPool:
    """``run(request) -> response`` with the same contract as ``PassiveWorker.run``.

    Pipes are plain blocking descriptors read on a worker thread, so a worker is
    not tied to the asyncio loop that started it and survives across runs.
    """

    def __init__(
        self,
        *,
        interpreter: Path,
        module: str,
        work_directory: Path,
        max_workers: int,
        timeout_seconds: float,
        max_output_bytes: int,
        environment: dict[str, str],
    ) -> None:
        self._interpreter, self._module, self._directory = interpreter, module, work_directory
        self._max_workers, self._timeout = max_workers, timeout_seconds
        self._output = max_output_bytes
        self._environment = dict(environment)
        self._slots: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}
        self._idle: list[_Worker] = []
        self._live: dict[int, subprocess.Popen[bytes]] = {}
        self._roots: set[Path] = set()
        self._inventory: PackageInventory | None = None
        self._lock = threading.Lock()
        # Interpreter exit or garbage collection never leaves children or copies.
        self._finalizer = weakref.finalize(
            self, PersistentWorkerPool._retire_all, self._live, self._roots
        )

    @staticmethod
    def _retire_all(live: dict[int, subprocess.Popen[bytes]], roots: set[Path]) -> None:
        for process in tuple(live.values()):
            _kill(process.pid)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        live.clear()
        for root in tuple(roots):
            _reap(root)
        roots.clear()

    def close(self) -> None:
        """Kill every worker and remove every package copy; the pool may be reused."""
        with self._lock:
            self._idle.clear()
            PersistentWorkerPool._retire_all(self._live, self._roots)

    def _spawn(self, inventory: PackageInventory) -> _Worker:
        private_directory(self._directory)
        root = _project(inventory, self._directory)
        environment = self._environment | {
            "PYTHONPATH": str(root),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        try:
            process = subprocess.Popen(
                [str(self._interpreter), "-m", self._module, "--serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._directory,
                env=environment,
                close_fds=True,
            )
        except OSError as exc:
            _reap(root)
            name = errno.errorcode.get(exc.errno or 0, type(exc).__name__)
            raise GhimeraRefused(
                RefusalCode.EXTRACTION_FAILED, f"starting a persistent reader failed with {name}"
            ) from None
        with self._lock:
            self._roots.add(root)
            self._live[process.pid] = process
        stderr = process.stderr

        def discard() -> None:
            # Diagnostics are not retained; draining keeps the child from blocking.
            if stderr is not None:
                while stderr.read(65536):
                    pass
                stderr.close()

        threading.Thread(target=discard, name="ghimera-reader-stderr", daemon=True).start()
        return _Worker(process, root)

    def _retire(self, worker: _Worker) -> None:
        with self._lock:
            self._live.pop(worker.process.pid, None)
            self._roots.discard(worker.root)
            if worker in self._idle:
                self._idle.remove(worker)
        PersistentWorkerPool._retire_all({worker.process.pid: worker.process}, {worker.root})

    def _call(self, worker: _Worker, request: bytes) -> bytes:
        process = worker.process
        if process.stdin is None or process.stdout is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT, "persistent reader has no pipes")
        try:
            process.stdin.write(FRAME.pack(len(request)) + request)
            process.stdin.flush()
            (size,) = FRAME.unpack(_read_exactly(process.stdout, FRAME.size))
            if size > self._output:
                raise GhimeraRefused(
                    RefusalCode.EXTRACTION_FAILED,
                    f"reader response of {size} bytes exceeds max_output_bytes={self._output}",
                )
            return _read_exactly(process.stdout, size)
        except (EOFError, BrokenPipeError, ValueError):
            raise GhimeraRefused(
                RefusalCode.EXTRACTION_FAILED,
                f"persistent reader exited before answering (after {worker.served} pages)",
            ) from None

    async def run(self, request: bytes) -> bytes:
        loop = asyncio.get_running_loop()
        slots = self._slots.get(loop)
        if slots is None:
            # Semaphores belong to one loop; the workers themselves do not.
            self._slots = {loop: (slots := asyncio.Semaphore(self._max_workers))}
        worker: _Worker | None = None
        try:
            # One deadline covers the slot wait, a cold start and the parse.
            async with asyncio.timeout(self._timeout), slots:
                with self._lock:
                    worker = self._idle.pop() if self._idle else None
                if worker is None:
                    if self._inventory is None:
                        self._inventory = await admit_package()
                    worker = await asyncio.to_thread(self._spawn, self._inventory)
                response = await asyncio.to_thread(self._call, worker, request)
        except TimeoutError:
            if worker is not None:
                self._retire(worker)
            raise GhimeraRefused(
                RefusalCode.BUDGET_EXHAUSTED,
                f"html reader exceeded its page deadline timeout_seconds={self._timeout:g}",
            ) from None
        except OSError as exc:
            if worker is not None:
                self._retire(worker)
            name = errno.errorcode.get(exc.errno or 0, type(exc).__name__)
            raise GhimeraRefused(
                RefusalCode.EXTRACTION_FAILED, f"persistent reader pipe failed with {name}"
            ) from None
        except BaseException:
            if worker is not None:
                self._retire(worker)
            raise
        worker.served += 1
        with self._lock:
            self._idle.append(worker)
        return response
