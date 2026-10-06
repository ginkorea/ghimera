"""Runs inside the network/PID namespace. HTTP is fulfilled only over bounded IPC."""

import asyncio
import hashlib
import json
import os
import sys
import uuid
from urllib.parse import parse_qs, urlsplit

from chimera.browser import BrowserRequest
from chimera.browser_types import (
    RenderResource,
    RenderResult,
    ResourceRequest,
    ResourceResponse,
    WorkerResult,
)
from chimera.refusals import ChimeraRefused, RefusalCode


async def render(request: BrowserRequest) -> RenderResult:
    from patchright.async_api import Route, WebSocketRoute, async_playwright

    config, page = request.config, request.page
    retained: list[RenderResource] = []
    lock = asyncio.Lock()
    navigation_seen = False
    worker_namespace = os.readlink("/proc/self/ns/net")
    if worker_namespace == request.parent_network_namespace:
        raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    parts = urlsplit(page.final_url)
    denied_socket_route = f"{parts.scheme}://{parts.netloc}/.__chimera_socket/{uuid.uuid4().hex}"

    def response_headers(headers: tuple[tuple[str, str], ...]) -> dict[str, str]:
        # Bodies are decoded by libcurl. Preserve source CSP/CORS; do not replay
        # compression/framing or create authenticated browser state.
        excluded = {
            "content-length",
            "content-encoding",
            "transfer-encoding",
            "connection",
            "set-cookie",
            "set-cookie2",
        }
        return {key: value for key, value in headers if key.lower() not in excluded}

    async def resource(url: str, method: str, kind: str) -> RenderResource:
        async with lock:
            # Send the attempted request even at the cap. The parent refuses it
            # before I/O and terminates rendering; swallowing a handler exception
            # here would wrongly report a complete render with missing resources.
            query = ResourceRequest(
                sequence=len(retained),
                url=url,
                method=method,
                resource_type=kind,
            )
            sys.stdout.write(query.model_dump_json() + "\n")
            sys.stdout.flush()
            response = ResourceResponse.model_validate_json(
                await asyncio.to_thread(sys.stdin.buffer.readline, config.max_protocol_bytes + 1)
            )
            if response.sequence != query.sequence or (
                response.result.url,
                response.result.method,
                response.result.resource_type,
            ) != (url, method, kind):
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            retained.append(response.result)
            return response.result

    async def handle(route: Route) -> None:
        nonlocal navigation_seen
        incoming = route.request
        if incoming.url.startswith(denied_socket_route + "?"):
            targets = parse_qs(urlsplit(incoming.url).query).get("url", [])
            if len(targets) != 1:
                raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            await resource(targets[0], "GET", "websocket")
            await route.fulfill(status=204, body=b"")
            return
        if incoming.is_navigation_request() and incoming.resource_type == "document":
            if not navigation_seen and incoming.url == page.final_url and incoming.method == "GET":
                navigation_seen = True
                await route.fulfill(
                    status=page.status,
                    content_type=page.content_type,
                    body=page.body,
                    headers=response_headers(page.headers),
                )
                return
            await resource(incoming.url, incoming.method, "document")
            await route.abort("blockedbyclient")
            return
        result = await resource(incoming.url, incoming.method, incoming.resource_type)
        if result.refusal is not None:
            await route.abort("blockedbyclient")
        else:
            # Headers/cookies/credentials supplied by the page are never forwarded.
            # Parent owns URL, scope, DNS, TLS, Tor, robots, redirects and budgets.
            await route.fulfill(
                status=result.status,
                content_type=result.content_type,
                body=result.body,
                headers=response_headers(result.headers),
            )

    async def websocket(socket: WebSocketRoute) -> None:
        await resource(socket.url, "GET", "websocket")
        await socket.close()

    async with async_playwright() as driver:
        browser = await driver.chromium.launch(
            executable_path=str(config.executable),
            headless=True,
            args=["--disable-dev-shm-usage"],
        )
        try:
            context = await browser.new_context(
                service_workers="block",
                accept_downloads=False,
                user_agent=request.user_agent,
                viewport={"width": config.viewport_width, "height": config.viewport_height},
            )
            await context.route("**/*", handle)
            await context.route_web_socket("**/*", websocket)

            # Patchright's exposed bindings are isolated from page scripts. An
            # intercepted private route reports refusals without such a binding
            # or any real network connection. This reporting URL is never fetched.
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
            await view.goto(
                page.final_url, wait_until="domcontentloaded", timeout=config.timeout_seconds * 1000
            )
            if config.ready_selector is not None:
                await view.locator(config.ready_selector).wait_for(
                    state="attached",
                    timeout=config.timeout_seconds * 1000,
                )
            await asyncio.sleep(config.settle_seconds)
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
            await browser.close()


def main() -> None:
    # Parent enforces cumulative input/output limits; validate wire data here too.
    try:
        request = BrowserRequest.model_validate_json(sys.stdin.buffer.readline())
        outcome = WorkerResult(result=asyncio.run(render(request)))
    except ChimeraRefused as exc:
        outcome = WorkerResult(refusal=exc.code)
    except Exception:
        # No vendor stack trace, page content or local environment goes to logs.
        outcome = WorkerResult(refusal=RefusalCode.FETCH_FAILED)
    sys.stdout.write(outcome.model_dump_json() + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
