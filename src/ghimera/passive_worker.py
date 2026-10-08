"""Owned bounded child lifecycle, shared by passive document parsing providers."""

import asyncio
import os
from pathlib import Path

from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.worker_package import admit_package


def private_directory(path: Path) -> None:
    """Never widen or reuse another owner's state directory."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)


async def read_bounded(stream: asyncio.StreamReader | None, limit: int) -> bytes:
    if stream is None:
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    chunks: list[bytes] = []
    total = 0
    while data := await stream.read(min(65536, limit - total + 1)):
        total += len(data)
        if total > limit:
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
        chunks.append(data)
    return b"".join(chunks)


class PassiveWorker:
    def __init__(
        self,
        *,
        interpreter: Path,
        module: str,
        work_directory: Path,
        max_workers: int,
        timeout_seconds: float,
        max_output_bytes: int,
        max_diagnostic_bytes: int,
        cleanup_timeout_seconds: float,
        environment: dict[str, str],
    ) -> None:
        self._interpreter, self._module, self._directory = interpreter, module, work_directory
        self._slots = asyncio.Semaphore(max_workers)
        self._timeout, self._output, self._diagnostic = (
            timeout_seconds,
            max_output_bytes,
            max_diagnostic_bytes,
        )
        self._environment = dict(environment)
        self._cleanup_timeout = cleanup_timeout_seconds

    async def run(self, request: bytes) -> bytes:
        try:
            async with asyncio.timeout(self._timeout), self._slots:
                private_directory(self._directory)
                inventory = await admit_package()
                async with inventory.project(self._directory) as package_root:
                    environment = self._environment | {
                        "PYTHONPATH": str(package_root),
                        "PYTHONNOUSERSITE": "1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                    }
                    return await self._run(request, environment)
        except TimeoutError:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED) from None
        except OSError:
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED) from None

    async def _run(self, request: bytes, environment: dict[str, str]) -> bytes:
        process: asyncio.subprocess.Process | None = None
        starting: asyncio.Task[asyncio.subprocess.Process] | None = None
        readers: list[asyncio.Task[bytes]] = []
        try:
            async with asyncio.timeout(self._timeout):
                starting = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        str(self._interpreter),
                        "-m",
                        self._module,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=self._directory,
                        env=environment,
                    )
                )
                # Cancelling asyncio's process creation can lose the child handle
                # before assignment. Finish owned startup, then kill/reap it below.
                process = await asyncio.shield(starting)
                if process.stdin is None:
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                readers = [
                    asyncio.create_task(read_bounded(process.stdout, self._output)),
                    asyncio.create_task(read_bounded(process.stderr, self._diagnostic)),
                ]
                process.stdin.write(request)
                await process.stdin.drain()
                process.stdin.close()
                response, _ = await asyncio.gather(*readers)
                if await process.wait() != 0:
                    raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
                return response
        except TimeoutError:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED) from None
        except OSError:
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED) from None
        finally:
            if process is None and starting is not None:
                try:
                    async with asyncio.timeout(self._cleanup_timeout):
                        process = await asyncio.shield(starting)
                except (TimeoutError, OSError):
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
            for task in readers:
                if not task.done():
                    task.cancel()
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)
            if process is not None:
                if process.stdin is not None and not process.stdin.is_closing():
                    process.stdin.transport.abort()
                if process.returncode is None:
                    process.kill()

                # asyncio Process.wait can wait for pipes as well as process exit.
                # Cancelled readers leave paused buffers: drain without retaining
                # bytes before waiting, rather than deadlocking on a killed child.
                async def discard(stream: asyncio.StreamReader | None) -> None:
                    if stream is not None:
                        while await stream.read(65536):
                            pass

                try:
                    async with asyncio.timeout(self._cleanup_timeout):
                        await asyncio.gather(discard(process.stdout), discard(process.stderr))
                        await process.wait()
                except TimeoutError:
                    raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
