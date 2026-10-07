"""Native request-stage admission; no external site, credentials or solver."""

import asyncio
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from pydantic import ValidationError

from ghimera.browser_navigation import BrowserNavigationGuard
from ghimera.browser_navigation_types import BrowserNavigationConfig, BrowserNavigationEvidence
from ghimera.human_browser_types import HumanBrowserConfig
from ghimera.refusals import GhimeraRefused, RefusalCode
from tests.test_document_extraction import native_pdf
from tests.test_human_browser import with_browser


def navigation_policy(**updates):
    values = dict(
        schema="ghimera.browser-navigation/1",
        adapter_revision="ghimera-chromium-navigation-guard/1",
        interception_owner="exclusive_main_frame_during_operation",
        max_redirects=3,
        admission_timeout_seconds=3.0,
        cleanup_timeout_seconds=3.0,
    )
    values.update(updates)
    return BrowserNavigationConfig.model_validate(values)


def page_policy(selected):
    data = selected.model_dump()
    data.pop("control_endpoint")
    data["adapter"] = "patchright_page"
    return HumanBrowserConfig.model_validate(data)


class Admission:
    def __init__(self, *, denied=None, wait=None):
        self.calls = []
        self.denied = denied
        self.wait = wait
        self.arrived = asyncio.Event()

    async def admit(self, url):
        self.calls.append(url)
        if url.endswith("/final"):
            self.arrived.set()
            if self.wait is not None:
                await self.wait.wait()
            if self.denied is not None:
                raise GhimeraRefused(self.denied)


class Scope:
    def __init__(self, *, denied=None):
        self.denied = denied

    def permits(self, url):
        return self.denied is None or not url.endswith(self.denied)


@contextmanager
def redirect_source(
    *, kind="html", final_path="/research/final", middle_target=None, cross_origin=False
):
    calls, cookie_calls = Counter(), Counter()
    raw = native_pdf()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls[self.path] += 1
            cookie_calls[self.path] += int("synthetic_caller=yes" in self.headers.get("Cookie", ""))
            location = {
                "/research/start": "/research/middle",
                "/research/middle": middle_target or final_path,
            }.get(self.path)
            if self.path == "/research/middle" and cross_origin:
                location = f"http://127.0.0.1:{server.server_port}" + final_path
            if location:
                self.send_response(302 if self.path.endswith("/start") else 307)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = raw if kind != "html" and self.path == final_path else b"<p>native result</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if body != raw else "application/pdf")
            if body == raw:
                self.send_header("Content-Disposition", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *unused):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://fixture.example:{server.server_port}", calls, cookie_calls, raw
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_navigation_config_requires_explicit_delegation_and_finite_limits():
    values = navigation_policy().model_dump()
    for name, value in (
        ("interception_owner", "shared"),
        ("max_redirects", True),
        ("max_redirects", -1),
        ("cleanup_timeout_seconds", float("inf")),
        ("cleanup_timeout_seconds", 0),
        ("admission_timeout_seconds", float("inf")),
        ("admission_timeout_seconds", 0),
        ("schema_version", "ghimera.browser-navigation/2"),
    ):
        with pytest.raises(ValidationError):
            BrowserNavigationConfig.model_validate(dict(values, **{name: value}))
    assert BrowserNavigationConfig.model_validate_json(navigation_policy().model_dump_json()) == (
        navigation_policy()
    )


