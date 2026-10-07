"""Actual same-session inline file bytes, not PDF-viewer DOM or cookies export."""

import asyncio
import hashlib
import tomllib
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
from pydantic import ValidationError

from ghimera.human_browser import BoundPageHumanSession
from ghimera.human_browser_types import BrowserResponseCapture, HumanBrowserConfig
from ghimera.refusals import GhimeraRefused
from tests.test_browser_downloads import download_policy, scope_for_source
from tests.test_document_extraction import native_pdf
from tests.test_human_browser import policy, with_browser


def inline_policy(selected, origin, *, limit=100_000):
    values = download_policy(selected, origin, limit=limit).model_dump()
    values["downloads"].pop("actions")
    values["downloads"]["navigation_content_types"] = ["application/pdf"]
    values["downloads"]["inline"] = dict(
        schema="ghimera.browser-inline/1",
        adapter_revision="ghimera-browser-fetch-stream/1",
        content_types=["application/pdf"],
    )
    return HumanBrowserConfig.model_validate(values)


@contextmanager
def inline_site(
    raw,
    *,
    require_session=False,
    changed_body=None,
    stall=None,
    redirect_second=False,
):
    counts = Counter()
    started = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            counts[self.path] += 1
            status, mime, body = 200, "application/pdf", raw
            if self.path == "/robots.txt":
                mime, body = "text/plain", b"User-agent: *\nDisallow:\n"
            elif self.path == "/research/session":
                mime = "text/html"
                body = (
                    b"<p>Controlled caller-owned session</p><button id='entitled'>Continue</button>"
                    b"<script>document.querySelector('#entitled').onclick=()=>{"
                    b"document.cookie='fixture-session=permitted; path=/research; SameSite=Strict';"
                    b"};</script>"
                )
            elif require_session and "fixture-session=permitted" not in self.headers.get(
                "Cookie", ""
            ):
                status, mime, body = 401, "text/plain", b"Caller session required"
            elif counts[self.path] > 1 and changed_body is not None:
                body = changed_body
            elif self.path == "/research/report" and counts[self.path] > 1 and redirect_second:
                status, mime, body = 302, "text/plain", b"Redirect must not be followed"
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Disposition", 'inline; filename="../../not-a-path.pdf"')
            self.send_header("Content-Length", str(len(body)))
            if status == 302:
                self.send_header("Location", "/outside")
            self.end_headers()
            try:
                if self.path == "/research/report" and counts[self.path] > 1 and stall is not None:
                    self.wfile.write(body[:5])
                    self.wfile.flush()
                    started.set()
                    stall.wait(timeout=5)
                    self.wfile.write(body[5:])
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (server.server_port, counts, started)
    finally:
        if stall is not None:
            stall.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_inline_policy_is_explicit_and_preserves_published_download_identity():
    old = download_policy(policy(), "https://publisher.example")
    assert "inline" not in old.model_dump()["downloads"]
    selected = inline_policy(policy(), "https://publisher.example")
    assert selected.downloads.inline.content_types == ("application/pdf",)
    example = tomllib.loads(Path("examples/browser-inline-documents.toml").read_text())
    values = selected.model_dump()
    values["downloads"] = example["human_browser"]["downloads"]
    assert HumanBrowserConfig.model_validate(values).downloads.inline == selected.downloads.inline
    for formats in ((), ("application/pdf", "application/pdf"), ("text/html",)):
        values = selected.model_dump()
        values["downloads"]["inline"]["content_types"] = formats
        with pytest.raises(ValidationError):
            HumanBrowserConfig.model_validate(values)
    values = selected.model_dump()
    values["downloads"]["navigation_content_types"] = []
    with pytest.raises(ValidationError):
        HumanBrowserConfig.model_validate(values)


