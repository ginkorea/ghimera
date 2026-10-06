"""Networkless browser worker; the parent exclusively owns every HTTP/Tor fetch."""

import asyncio
import hashlib
import os
import shutil
import signal
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol

from chimera.browser_config import BrowserConfig
from chimera.browser_types import (
    RenderResource,
    RenderResult,
    ResourceRequest,
    ResourceResponse,
    WorkerResult,
)
from chimera.config import ChimeraConfig
from chimera.models import Page, Record, Scope
from chimera.passive_worker import private_directory, read_bounded
from chimera.refusals import ChimeraRefused, RefusalCode
from chimera.response import REDIRECT_STATUSES, redirect_target


class ResourceFetcher(Protocol):
    async def fetch(self, url: str) -> Page: ...


class PageRenderer(Protocol):
    @property
    def name(self) -> str: ...

    def validate_config(self, config: ChimeraConfig) -> None: ...

    async def render(
        self,
        page: Page,
        scope: Scope,
        resources: ResourceFetcher,
        *,
        timeout_seconds: float,
    ) -> RenderResult: ...


class BrowserRequest(Record):
    config: BrowserConfig
    page: Page
    user_agent: str
    parent_network_namespace: str


class IsolatedBrowserRenderer:
    name = "patchright_isolated"

    def __init__(self, config: ChimeraConfig) -> None:
        if config.browser is None:
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self.config, self._user_agent = config.browser, config.user_agent
        self._redirect_limit = config.http.max_redirects if config.http is not None else 0
        try:
            if version("patchright") != "1.63.0":
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            self._check_artifacts()
        except (OSError, PackageNotFoundError):
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        self._slots = asyncio.Semaphore(self.config.max_workers)

    def _check_artifacts(self) -> None:
        for path in (self.config.executable, self.config.isolator):
            if not path.is_file() or not os.access(path, os.X_OK):
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        with self.config.executable.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != self.config.executable_sha256:
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    def validate_config(self, config: ChimeraConfig) -> None:
        if (
            config.browser != self.config
            or config.user_agent != self._user_agent
            or (config.http.max_redirects if config.http is not None else 0) != self._redirect_limit
        ):
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    async def render(
        self,
        page: Page,
        scope: Scope,
        resources: ResourceFetcher,
        *,
        timeout_seconds: float,
    ) -> RenderResult:
        if (
            page.rendered is not None
            or not scope.permits(page.final_url)
            or page.content_type != "text/html"
            or not page.body
        ):
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if len(page.body) > self.config.max_input_bytes:
            raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        request = (
            BrowserRequest(
                config=self.config,
                page=page,
                user_agent=self._user_agent,
                parent_network_namespace=os.readlink("/proc/self/ns/net"),
            )
            .model_dump_json()
            .encode()
            + b"\n"
        )
        if len(request) > self.config.max_protocol_bytes:
            raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        try:
            async with (
                asyncio.timeout(min(timeout_seconds, self.config.timeout_seconds)),
                self._slots,
            ):
                self._check_artifacts()
                return await self._run(request, page, scope, resources)
        except TimeoutError:
            raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED) from None
        except (OSError, ValueError):
            raise ChimeraRefused(RefusalCode.FETCH_FAILED) from None

    async def _resource(
        self,
        request: ResourceRequest,
        scope: Scope,
        resources: ResourceFetcher,
        retained: list[RenderResource],
    ) -> RenderResource:
        if (
            request.method != "GET"
            or request.resource_type not in self.config.resource_types
            or not scope.permits(request.url)
        ):
            return RenderResource(
                url=request.url,
                method=request.method,
                resource_type=request.resource_type,
                refusal=RefusalCode.OUT_OF_SCOPE,
                redirected_from=request.redirected_from,
            )
        try:
            # The untrusted worker supplies an index, not authority. Reconstruct
            # the chain from parent-retained responses before any new source I/O.
            previous_index = request.redirected_from
            urls = {request.url}
            hops = 0
            while previous_index is not None:
                if previous_index >= len(retained):
                    raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                previous = retained[previous_index]
                if (
                    previous.refusal is not None
                    or previous.status not in REDIRECT_STATUSES
                    or previous.resource_type != request.resource_type
                    or (
                        hops == 0 and redirect_target(previous.url, previous.headers) != request.url
                    )
                ):
                    raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                hops += 1
                if hops > self._redirect_limit or previous.url in urls:
                    raise ChimeraRefused(RefusalCode.FETCH_FAILED)
                urls.add(previous.url)
                previous_index = previous.redirected_from
            page = await resources.fetch(request.url)
            if not scope.permits(page.final_url):
                raise ChimeraRefused(RefusalCode.OUT_OF_SCOPE)
            if page.final_url != request.url:
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            if page.status in REDIRECT_STATUSES:
                target = redirect_target(page.final_url, page.headers)
                if target is None:
                    raise ChimeraRefused(RefusalCode.FETCH_FAILED)
                if not scope.permits(target):
                    raise ChimeraRefused(RefusalCode.OUT_OF_SCOPE)
            elif page.status >= 300:
                raise ChimeraRefused(RefusalCode.FETCH_FAILED)
            elif page.content_type not in self.config.resource_content_types:
                raise ChimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
            if len(page.body) > self.config.max_input_bytes:
                raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            return RenderResource(
                url=request.url,
                method=request.method,
                resource_type=request.resource_type,
                final_url=page.final_url,
                status=page.status,
                content_type=page.content_type,
                body=page.body,
                source_sha256=hashlib.sha256(page.body).hexdigest(),
                transport=page.transport,
                source_session=page.source_session,
                headers=page.headers,
                redirected_from=request.redirected_from,
            )
        except ChimeraRefused as exc:
            return RenderResource(
                url=request.url,
                method=request.method,
                resource_type=request.resource_type,
                refusal=exc.code,
                redirected_from=request.redirected_from,
            )

    async def _run(
        self,
        request: bytes,
        page: Page,
        scope: Scope,
        resources: ResourceFetcher,
    ) -> RenderResult:
        private_directory(self.config.work_directory)
        scratch = Path(tempfile.mkdtemp(prefix="render-", dir=self.config.work_directory))
        process: asyncio.subprocess.Process | None = None
        starting: asyncio.Task[asyncio.subprocess.Process] | None = None
        diagnostic: asyncio.Task[bytes] | None = None
        retained: list[RenderResource] = []
        total = len(request)
        try:
            # Browser AND driver inherit a new OS network namespace. No browser
            # socket, DNS, service worker or WebRTC can reach the parent's network.
            starting = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    str(self.config.isolator),
                    "--unshare-net",
                    "--unshare-pid",
                    "--die-with-parent",
                    "--new-session",
                    "--ro-bind",
                    "/",
                    "/",
                    "--tmpfs",
                    str(self.config.sandbox_work_directory.parent),
                    "--bind",
                    str(scratch),
                    str(self.config.sandbox_work_directory),
                    "--proc",
                    "/proc",
                    "--dev",
                    "/dev",
                    "--chdir",
                    str(self.config.sandbox_work_directory),
                    "--",
                    sys.executable,
                    "-m",
                    "chimera.browser_worker",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                    limit=self.config.max_protocol_bytes + 1,
                    env={
                        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                        "PYTHONNOUSERSITE": "1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "TMPDIR": str(self.config.sandbox_work_directory),
                        "XDG_CACHE_HOME": str(self.config.sandbox_work_directory / "cache"),
                        "XDG_CONFIG_HOME": str(self.config.sandbox_work_directory / "config"),
                    },
                )
            )
            process = await asyncio.shield(starting)
            if process.stdin is None or process.stdout is None:
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            diagnostic = asyncio.create_task(
                read_bounded(
                    process.stderr,
                    self.config.max_diagnostic_bytes,
                )
            )
            process.stdin.write(request)
            await process.stdin.drain()
            while True:
                line = await process.stdout.readline()
                total += len(line)
                if total > self.config.max_protocol_bytes:
                    raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
                if not line:
                    raise ChimeraRefused(RefusalCode.FETCH_FAILED)
                if diagnostic.done():
                    diagnostic.result()  # Refuse overflow rather than logging vendor stderr.
                try:
                    requested = ResourceRequest.model_validate_json(line)
                except ValueError:
                    outcome = WorkerResult.model_validate_json(line)
                    if outcome.refusal is not None:
                        raise ChimeraRefused(outcome.refusal) from None
                    result = outcome.result
                    if result is None or (
                        result.resources != tuple(retained)
                        or result.source_url != page.final_url
                        or result.source_sha256 != hashlib.sha256(page.body).hexdigest()
                        or result.config_digest != self.config.content_digest()
                        or result.browser_sha256 != self.config.executable_sha256
                        or result.parent_network_namespace != os.readlink("/proc/self/ns/net")
                        or len(result.html) > self.config.max_rendered_bytes
                    ):
                        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
                    try:
                        result.validate_policy(self.config, max_redirects=self._redirect_limit)
                    except ValueError:
                        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
                    process.stdin.close()
                    await diagnostic
                    if await process.wait() != 0:
                        raise ChimeraRefused(RefusalCode.FETCH_FAILED) from None
                    return result
                if requested.sequence != len(retained):
                    raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                if len(retained) >= self.config.max_resources:
                    raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
                fetched = await self._resource(requested, scope, resources, retained)
                retained.append(fetched)
                response = (
                    ResourceResponse(
                        sequence=requested.sequence,
                        result=fetched,
                    )
                    .model_dump_json()
                    .encode()
                    + b"\n"
                )
                total += len(response)
                if total > self.config.max_protocol_bytes:
                    raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
                process.stdin.write(response)
                await process.stdin.drain()
        finally:
            if process is None and starting is not None:
                async with asyncio.timeout(self.config.cleanup_timeout_seconds):
                    process = await asyncio.shield(starting)
            if diagnostic is not None and not diagnostic.done():
                diagnostic.cancel()
            if diagnostic is not None:
                await asyncio.gather(diagnostic, return_exceptions=True)
            if process is not None:
                if process.stdin is not None and not process.stdin.is_closing():
                    process.stdin.transport.abort()
                # Only this subprocess's fresh session is targeted. Descendants
                # live in the child PID namespace, not shared browser sessions.
                if process.returncode is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

                async def discard(stream: asyncio.StreamReader | None) -> None:
                    if stream is not None:
                        while await stream.read(65536):
                            pass

                async with asyncio.timeout(self.config.cleanup_timeout_seconds):
                    await asyncio.gather(discard(process.stdout), discard(process.stderr))
                    await process.wait()
            shutil.rmtree(scratch)  # Exactly the owned mkdtemp directory, after reaping.