@pytest.mark.parametrize("kind", ["html", "attachment", "inline"])
def test_native_redirect_chain_is_admitted_before_each_contact_and_session_survives(tmp_path, kind):
    from patchright.async_api import Error

    with redirect_source(kind=kind) as (origin, calls, cookie_calls, raw):

        async def operation(selected, page, unrelated):
            selected = page_policy(selected)
            await page.context.add_cookies([dict(name="synthetic_caller", value="yes", url=origin)])
            admission = Admission()
            downloads = []
            page.on("download", lambda item: downloads.append(item))
            guard = BrowserNavigationGuard(
                navigation_policy(),
                browser_policy=selected,
                page=page,
                request_url=origin + "/research/start",
                scope=Scope(),
                admission=admission,
            )
            async with guard:
                try:
                    response = await page.goto(origin + "/research/start", wait_until="commit")
                except Error as exc:
                    guard.check()
                    assert kind == "attachment"
                    assert "ERR_ABORTED" in str(exc) or "Download is starting" in str(exc)
                    async with asyncio.timeout(5):
                        while not downloads:
                            await asyncio.sleep(0.01)
                    final_url = downloads[0].url
                else:
                    assert kind != "attachment" and response.status == 200
                    final_url = response.url
                evidence = guard.evidence(final_url)
                assert len(evidence.hops) == 3
                evidence.validate_policy(navigation_policy(), selected)
                assert BrowserNavigationEvidence.model_validate_json(
                    evidence.model_dump_json()
                ) == (evidence)
                assert admission.calls == [
                    origin + path
                    for path in ("/research/start", "/research/middle", "/research/final")
                ]
                assert "synthetic_caller" not in evidence.model_dump_json()
                changed = evidence.model_dump()
                changed["hops"] = changed["hops"][:1] + changed["hops"][2:]
                with pytest.raises(ValidationError):
                    BrowserNavigationEvidence.model_validate(changed)
                with pytest.raises(ValueError):
                    evidence.validate_policy(navigation_policy(max_redirects=0), selected)
                with pytest.raises(GhimeraRefused, match="adapter_contract"):
                    guard.evidence(origin + "/research/fabricated")
            if downloads:
                target = tmp_path / "observed-original.pdf"
                await downloads[0].save_as(str(target))
                assert target.read_bytes() == raw
                await downloads[0].delete()
            assert all(
                calls[path] == cookie_calls[path] == 1
                for path in ("/research/start", "/research/middle", "/research/final")
            )
            await page.goto(origin + "/research/after", wait_until="commit")
            assert calls["/research/after"] == cookie_calls["/research/after"] == 1
            with pytest.raises(GhimeraRefused, match="adapter_contract"):
                await guard.__aenter__()

        asyncio.run(with_browser(tmp_path, origin, operation, accept_downloads=True))


@pytest.mark.parametrize("denial", ["robots", "scope", "browser", "budget", "limit", "loop"])
def test_native_denied_redirect_is_never_contacted_and_cleanup_releases_page(tmp_path, denial):
    from patchright.async_api import Error

    final_path = "/blocked" if denial == "browser" else "/research/final"
    with redirect_source(
        final_path=final_path,
        middle_target="/research/start" if denial == "loop" else None,
    ) as (origin, calls, _, _):

        async def operation(selected, page, unrelated):
            selected = page_policy(selected)
            admission = Admission(
                denied={
                    "robots": RefusalCode.ROBOTS_DISALLOWED,
                    "budget": RefusalCode.BUDGET_EXHAUSTED,
                }.get(denial)
            )
            cfg = navigation_policy(max_redirects=1 if denial == "limit" else 3)
            guard = BrowserNavigationGuard(
                cfg,
                browser_policy=selected,
                page=page,
                request_url=origin + "/research/start",
                scope=Scope(denied="/final" if denial == "scope" else None),
                admission=admission,
            )
            expected = {
                "robots": "robots_disallowed",
                "budget": "budget_exhausted",
                "scope": "out_of_scope",
                "browser": "out_of_scope",
                "limit": "fetch_failed",
                "loop": "fetch_failed",
            }[denial]
            with pytest.raises(GhimeraRefused, match=expected):
                async with guard:
                    with pytest.raises(Error):
                        await page.goto(origin + "/research/start", wait_until="commit")
                    guard.check()
            assert calls[final_path] == 0 and calls["/research/start"] == 1
            await page.goto(origin + "/research/after", wait_until="commit")
            assert calls["/research/after"] == 1

        asyncio.run(with_browser(tmp_path, origin, operation, accept_downloads=True))


def test_native_cancellation_while_admitting_prevents_late_contact(tmp_path):
    with redirect_source() as (origin, calls, _, _):

        async def operation(selected, page, unrelated):
            admission = Admission(wait=asyncio.Event())
            guard = BrowserNavigationGuard(
                navigation_policy(),
                browser_policy=page_policy(selected),
                page=page,
                request_url=origin + "/research/start",
                scope=Scope(),
                admission=admission,
            )

            async def capture():
                async with guard:
                    await page.goto(origin + "/research/start", wait_until="commit")

            task = asyncio.create_task(capture())
            async with asyncio.timeout(5):
                await admission.arrived.wait()
            assert calls["/research/final"] == 0
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            admission.wait.set()
            assert not guard._pending and not guard._tasks
            await page.goto(origin + "/research/after", wait_until="commit")
            assert calls["/research/final"] == 0 and calls["/research/after"] == 1

        asyncio.run(with_browser(tmp_path, origin, operation, accept_downloads=True))


