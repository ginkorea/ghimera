"""Real isolated Chromium must see each parent-fetched redirect hop."""

import asyncio
import hashlib
import threading
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_browser_render import browser_policy
from test_http_fetch import ResolverFixture, state

from ghimera.browser import IsolatedBrowserRenderer
from ghimera.config import GhimeraConfig
from ghimera.fetch import FetchLadder
from ghimera.http import CurlRoute


@contextmanager
def redirect_site():
    calls = Counter()
    responses = {
        "/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /blocked\n", {}),
        "/plain": (
            200,
            "text/html",
            b"<article></article><noscript>enable javascript</noscript>"
            b"<script src='/script'></script>",
            {},
        ),
        "/script": (302, "text/html", b"first hop", {"Location": "/final.js"}),
        "/final.js": (
            200,
            "application/javascript",
            b"fetch('/data').then(r=>{const url=r.url;return r.json().then(v=>({url,...v}))})"
            b".then(v=>{const a=document.querySelector('article');a.textContent=v.text+' '+v.url;"
            b"a.setAttribute('data-ready','yes')});",
            {},
        ),
        "/data": (307, "text/html", b"second hop", {"Location": "/final.json"}),
        "/final.json": (200, "application/json", b'{"text":"Native port evidence"}', {}),
        "/blocked": (200, "application/json", b'{"text":"must not fetch"}', {}),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls[self.path] += 1
            status, mime, body, headers = responses.get(self.path, (404, "text/plain", b"", {}))
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, calls, responses
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def setup(site, tmp_path, *, ready=True, max_redirects=3):
    _, scope, budget, ledger, origin = state(site)
    data = budget.config.model_dump(by_alias=True)
    data["http"]["max_redirects"] = max_redirects
    data["browser"] = browser_policy(
        tmp_path, ready_selector="article[data-ready]" if ready else None, settle_seconds=0.2
    ).model_dump(by_alias=True)
    config = GhimeraConfig.model_validate(data)
    budget = type(budget)(config, budget.clock)
    ladder = FetchLadder(
        (CurlRoute(config, resolver=ResolverFixture()),), renderer=IsolatedBrowserRenderer(config)
    )
    return ladder, scope, budget, ledger, origin


def test_redirected_script_and_json_keep_final_url_and_every_hop(tmp_path):
    with redirect_site() as site:
        ladder, scope, budget, ledger, origin = setup(site, tmp_path)
        page = asyncio.run(ladder.fetch(origin + "/plain", scope, budget, ledger))
        resources = [item for item in page.rendered.resources if item.refusal is None]
        assert b"Native port evidence" in page.rendered.html
        assert (origin + "/final.json").encode() in page.rendered.html
        assert [item.status for item in resources] == [302, 200, 307, 200]
        assert [item.url.removeprefix(origin) for item in resources] == [
            "/script",
            "/final.js",
            "/data",
            "/final.json",
        ]
        assert [item.redirected_from for item in resources] == [None, 0, None, 2]
        assert all(item.final_url == item.url for item in resources)
        assert all(
            item.source_sha256 == hashlib.sha256(item.body).hexdigest() for item in resources
        )
        assert budget.fetches == sum(site[1].values()) == 6
        assert budget.bytes_read == sum(row.bytes_read for row in ledger.snapshot())
        assert page.body == site[2]["/plain"][2]
        with pytest.raises(ValueError, match="HTTP policy"):
            page.rendered.validate_policy(budget.config.browser, max_redirects=0)
        changed = page.rendered.model_dump(mode="json", by_alias=True)
        changed["resources"][1]["redirected_from"] = 1
        with pytest.raises(ValueError, match="earlier resource"):
            type(page.rendered).model_validate(changed)


@pytest.mark.parametrize(
    "target,refusal",
    [("/blocked", "robots_disallowed"), ("http://other.example/data", "out_of_scope")],
)
def test_denied_redirect_destination_is_not_fetched(tmp_path, target, refusal):
    with redirect_site() as site:
        site[2]["/script"] = (302, "text/html", b"redirect", {"Location": target})
        ladder, scope, budget, ledger, origin = setup(site, tmp_path, ready=False)
        page = asyncio.run(ladder.fetch(origin + "/plain", scope, budget, ledger))
        assert any(
            item.refusal is not None and item.refusal.value == refusal
            for item in page.rendered.resources
        )
        assert site[1][target] == 0
        assert budget.fetches == sum(site[1].values())


def test_redirect_cap_prevents_later_network_request(tmp_path):
    with redirect_site() as site:
        site[2]["/final.js"] = (302, "text/html", b"again", {"Location": "/third.js"})
        ladder, scope, budget, ledger, origin = setup(site, tmp_path, ready=False, max_redirects=1)
        page = asyncio.run(ladder.fetch(origin + "/plain", scope, budget, ledger))
        assert any(
            item.url == origin + "/third.js" and item.refusal.value == "fetch_failed"
            for item in page.rendered.resources
            if item.refusal is not None
        )
        assert site[1]["/third.js"] == 0
        assert site[1]["/script"] == site[1]["/final.js"] == 1
        assert budget.fetches == sum(site[1].values())