def test_inline_pdf_uses_existing_caller_session_and_retains_actual_original(tmp_path):
    raw = native_pdf()
    with inline_site(raw, require_session=True) as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(selected, page, unrelated):
            selected = inline_policy(selected, origin)
            await page.goto(origin + "/research/session")
            await page.locator("#entitled").click()
            capture = await BoundPageHumanSession(selected, page=page, assistant=None).capture(
                origin + "/research/report", scope=scope_for_source(site[0])
            )
            assert capture.body == raw
            assert capture.evidence.acquisition == "browser_response"
            assert capture.evidence.response_status == 200
            assert capture.evidence.response_content_type == "application/pdf"
            assert capture.evidence.file_sha256 == hashlib.sha256(raw).hexdigest()
            assert capture.evidence.collector_bytes_read == len(raw)
            assert capture.evidence.document_url == capture.evidence.final_url
            assert capture.evidence.browser_fetch_bytes is None
            capture.validate_policy(selected)
            for key, value in (
                ("response_status", 201),
                ("response_content_type", "text/html"),
                ("document_url", origin + "/research/different"),
                ("collector_file_bytes_read", len(raw) + 1),
            ):
                changed = capture.model_dump()
                changed["evidence"][key] = value
                with pytest.raises(ValidationError):
                    BrowserResponseCapture.model_validate(changed)
            changed = capture.model_dump()
            changed["body"] = b"%PDF- not the captured original"
            with pytest.raises(ValidationError):
                BrowserResponseCapture.model_validate(changed)
            changed_policy = selected.model_dump()
            changed_policy["downloads"].pop("inline")
            with pytest.raises(ValueError):
                capture.validate_policy(HumanBrowserConfig.model_validate(changed_policy))
            assert "fixture-session" not in capture.model_dump_json()
            assert site[1]["/research/report"] == 2
            assert not page.is_closed() and not unrelated.is_closed()
            await page.goto(origin + "/research/session")
            assert "Controlled caller-owned session" in await page.content()

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


@pytest.mark.parametrize(
    "invalid",
    [b"not the original PDF", native_pdf() + b"x" * 100_000],
    ids=["invalid-media", "over-limit"],
)
def test_changed_or_oversized_inline_body_refuses_without_partial_document(tmp_path, invalid):
    raw = native_pdf()
    with inline_site(raw, changed_body=invalid) as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(selected, page, unrelated):
            selected = inline_policy(selected, origin, limit=len(raw))
            with pytest.raises(GhimeraRefused) as caught:
                await BoundPageHumanSession(selected, page=page, assistant=None).capture(
                    origin + "/research/report"
                )
            expected = "budget_exhausted" if len(invalid) > len(raw) else "content_type_unwanted"
            assert caught.value.code.value == expected
            assert caught.value.bytes_read == min(len(invalid), len(raw) + 1)
            assert not page.is_closed() and not unrelated.is_closed()

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


def test_inline_cancellation_aborts_only_its_own_fetch(tmp_path):
    raw, stalled = native_pdf(), Event()
    with inline_site(raw, stall=stalled) as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(selected, page, unrelated):
            selected = inline_policy(selected, origin)
            capture = asyncio.create_task(
                BoundPageHumanSession(selected, page=page, assistant=None).capture(
                    origin + "/research/report"
                )
            )
            try:
                assert await asyncio.to_thread(site[2].wait, 5)
                capture.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await capture
                assert not page.is_closed() and not unrelated.is_closed()
                assert (
                    await page.evaluate(
                        """() => Object.getOwnPropertyNames(globalThis)
                        .filter(k=>k.startsWith('ghimera_inline_'))""",
                        isolated_context=True,
                    )
                    == []
                )
            finally:
                stalled.set()

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


def test_inline_stream_never_follows_a_redirect_in_the_caller_session(tmp_path):
    with inline_site(native_pdf(), redirect_second=True) as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(selected, page, unrelated):
            selected = inline_policy(selected, origin)
            with pytest.raises(GhimeraRefused, match="fetch_failed"):
                await BoundPageHumanSession(selected, page=page, assistant=None).capture(
                    origin + "/research/report"
                )
            assert site[1]["/research/report"] == 2 and site[1]["/outside"] == 0
            assert not page.is_closed() and not unrelated.is_closed()

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))
