"""Real Chromium pagination and route refusal; no fixture claims a verified Tor exit."""

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from pydantic import ValidationError

from ghimera.browser_tor import BrowserTorConfig, TorProbeObservation, TorProbeReply
from ghimera.budget import RunBudget
from ghimera.human_browser import BoundPageHumanSession
from ghimera.human_browser_types import AssistanceDecision, BrowserCapture, HumanBrowserConfig
from ghimera.ledger import Ledger
from ghimera.models import Scope
from ghimera.refusals import GhimeraRefused
from tests.test_browser_guarded_collection import effective, ladder_for
from tests.test_browser_navigation_guard import navigation_policy, page_policy
from tests.test_http_fetch import state
from tests.test_human_browser import with_browser


@contextmanager
def pages(*, assisted=False):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            if self.path == "/robots.txt":
                body = b"User-agent: *\nDisallow:\n"
            elif self.path == "/research/page1":
                if assisted and "fixture_complete=yes" not in self.headers.get("Cookie", ""):
                    body = b"""<h1>Verify you are human</h1><button id='human'>Continue</button>
                    <script>document.querySelector('#human').onclick=()=>{
                    document.cookie='fixture_complete=yes; Path=/'; location.reload();};</script>"""
                else:
                    body = (
                        "<article>第一頁原文</article><a id='next' href='/research/page2'>Next</a>"
                    ).encode()
            else:
                body = "<article>第二頁保留的港口證據</article>".encode()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/plain" if self.path == "/robots.txt" else "text/html; charset=utf-8",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *unused):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_port, calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


@pytest.mark.parametrize("assisted", [False, True])
def test_native_guarded_pagination_preserves_landing_original_and_actual_budget(tmp_path, assisted):
    with pages(assisted=assisted) as (port, calls):
        origin = f"http://fixture.example:{port}"

        async def operation(selected, page, unrelated):
            raw = page_policy(selected).model_dump()
            raw.update(
                navigation=navigation_policy(),
                assistance_reasons=("challenge_not_solved",) if assisted else (),
                max_assistance_attempts=1 if assisted else 0,
                pagination=[
                    dict(
                        source_url=origin + "/research/page2",
                        navigation_url=origin + "/research/page1",
                        selector="#next",
                    )
                ],
            )
            selected = HumanBrowserConfig.model_validate(raw)
            cfg = effective(tmp_path, (port, {}, {}, b"", {}), selected)
            previous = state((port, {}, {}, b""))[2]
            budget = RunBudget(cfg, clock=previous.clock)
            ledger = Ledger()
            scope = Scope(
                allowed_hosts=("fixture.example",),
                allowed_ports=(port,),
                max_depth=0,
                content_types=("text/html",),
            )

            class FixtureHuman:
                async def assist(self, request):
                    await page.locator("#human").click()
                    await page.locator("#next").wait_for()
                    return AssistanceDecision(
                        request_digest=request.content_digest(), action="resume"
                    )

            page_result = await ladder_for(cfg, page, FixtureHuman() if assisted else None).fetch(
                origin + "/research/page2", scope, budget, ledger
            )
            evidence = page_result.human_browser
            assert "第二頁" in page_result.body.decode()
            assert evidence.pagination is not None
            assert "第一頁" in evidence.pagination.landing_dom.decode()
            assert evidence.pagination.action.selector == "#next"
            assert evidence.collector_bytes_read == len(page_result.body) + len(
                evidence.pagination.landing_dom
            ) + sum(item.request.observed_dom_bytes for item in evidence.assistance)
            assert len(evidence.assistance) == int(assisted)
            assert calls.count("/research/page1") == (3 if assisted else 1)
            assert calls.count("/research/page2") == 1
            assert [row.browser_action.url for row in ledger.snapshot() if row.browser_action] == [
                origin + "/research/page1",
            ] + ([origin + "/research/page1"] if assisted else []) + [
                origin + "/research/page2",
            ]
            capture = BrowserCapture(dom=page_result.body, evidence=evidence)
            assert BrowserCapture.model_validate_json(capture.model_dump_json()) == capture
            capture.validate_policy(selected)

        asyncio.run(with_browser(tmp_path, origin, operation))


def tor_policy():
    return BrowserTorConfig(
        schema="ghimera.browser-tor/1",
        proxy_url="socks5://127.0.0.1:9050",
        probe_url="https://route.example/api/ip",
        max_probe_bytes=1024,
        required_resolver_rule="MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
    )


def test_tor_launch_route_admission_rejects_direct_fallback_and_ambiguous_flags():
    selected = tor_policy()
    arguments = (
        "--proxy-server=" + selected.proxy_url,
        "--proxy-bypass-list=<-loopback>",
        "--host-resolver-rules=" + selected.required_resolver_rule,
    )
    selected.admit_command_line(arguments)
    for changed in [
        (),
        arguments + ("--no-proxy-server",),
        arguments + (arguments[0],),
        arguments + ("--proxy-pac-url=http://proxy.example",),
    ]:
        with pytest.raises(ValueError):
            selected.admit_command_line(changed)
    for data in [
        dict(IsTor=False, IP="1.1.1.1"),
        dict(IsTor=True, IP="127.0.0.1"),
        dict(IsTor="true", IP="1.1.1.1"),
    ]:
        with pytest.raises(ValidationError):
            TorProbeReply.model_validate(data)


def test_malformed_native_probe_metadata_is_a_safe_refusal_observation():
    observation = TorProbeObservation(tor_policy().probe_url)
    observation.observe(dict(type="Document", response=dict(headers={"secret": "fixture-only"})))
    assert observation.failed and observation.response is None


def test_actual_direct_browser_cannot_masquerade_as_tor_before_source_contact(tmp_path):
    with pages() as (port, calls):
        origin = f"http://fixture.example:{port}"

        async def operation(selected, page, unrelated):
            raw = page_policy(selected).model_dump()
            raw.update(
                navigation=navigation_policy(),
                declared_route="tor",
                tor_verification=tor_policy(),
                assistance_reasons=(),
                max_assistance_attempts=0,
            )
            raw["origins"] = raw["origins"] + (
                dict(origin="https://route.example", path_prefixes=["/api"], allow_http=False),
            )
            selected = HumanBrowserConfig.model_validate(raw)
            # Native route verification happens before the first probe or source request.
            from ghimera.browser_operation_types import BrowserOperation

            class Admission:
                async def admit(self, url):
                    raise AssertionError("direct browser cannot reach admission")

            class Output:
                bytes_read = 0

                def reading(self, maximum):
                    raise AssertionError("direct browser cannot spend probe bytes")

            class ScopeFixture:
                def permits(self, url):
                    return True

            with pytest.raises(GhimeraRefused):
                await BoundPageHumanSession(selected, page=page, assistant=None).capture(
                    origin + "/research/page2",
                    scope=ScopeFixture(),
                    operation=BrowserOperation(admission=Admission(), output=Output()),
                )
            assert not calls

        asyncio.run(with_browser(tmp_path, origin, operation))