def test_redirected_module_import_uses_the_final_directory(tmp_path):
    with redirect_site() as site:
        site[2]["/plain"] = (
            200,
            "text/html",
            b"<article></article><noscript>enable javascript</noscript>"
            b"<script type='module' src='/script'></script>",
            {},
        )
        site[2]["/script"] = (308, "text/html", b"moved", {"Location": "/assets/main.js"})
        site[2]["/assets/main.js"] = (
            200,
            "application/javascript",
            b"import {text} from './dep.js';"
            b"document.querySelector('article').textContent=text;"
            b"document.querySelector('article').setAttribute('data-ready','yes');",
            {},
        )
        site[2]["/assets/dep.js"] = (
            200,
            "application/javascript",
            b"export const text='Relative maritime module';",
            {},
        )
        ladder, scope, budget, ledger, origin = setup(site, tmp_path)
        page = asyncio.run(ladder.fetch(origin + "/plain", scope, budget, ledger))
        assert b">Relative maritime module</article>" in page.rendered.html
        assert site[1]["/assets/dep.js"] == 1
        assert site[1]["/dep.js"] == 0


@pytest.mark.parametrize("allow_cors", [False, True])
def test_cross_origin_redirect_preserves_browser_cors(tmp_path, allow_cors):
    with redirect_site() as site:
        target = f"http://other.example:{site[0]}/final.json"
        site[2]["/data"] = (301, "text/html", b"moved", {"Location": target})
        status, mime, body, _ = site[2]["/final.json"]
        site[2]["/final.json"] = (
            status,
            mime,
            body,
            {"Access-Control-Allow-Origin": "*"} if allow_cors else {},
        )
        ladder, scope, budget, ledger, origin = setup(site, tmp_path, ready=allow_cors)
        scope = scope.model_copy(update={"allowed_hosts": ("fixture.example", "other.example")})
        page = asyncio.run(ladder.fetch(origin + "/plain", scope, budget, ledger))
        assert site[1]["/final.json"] == 1
        if allow_cors:
            assert ("Native port evidence " + target).encode() in page.rendered.html
        else:
            assert b"<article></article>" in page.rendered.html
        assert any(item.url == target and item.status == 200 for item in page.rendered.resources)
        assert budget.fetches == sum(site[1].values())


def test_redirect_loop_stops_before_refetching_same_resource(tmp_path):
    with redirect_site() as site:
        site[2]["/script"] = (303, "text/html", b"loop", {"Location": "/script"})
        ladder, scope, budget, ledger, origin = setup(site, tmp_path, ready=False)
        page = asyncio.run(ladder.fetch(origin + "/plain", scope, budget, ledger))
        assert site[1]["/script"] == 1
        assert any(
            item.refusal is not None and item.refusal.value == "fetch_failed"
            for item in page.rendered.resources
        )


@pytest.mark.parametrize("dark", [False, True])
def test_all_browser_redirect_hops_use_tor_without_local_dns(tmp_path, dark):
    from test_tor_transport import NoLocalDNS, onion, policy, socks_server

    with redirect_site() as site:
        seen = []

        async def exercise():
            server = await socks_server(site[0], seen)
            async with server:
                _, scope, initial_budget, ledger, _ = setup(site, tmp_path)
                data = initial_budget.config.model_dump(by_alias=True)
                transport = policy(server.sockets[0].getsockname()[1]).model_dump(by_alias=True)
                transport["tor"]["allowed_ports"] = [site[0]]
                data["transport"] = transport
                config = GhimeraConfig.model_validate(data)
                host = onion() if dark else "fixture.example"
                scope = scope.model_copy(update={"allowed_hosts": (host,)})
                origin = f"http://{host}:{site[0]}"
                budget = type(initial_budget)(config, initial_budget.clock)
                ladder = FetchLadder(
                    (CurlRoute(config, resolver=NoLocalDNS()),),
                    renderer=IsolatedBrowserRenderer(config),
                )
                page = await ladder.fetch(origin + "/plain", scope, budget, ledger)
                assert b"Native port evidence" in page.rendered.html
                assert budget.fetches == sum(site[1].values()) == 6
                assert all(
                    item.transport.mode == "tor"
                    for item in page.rendered.resources
                    if item.refusal is None
                )
                assert all(
                    row.transport.mode == "tor" for row in ledger.snapshot() if row.event == "fetch"
                )
            await asyncio.sleep(0.01)

        asyncio.run(exercise())
        assert len([row for row in seen if row[0] == 1]) == 6
        assert len([row for row in seen if row[0] == 0xF0]) == (0 if dark else 6)


def test_real_http_preserves_csp_before_browser_script_execution(tmp_path):
    with redirect_site() as site:
        site[2]["/plain"] = (
            200,
            "text/html",
            b"<article data-ready='yes'>Original</article>"
            b"<noscript>enable javascript</noscript><script>"
            b"document.querySelector('article').textContent='Should not execute';</script>",
            {"Content-Security-Policy": "script-src 'none'"},
        )
        ladder, scope, budget, ledger, origin = setup(site, tmp_path)
        page = asyncio.run(ladder.fetch(origin + "/plain", scope, budget, ledger))
        assert b">Original</article>" in page.rendered.html
        assert page.header("content-security-policy") == "script-src 'none'"


def test_retained_headers_preserve_repeated_csp_and_drop_credentials():
    from ghimera.browser_worker import response_headers
    from ghimera.http import BoundedHeaders

    headers = BoundedHeaders(1000)
    for line in (
        b"HTTP/1.1 200 OK\r\n",
        b"Content-Type: text/html; charset=utf-8\r\n",
        b"Content-Security-Policy: script-src 'none'\r\n",
        b"Content-Security-Policy: script-src 'unsafe-inline'\r\n",
        b"Set-Cookie: fixture=private\r\n",
        b"Authorization: fixture-only\r\n",
    ):
        assert headers.write(line) == len(line)
    replay = response_headers(tuple(headers.entries), "text/html")
    assert [item["value"] for item in replay if item["name"] == "content-security-policy"] == [
        "script-src 'none'",
        "script-src 'unsafe-inline'",
    ]
    assert not any(item["name"] in {"set-cookie", "authorization"} for item in replay)
