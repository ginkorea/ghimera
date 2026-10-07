"""Explicit human/browser contracts and actual same-session Chromium capture."""

import asyncio
import hashlib
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from pydantic import ValidationError

from ghimera.collector import Collector
from ghimera.config import GhimeraConfig
from ghimera.human_browser import ChromiumHumanSession
from ghimera.human_browser_types import (
    AssistanceDecision,
    BrowserCapture,
    HumanBrowserConfig,
)
from ghimera.refusals import GhimeraRefused


def policy(**updates):
    values = dict(
        schema="ghimera.human-browser/1",
        adapter="patchright_cdp",
        adapter_revision="ghimera-human-chromium/1",
        driver_version="1.63.0",
        session_id="dedicated-research-session",
        control_endpoint="ws://127.0.0.1:9222/devtools/browser/fixture-only",
        target_id="fixture-target",
        lifecycle="caller_managed",
        network_boundary="operator_managed_browser",
        declared_route="direct",
        origins=[
            dict(origin="https://publisher.example", path_prefixes=["/research"], allow_http=False)
        ],
        assistance_reasons=["challenge_not_solved", "login_wall", "paywall"],
        max_assistance_attempts=2,
        timeout_seconds=20.0,
        assistance_timeout_seconds=5.0,
        cleanup_timeout_seconds=3.0,
        max_dom_bytes=100_000,
    )
    values.update(updates)
    return HumanBrowserConfig.model_validate(values)


def test_policy_is_explicit_and_its_capture_scope_is_exact():
    selected = policy()
    assert selected.permits("https://publisher.example/research/article")
    assert not selected.permits("https://publisher.example/researcher/article")
    assert not selected.permits("https://other.example/research/article")
    assert not selected.permits("https://publisher.example/research/%2foutside")
    assert HumanBrowserConfig.model_validate_json(selected.model_dump_json()) == selected


@pytest.mark.parametrize(
    "changes",
    [
        {"control_endpoint": "ws://0.0.0.0:9222/devtools/browser/id"},
        {"control_endpoint": "http://127.0.0.1:9222"},
        {"control_endpoint": "ws://localhost:9222/devtools/browser/id"},
        {"control_endpoint": "ws://user:secret@127.0.0.1:9222/devtools/browser/id"},
        {"control_endpoint": "ws://127.0.0.1:9222/devtools/browser/id?token=secret"},
        {"target_id": ""},
        {"max_assistance_attempts": -1},
        {"timeout_seconds": float("inf")},
        {"assistance_reasons": ["budget_exhausted"]},
        {"origins": [dict(origin="http://example.org", path_prefixes=["/"], allow_http=False)]},
        {"origins": [dict(origin="http://example.onion", path_prefixes=["/"], allow_http=True)]},
    ],
)
def test_invalid_policy_refuses_before_contact(changes):
    with pytest.raises(ValidationError):
        policy(**changes)


def test_missing_assistance_binding_and_unverified_tor_refuse_before_contact():
    with pytest.raises(GhimeraRefused, match="source_session_unavailable"):
        ChromiumHumanSession(policy(), assistant=None)
    with pytest.raises(GhimeraRefused, match="tor_unavailable"):
        ChromiumHumanSession(policy(declared_route="tor"), assistant=object())


def test_recipe_boundary_preserves_legacy_and_cannot_silently_ignore_capture():
    from pathlib import Path

    original = GhimeraConfig.from_toml(Path("examples/chimera.toml"))
    assert "human_browser" not in original.model_dump()
    data = original.model_dump()
    data["human_browser"] = policy().model_dump()
    configured = GhimeraConfig.model_validate(data)
    assert GhimeraConfig.model_validate_json(configured.model_dump_json()) == configured
    with pytest.raises(GhimeraRefused, match="source_session_unavailable"):
        Collector(configured)


def test_browser_cannot_override_configured_tor_source_routing():
    from pathlib import Path

    from tests.test_tor_transport import policy as tor_policy

    original = GhimeraConfig.from_toml(Path("examples/chimera.toml"))
    values = original.model_dump()
    values.update(
        human_browser=policy().model_dump(),
        transport=tor_policy(9050).model_dump(),
    )
    with pytest.raises(ValidationError, match="browser declaration"):
        GhimeraConfig.model_validate(values)


