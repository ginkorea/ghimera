"""Network-isolated Chromium: every redirect hop is locally fulfilled over CDP.

Vendor CDP dictionaries are narrowed here. No page-supplied request header,
cookie, credential or POST body is sent to the parent fetcher.
"""

import asyncio
import base64
import hashlib
import json
import os
import sys
import uuid
from collections import OrderedDict
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from chimera.browser import BrowserRequest
from chimera.browser_types import (
    RenderResource,
    RenderResult,
    ResourceRequest,
    ResourceResponse,
    WorkerResult,
)
from chimera.refusals import ChimeraRefused, RefusalCode


class CdpRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    url: str = Field(min_length=1)
    method: str = Field(min_length=1)


class PausedRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    request_id: str = Field(alias="requestId", min_length=1)
    request: CdpRequest
    resource_type: str = Field(alias="resourceType", min_length=1)
    redirected_request_id: str | None = Field(default=None, alias="redirectedRequestId")
    response_status: int | None = Field(default=None, alias="responseStatusCode")
    response_error: str | None = Field(default=None, alias="responseErrorReason")
    network_id: str | None = Field(default=None, alias="networkId")


class NetworkRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    request_id: str = Field(alias="requestId", min_length=1)
    resource_type: str = Field(alias="type", min_length=1)


def response_headers(
    headers: tuple[tuple[str, str], ...], content_type: str
) -> list[dict[str, str]]:
    # An array retains repeated CSP headers; a dict would discard policies.
    excluded = {
        "content-length",
        "content-encoding",
        "transfer-encoding",
        "connection",
        "set-cookie",
        "set-cookie2",
    }
    kept = [{"name": key, "value": value} for key, value in headers if key.lower() not in excluded]
    if not any(item["name"].lower() == "content-type" for item in kept):
        kept.append({"name": "content-type", "value": content_type})
    return kept