def test_guard_never_claims_unverified_tor_or_accepts_a_forged_target(tmp_path):
    with redirect_source() as (origin, calls, _, _):

        async def operation(selected, page, unrelated):
            selected = page_policy(selected)
            with pytest.raises(GhimeraRefused, match="tor_unavailable"):
                BrowserNavigationGuard(
                    navigation_policy(),
                    browser_policy=selected.model_copy(update={"declared_route": "tor"}),
                    page=page,
                    request_url=origin + "/research/start",
                    scope=Scope(),
                    admission=Admission(),
                )
            guard = BrowserNavigationGuard(
                navigation_policy(),
                browser_policy=selected.model_copy(update={"target_id": "wrong-target"}),
                page=page,
                request_url=origin + "/research/start",
                scope=Scope(),
                admission=Admission(),
            )
            with pytest.raises(GhimeraRefused, match="adapter_contract"):
                async with guard:
                    await page.goto(origin + "/research/start")
            assert calls["/research/start"] == 0
            await page.goto(origin + "/research/after", wait_until="commit")
            assert calls["/research/after"] == 1

        asyncio.run(with_browser(tmp_path, origin, operation))


def test_competing_main_frame_navigation_is_blocked_before_first_contact(tmp_path):
    from patchright.async_api import Error

    with redirect_source() as (origin, calls, _, _):

        async def operation(selected, page, unrelated):
            guard = BrowserNavigationGuard(
                navigation_policy(),
                browser_policy=page_policy(selected),
                page=page,
                request_url=origin + "/research/start",
                scope=Scope(),
                admission=Admission(),
            )
            with pytest.raises(GhimeraRefused, match="adapter_contract"):
                async with guard:
                    with pytest.raises(Error):
                        await page.goto(origin + "/research/after", wait_until="commit")
                    # Even a caller forgetting check() cannot make normal context
                    # exit pretend that failed admission was successful.
            assert calls["/research/after"] == 0
            await page.goto(origin + "/research/after", wait_until="commit")
            assert calls["/research/after"] == 1

        asyncio.run(with_browser(tmp_path, origin, operation))


def test_admission_timeout_prevents_contact_and_has_no_late_continuation(tmp_path):
    from patchright.async_api import Error

    with redirect_source() as (origin, calls, _, _):

        async def operation(selected, page, unrelated):
            admission = Admission(wait=asyncio.Event())
            guard = BrowserNavigationGuard(
                navigation_policy(admission_timeout_seconds=0.05),
                browser_policy=page_policy(selected),
                page=page,
                request_url=origin + "/research/start",
                scope=Scope(),
                admission=admission,
            )
            with pytest.raises(GhimeraRefused, match="fetch_failed"):
                async with guard:
                    with pytest.raises(Error):
                        await page.goto(origin + "/research/start", wait_until="commit")
                    guard.check()
            admission.wait.set()
            assert not guard._pending and not guard._tasks
            await page.goto(origin + "/research/after", wait_until="commit")
            assert calls["/research/final"] == 0 and calls["/research/after"] == 1

        asyncio.run(with_browser(tmp_path, origin, operation))


@pytest.mark.parametrize("admitted", [False, True])
def test_cross_origin_redirect_requires_explicit_browser_origin_and_native_chain(
    tmp_path, admitted
):
    from patchright.async_api import Error

    with redirect_source(cross_origin=True) as (origin, calls, _, _):

        async def operation(selected, page, unrelated):
            data = page_policy(selected).model_dump()
            final_origin = origin.replace("fixture.example", "127.0.0.1")
            if admitted:
                data["origins"] = [
                    *data["origins"],
                    dict(
                        origin=final_origin,
                        path_prefixes=["/research"],
                        allow_http=True,
                    ),
                ]
            selected = HumanBrowserConfig.model_validate(data)
            admission = Admission()
            guard = BrowserNavigationGuard(
                navigation_policy(),
                browser_policy=selected,
                page=page,
                request_url=origin + "/research/start",
                scope=Scope(),
                admission=admission,
            )
            if admitted:
                async with guard:
                    response = await page.goto(origin + "/research/start", wait_until="commit")
                    assert response.status == 200
                    evidence = guard.evidence(response.url)
                    assert evidence.final_url == final_origin + "/research/final"
                    assert len(evidence.hops) == 3
                    evidence.validate_policy(navigation_policy(), selected)
                assert calls["/research/final"] == 1
                assert admission.calls[-1] == final_origin + "/research/final"
            else:
                with pytest.raises(GhimeraRefused, match="out_of_scope"):
                    async with guard:
                        with pytest.raises(Error):
                            await page.goto(origin + "/research/start", wait_until="commit")
                        guard.check()
                assert calls["/research/final"] == 0
                assert final_origin + "/research/final" not in admission.calls

        asyncio.run(with_browser(tmp_path, origin, operation))