@contextmanager
def interactive_source():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/research/outside":
                html = "<script>location.href='/outside';</script>"
            elif self.path == "/research/large":
                html = "<article>" + "測試" * 100_000 + "</article>"
            elif self.path == "/research/tamper":
                html = """<article>Native evidence from the actual DOM.</article><script>
                    window.TextEncoder=class {encode(){return new Uint8Array([102,97,107,101]);}};
                    window.btoa=()=>"ZmFrZQ==";
                </script>"""
            elif "human_fixture=complete" in self.headers.get("Cookie", ""):
                html = "<article id='evidence'>原始文件：同一瀏覽器保留的研究證據。</article>"
            else:
                html = """<title>Just a moment</title><p>Verify you are human</p>
                <button id='human'>Complete fixture interaction</button><script>
                document.querySelector('#human').onclick=()=>{
                  document.cookie='human_fixture=complete; Path=/'; location.reload();
                };</script>"""
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


async def with_browser(tmp_path, origin, operation, *, accept_downloads=None):
    from patchright.async_api import async_playwright

    executable = os.environ["CHIMERA_TEST_BROWSER"]
    profile = tmp_path / "caller-profile"
    async with async_playwright() as owner:
        context = await owner.chromium.launch_persistent_context(
            str(profile),
            executable_path=executable,
            headless=True,
            accept_downloads=accept_downloads,
            args=[
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                "--no-proxy-server",
                "--host-resolver-rules=MAP fixture.example 127.0.0.1",
            ],
        )
        try:
            # Chromium/Patchright may retire its startup about:blank target.
            # Explicit owned tabs make the non-first-target assertion stable.
            unrelated = await context.new_page()
            await unrelated.set_content("<p>Unrelated private fixture tab</p>")
            page = await context.new_page()
            cdp = await context.new_cdp_session(page)
            target_id = (await cdp.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
            await cdp.detach()
            lines = (profile / "DevToolsActivePort").read_text().splitlines()
            selected = policy(
                control_endpoint=f"ws://127.0.0.1:{lines[0]}{lines[1]}",
                target_id=target_id,
                origins=[dict(origin=origin, path_prefixes=["/research"], allow_http=True)],
            )
            await operation(selected, page, unrelated)
            assert not page.is_closed()
            assert not unrelated.is_closed()
            assert "Unrelated private fixture tab" in await unrelated.content()
        finally:
            await context.close()


def test_real_same_browser_assistance_and_capture_preserve_caller_tabs(tmp_path):
    with interactive_source() as origin:

        async def operation(selected, page, unrelated):
            requests = []

            class FixtureHuman:
                async def assist(self, request):
                    requests.append(request)
                    await page.locator("#human").click()
                    await page.locator("#evidence").wait_for()
                    return AssistanceDecision(
                        request_digest=request.content_digest(), action="resume"
                    )

            session = ChromiumHumanSession(selected, assistant=FixtureHuman())
            capture = await session.capture(origin + "/research/article")
            assert b"Unrelated private" not in capture.dom
            assert "同一瀏覽器" in capture.dom.decode()
            assert len(requests) == 1
            assert capture.evidence.target_id == selected.target_id
            assert capture.evidence.acquisition == "browser_dom"
            assert capture.evidence.browser_subresource_bytes is None
            assert capture.evidence.dom_sha256 == hashlib.sha256(capture.dom).hexdigest()
            assert capture.evidence.collector_dom_bytes_read > len(capture.dom)
            assert capture.evidence.assistance[0].action == "resume"
            assert "human_fixture" not in capture.model_dump_json()
            assert selected.control_endpoint not in capture.model_dump_json()
            assert BrowserCapture.model_validate_json(capture.model_dump_json()) == capture
            capture.validate_policy(selected)
            with pytest.raises(ValueError):
                capture.model_copy(update={"dom": b"fabricated"}).validate_policy(selected)
            mutated = capture.evidence.model_copy(
                update={"collector_dom_bytes_read": len(capture.dom)}
            )
            with pytest.raises(ValueError):
                capture.model_copy(update={"evidence": mutated}).validate_policy(selected)
            with pytest.raises(ValueError):
                capture.validate_policy(
                    selected.model_copy(update={"target_id": "different-target"})
                )
            # Same browser's cookie persists. A second capture needs no assistance.
            again = await session.capture(origin + "/research/another")
            assert "同一瀏覽器" in again.dom.decode()
            assert not again.evidence.assistance
            assert len(requests) == 1

        asyncio.run(with_browser(tmp_path, origin, operation))


@pytest.mark.parametrize(
    "outcome", ["decline", "timeout", "cancel", "stale", "unchanged", "failed"]
)
def test_real_assistance_refusal_or_cancellation_only_detaches(tmp_path, outcome):
    with interactive_source() as origin:

        async def operation(selected, page, unrelated):
            calls = []

            class FixtureHuman:
                async def assist(self, request):
                    calls.append(request)
                    if outcome == "timeout":
                        await asyncio.sleep(10)
                    if outcome == "cancel":
                        raise asyncio.CancelledError()
                    if outcome == "failed":
                        raise RuntimeError("secret fixture assistant diagnostic must not leak")
                    return AssistanceDecision(
                        request_digest="0" * 64 if outcome == "stale" else request.content_digest(),
                        action="decline" if outcome == "decline" else "resume",
                    )

            selected = selected.model_copy(update={"assistance_timeout_seconds": 0.05})
            session = ChromiumHumanSession(selected, assistant=FixtureHuman())
            expected = asyncio.CancelledError if outcome == "cancel" else GhimeraRefused
            with pytest.raises(expected) as caught:
                await session.capture(origin + "/research/article")
            assert caught.value.bytes_read > 0
            assert "secret fixture" not in str(caught.value)
            if outcome == "unchanged":
                assert len(calls) == selected.max_assistance_attempts
            else:
                assert len(calls) == 1

        asyncio.run(with_browser(tmp_path, origin, operation))


def test_capture_refuses_scope_and_oversize_without_using_unrelated_tab(tmp_path):
    with interactive_source() as origin:

        async def operation(selected, page, unrelated):
            class NoAssistance:
                async def assist(self, request):
                    raise AssertionError("no assistance for an oversized DOM")

            session = ChromiumHumanSession(selected, assistant=NoAssistance())
            with pytest.raises(GhimeraRefused, match="out_of_scope"):
                await session.capture(origin + "/outside")
            with pytest.raises(GhimeraRefused, match="budget_exhausted") as caught:
                await session.capture(origin + "/research/large")
            assert caught.value.bytes_read == selected.max_dom_bytes

        asyncio.run(with_browser(tmp_path, origin, operation))


def test_selected_target_never_falls_back_to_first_tab(tmp_path):
    with interactive_source() as origin:

        async def operation(selected, page, unrelated):
            class NoAssistance:
                async def assist(self, request):
                    raise AssertionError("unknown target cannot request assistance")

            session = ChromiumHumanSession(
                selected.model_copy(update={"target_id": "missing-target"}),
                assistant=NoAssistance(),
            )
            with pytest.raises(GhimeraRefused, match="adapter_contract") as caught:
                await session.capture(origin + "/research/article")
            assert caught.value.bytes_read == 0
            assert page.url == "about:blank"

        asyncio.run(with_browser(tmp_path, origin, operation))


def test_human_completion_does_not_expand_capture_scope(tmp_path):
    with interactive_source() as origin:

        async def operation(selected, page, unrelated):
            class FixtureHuman:
                async def assist(self, request):
                    await page.goto(origin + "/outside")
                    return AssistanceDecision(
                        request_digest=request.content_digest(), action="resume"
                    )

            session = ChromiumHumanSession(selected, assistant=FixtureHuman())
            with pytest.raises(GhimeraRefused, match="out_of_scope") as caught:
                await session.capture(origin + "/research/article")
            assert len(caught.value.assistance) == 1
            assert caught.value.assistance[0].action == "resume"

        asyncio.run(with_browser(tmp_path, origin, operation))


def test_page_script_cannot_replace_capture_encoder_with_fabricated_bytes(tmp_path):
    with interactive_source() as origin:

        async def operation(selected, page, unrelated):
            session = ChromiumHumanSession(
                selected.model_copy(
                    update={"assistance_reasons": (), "max_assistance_attempts": 0}
                ),
                assistant=None,
            )
            result = await session.capture(origin + "/research/tamper")
            assert b"Native evidence from the actual DOM" in result.dom
            assert result.dom != b"fake"
            assert result.evidence.collector_dom_bytes_read == len(result.dom)

        asyncio.run(with_browser(tmp_path, origin, operation))