async def render(request: BrowserRequest) -> RenderResult:
    from patchright.async_api import Page as BrowserPage
    from patchright.async_api import WebSocketRoute, async_playwright

    config, page = request.config, request.page
    retained: list[RenderResource] = []
    lock = asyncio.Lock()
    navigation_seen = False
    requests: dict[str, int] = {}
    kinds: OrderedDict[str, str] = OrderedDict()
    kind_waiters: dict[str, asyncio.Future[str]] = {}
    worker_namespace = os.readlink("/proc/self/ns/net")
    if worker_namespace == request.parent_network_namespace:
        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    parts = urlsplit(page.final_url)
    denied_socket_route = f"{parts.scheme}://{parts.netloc}/.__chimera_socket/{uuid.uuid4().hex}"

    async def resource(
        url: str, method: str, kind: str, previous: int | None = None
    ) -> tuple[RenderResource, int]:
        async with lock:
            query = ResourceRequest(
                sequence=len(retained),
                url=url,
                method=method,
                resource_type=kind,
                redirected_from=previous,
            )
            # Send over-cap attempts too; parent terminates them before I/O.
            sys.stdout.write(query.model_dump_json() + "\n")
            sys.stdout.flush()
            response = ResourceResponse.model_validate_json(
                await asyncio.to_thread(sys.stdin.buffer.readline, config.max_protocol_bytes + 1)
            )
            if response.sequence != query.sequence or (
                response.result.url,
                response.result.method,
                response.result.resource_type,
                response.result.redirected_from,
            ) != (url, method, kind, previous):
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            retained.append(response.result)
            return response.result, query.sequence

    async def websocket(socket: WebSocketRoute) -> None:
        await resource(socket.url, "GET", "websocket")
        await socket.close()

    async with async_playwright() as driver:
        browser = await driver.chromium.launch(
            executable_path=str(config.executable),
            headless=True,
            args=["--disable-dev-shm-usage"],
        )
        pending: set[asyncio.Task[None]] = set()
        failure: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        ready: asyncio.Task[None] | None = None
        try:
            context = await browser.new_context(
                service_workers="block",
                accept_downloads=False,
                user_agent=request.user_agent,
                viewport={"width": config.viewport_width, "height": config.viewport_height},
            )
            await context.route_web_socket("**/*", websocket)
            await context.add_init_script(
                """(() => {
                const report = globalThis.fetch.bind(globalThis);
                const denied = function(url) {
                    report(REPORT_URL + '?url=' + encodeURIComponent(String(url))).catch(()=>{});
                    throw new DOMException(
                        'Socket transport is outside crawl scope','SecurityError');
                };
                Object.defineProperty(globalThis,'WebSocket',{value:denied,writable:false});
            })();""".replace("REPORT_URL", json.dumps(denied_socket_route))
            )
            view = await context.new_page()
            session = await context.new_cdp_session(view)

            async def fulfill(
                request_id: str,
                status: int,
                mime: str,
                body: bytes,
                headers: tuple[tuple[str, str], ...] = (),
            ) -> None:
                await session.send(
                    "Fetch.fulfillRequest",
                    {
                        "requestId": request_id,
                        "responseCode": status,
                        "responseHeaders": response_headers(headers, mime),
                        "body": base64.b64encode(body).decode("ascii"),
                    },
                )

            async def handle(value: object) -> None:
                nonlocal navigation_seen
                incoming = PausedRequest.model_validate(value)
                if incoming.response_status is not None or incoming.response_error is not None:
                    raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                url, method = incoming.request.url, incoming.request.method
                if url.startswith(denied_socket_route + "?"):
                    targets = parse_qs(urlsplit(url).query).get("url", [])
                    if len(targets) != 1:
                        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                    await resource(targets[0], "GET", "websocket")
                    await fulfill(incoming.request_id, 204, "text/plain", b"")
                    return
                if incoming.resource_type == "Document" and (
                    not navigation_seen and url == page.final_url and method == "GET"
                ):
                    navigation_seen = True
                    await fulfill(
                        incoming.request_id, page.status, page.content_type, page.body, page.headers
                    )
                    return
                previous = None
                if incoming.redirected_request_id is not None:
                    previous = requests.get(incoming.redirected_request_id)
                    if previous is None:
                        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                # Fetch may classify fetch() as XHR at request pause; Network
                # supplies the precise classification ordinary routing uses.
                kind = incoming.resource_type.lower()
                if incoming.network_id is not None:
                    cached = kinds.pop(incoming.network_id, None)
                    if cached is not None:
                        kind = cached
                    else:
                        waiter: asyncio.Future[str] = asyncio.get_running_loop().create_future()
                        kind_waiters[incoming.network_id] = waiter
                        try:
                            kind = await waiter
                        finally:
                            kind_waiters.pop(incoming.network_id, None)
                result, index = await resource(url, method, kind, previous)
                requests[incoming.request_id] = index
                if result.refusal is not None:
                    await session.send(
                        "Fetch.failRequest",
                        {
                            "requestId": incoming.request_id,
                            "errorReason": "BlockedByClient",
                        },
                    )
                    return
                if result.status is None or result.content_type is None:
                    raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
                await fulfill(
                    incoming.request_id,
                    result.status,
                    result.content_type,
                    result.body,
                    result.headers,
                )

            def done(task: asyncio.Task[None]) -> None:
                pending.discard(task)
                if not task.cancelled():
                    error = task.exception()
                    if error is not None and not failure.done():
                        failure.set_exception(error)

            def paused(value: object) -> None:
                if len(pending) > config.max_resources:
                    if not failure.done():
                        failure.set_exception(ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED))
                    return
                task = asyncio.create_task(handle(value))
                pending.add(task)
                task.add_done_callback(done)

            async def popup(other: BrowserPage) -> None:
                await resource(other.url or "about:blank", "GET", "document")
                await other.close()

            context.on("page", popup)

            def network_request(value: object) -> None:
                try:
                    packet = NetworkRequest.model_validate(value)
                except ValueError:
                    if not failure.done():
                        failure.set_exception(ChimeraRefused(RefusalCode.ADAPTER_CONTRACT))
                    return
                waiter = kind_waiters.get(packet.request_id)
                if waiter is not None and not waiter.done():
                    waiter.set_result(packet.resource_type.lower())
                else:
                    kinds[packet.request_id] = packet.resource_type.lower()
                    while len(kinds) > config.max_resources + 1:
                        kinds.popitem(last=False)

            session.on("Network.requestWillBeSent", network_request)
            session.on("Fetch.requestPaused", paused)
            # Ordinary routing can miss redirected URLs; Fetch reports each hop
            # with redirectedRequestId. Never continue a real network request.
            await session.send("Network.enable")
            await session.send("Network.setCacheDisabled", {"cacheDisabled": True})
            await session.send(
                "Fetch.enable",
                {
                    "patterns": [{"urlPattern": "*", "requestStage": "Request"}],
                },
            )

            async def readiness() -> None:
                await view.goto(
                    page.final_url,
                    wait_until="domcontentloaded",
                    timeout=config.timeout_seconds * 1000,
                )
                if config.ready_selector is not None:
                    await view.locator(config.ready_selector).wait_for(
                        state="attached",
                        timeout=config.timeout_seconds * 1000,
                    )
                await asyncio.sleep(config.settle_seconds)

            ready = asyncio.create_task(readiness())
            completed, _ = await asyncio.wait((ready, failure), return_when=asyncio.FIRST_COMPLETED)
            if failure in completed:
                await failure
            await ready
            while pending:
                await asyncio.gather(*tuple(pending))
            if failure.done():
                await failure
            if view.url != page.final_url:
                raise ChimeraRefused(RefusalCode.OUT_OF_SCOPE)
            html = (await view.content()).encode()
            if len(html) > config.max_rendered_bytes:
                raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            await context.close()
            return RenderResult(
                schema="chimera.browser-render/1",
                source_url=page.final_url,
                source_sha256=hashlib.sha256(page.body).hexdigest(),
                html=html,
                html_sha256=hashlib.sha256(html).hexdigest(),
                config_digest=config.content_digest(),
                browser_sha256=config.executable_sha256,
                driver_revision="patchright@1.63.0",
                network_isolation="linux_network_namespace",
                parent_network_namespace=request.parent_network_namespace,
                worker_network_namespace=worker_namespace,
                resources=tuple(retained),
            )
        finally:
            if ready is not None and not ready.done():
                ready.cancel()
            for task in tuple(pending):
                task.cancel()
            await asyncio.gather(
                *tuple(pending), *([ready] if ready is not None else []), return_exceptions=True
            )
            if failure.done():
                failure.exception()
            else:
                failure.cancel()
            await browser.close()


def main() -> None:
    try:
        request = BrowserRequest.model_validate_json(sys.stdin.buffer.readline())
        outcome = WorkerResult(result=asyncio.run(render(request)))
    except ChimeraRefused as exc:
        outcome = WorkerResult(refusal=exc.code)
    except Exception:
        outcome = WorkerResult(refusal=RefusalCode.FETCH_FAILED)
    sys.stdout.write(outcome.model_dump_json() + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
