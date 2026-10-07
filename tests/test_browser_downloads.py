"""Actual same-session browser downloads -> native document parsing/readback."""

import asyncio
import hashlib
import time
import tomllib
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.collector import Collector
from ghimera.config import GhimeraConfig
from ghimera.document_media import DOCX_TYPE
from ghimera.documents import DocumentExtractor
from ghimera.doubles import FakeJudge, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.http import CurlRoute
from ghimera.human_browser import BoundPageHumanSession, HumanBrowserSession
from ghimera.human_browser_route import HumanBrowserRoute
from ghimera.human_browser_types import (
    AssistanceDecision,
    BrowserDownloadCapture,
    HumanBrowserConfig,
)
from ghimera.journal import read_journal
from ghimera.ledger import Ledger
from ghimera.loop import GoalLoop
from ghimera.models import Harvest, Page
from ghimera.refusals import GhimeraRefused
from ghimera.research import ResearchLoop
from ghimera.research_types import ResearchRequest, ResearchResult
from ghimera.result_archive import ResearchResultArchive
from tests.test_collector import assembled, encoder_endpoint, model_endpoint, search_endpoint
from tests.test_document_extraction import config as document_config
from tests.test_document_extraction import docx, native_pdf
from tests.test_http_fetch import ResolverFixture, state
from tests.test_human_browser import policy, with_browser
from tests.test_intent_research import (
    AnalystFixture,
    PlannerFixture,
    ReviewerFixture,
    SearchFixture,
)
from tests.test_intent_research import policy as research_policy

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint"]


@contextmanager
def download_site(
    raw,
    *,
    robots=b"User-agent: *\nDisallow:\n",
    media="application/octet-stream",
    assisted=False,
    slow_body=None,
    landing_html=None,
):
    counts = Counter()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            counts[self.path] += 1
            if self.path == "/robots.txt":
                body, mime = robots, "text/plain"
            elif self.path == "/research/landing":
                body = (
                    b"<article>Download the original report.</article>"
                    b"<a id='get-report' href='/research/report' download>Download</a>"
                )
                if landing_html is not None:
                    body = landing_html
                if assisted:
                    body = (
                        b"<title>Just a moment</title><p>Verify you are human</p>"
                        b"<button id='human'>Complete controlled human interaction</button>"
                        b"<script>document.querySelector('#human').onclick=()=>{"
                        b"document.documentElement.innerHTML=\"<a id='get-report' "
                        b"href='/research/report' download>Download</a>\";};</script>"
                    )
                mime = "text/html"
            else:
                body, mime = raw, media
            self.send_response(200)
            self.send_header("Content-Type", mime)
            if self.path == "/research/report":
                # A hostile suggested filename cannot select any collector path.
                self.send_header("Content-Disposition", 'attachment; filename="../../private.pdf"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                if self.path == "/research/report" and slow_body is not None:
                    # Chromium's MIME sniffing needs more than a five-byte PDF
                    # prefix before it announces a download. Hold the remaining
                    # bytes only after enough actual payload has been sent.
                    self.wfile.write(body[:2048])
                    self.wfile.flush()
                    slow_body.wait(timeout=5)
                    self.wfile.write(body[2048:])
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
        yield (server.server_port, counts)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def download_policy(selected, origin, *, mime="application/pdf", click=False, limit=100_000):
    values = selected.model_dump()
    values.update(adapter="patchright_page", control_endpoint=None)
    values.update(
        assistance_reasons=(),
        max_assistance_attempts=0,
        downloads=dict(
            schema="ghimera.browser-downloads/1",
            adapter_revision="ghimera-patchright-download-stream/1",
            max_file_bytes=limit,
            read_chunk_bytes=512,
            actions=[
                dict(
                    source_url=origin + "/research/report",
                    navigation_url=origin + ("/research/landing" if click else "/research/report"),
                    selector="#get-report" if click else None,
                    content_type=mime,
                )
            ],
        ),
    )
    return HumanBrowserConfig.model_validate(values)


def test_download_recipe_is_opt_in_and_rejects_scope_expansion():
    original = policy()
    assert "downloads" not in original.model_dump()
    origin = "https://publisher.example"
    selected = download_policy(original, origin, click=True)
    assert HumanBrowserConfig.model_validate_json(selected.model_dump_json()) == selected
    for change in (
        dict(navigation_url="https://other.example/report"),
        dict(source_url="https://publisher.example/outside"),
        dict(selector=None),
    ):
        data = selected.model_dump()
        data["downloads"]["actions"][0].update(change)
        with pytest.raises(ValidationError):
            HumanBrowserConfig.model_validate(data)
    fragment = tomllib.loads(Path("examples/human-browser-downloads.toml").read_text())
    checked = HumanBrowserConfig.model_validate(fragment["human_browser"])
    assert checked.adapter == "patchright_page" and checked.control_endpoint is None
    assert checked.downloads.actions[0].selector == "a#download-original"


def test_browser_adapter_cannot_override_shared_scope_budget_and_cancel_order():
    with pytest.raises(TypeError, match="final"):
        type("UnboundedCapture", (HumanBrowserSession,), {"capture": lambda *args: None})
    with pytest.raises(TypeError, match="final"):
        type("UnvalidatedCapture", (HumanBrowserSession,), {"validate_config": lambda *args: None})


@pytest.mark.parametrize("mime,click", [("application/pdf", False), (DOCX_TYPE, True)])
def test_real_download_is_parsed_cited_graphed_journalled_and_archived(tmp_path, mime, click):
    raw = native_pdf() if mime == "application/pdf" else docx()
    with download_site(raw) as site:
        _, _, previous, _, origin = state(site)

        async def scenario(selected, page, unrelated):
            selected = download_policy(selected, origin, mime=mime, click=click)
            graph = tomllib.loads(Path("examples/research-graph.toml").read_text())
            graph["sink_path"] = str(tmp_path / "graph")
            values = previous.config.model_dump()
            values.update(
                human_browser=selected.model_dump(),
                document_extraction=document_config(tmp_path).document_extraction.model_dump(),
                request_timeout_seconds=30.0,
                wall_seconds=120.0,
                graph=graph,
                research=research_policy(
                    allowed_ports=[site[0]],
                    content_types=["text/html", "application/pdf", DOCX_TYPE],
                ),
                journal=dict(
                    schema="chimera.run-journal-config/1",
                    directory=str(tmp_path / "journal"),
                    max_record_bytes=1_000_000,
                    max_journal_bytes=10_000_000,
                    max_summary_bytes=1_000_000,
                    max_records=1000,
                ),
            )
            cfg = GhimeraConfig.model_validate(values)
            ladder = FetchLadder(
                (
                    HumanBrowserRoute(
                        selected, BoundPageHumanSession(selected, page=page, assistant=None)
                    ),
                    CurlRoute(cfg, resolver=ResolverFixture()),
                )
            )
            collection = GoalLoop(
                config=cfg,
                fetcher=ladder,
                extractor=DocumentExtractor(cfg),
                scorer=KeywordScorer(),
                judge=FakeJudge(satisfied=True),
            )

            class SeedPlanner(PlannerFixture):
                async def plan(self, request):
                    return (await super().plan(request)).model_copy(update={"queries": ()})

            loop = ResearchLoop(
                config=cfg,
                collector=collection,
                search=SearchFixture(),
                planner=SeedPlanner(),
                analyst=AnalystFixture(),
                reviewer=ReviewerFixture(),
            )
            result = await loop.run(
                ResearchRequest(intent="find ports", seeds=(origin + "/research/report",)),
                run_id="browser-file-research",
            )
            assert result.status == "answered", [
                (r.event, r.refusal, r.bytes_read) for r in result.harvest.ledger
            ]
            document = result.harvest.documents[0]
            assert document.raw == raw
            assert "terminal construction" in document.extracted.text
            evidence = document.human_browser
            assert evidence.acquisition == "browser_download"
            assert evidence.file_sha256 == hashlib.sha256(raw).hexdigest()
            assert evidence.file_bytes == evidence.collector_file_bytes_read == len(raw)
            assert (
                evidence.collector_dom_bytes_read > 0
                if click
                else evidence.collector_dom_bytes_read == 0
            )
            assert evidence.browser_download_bytes is None
            assert "private.pdf" not in evidence.model_dump_json()
            assert document.transport is None and document.local_input is None
            assert document.extracted.document_parse.source_sha256 == evidence.file_sha256
            assert result.answer.claims[0].citations[0].matches(document)
            row = next(row for row in result.harvest.ledger if row.human_browser is not None)
            assert row.status is None and row.bytes_read == evidence.collector_bytes_read
            assert result.harvest.receipt.bytes_read == sum(
                row.bytes_read for row in result.harvest.ledger
            )
            node = next(node for node in result.harvest.graph.nodes if node.role == "document")
            assert node.human_browser == evidence and ":browser_download:" in node.identity
            assert read_journal(cfg.journal, "browser-file-research").rows == result.harvest.ledger
            assert ResearchResult.model_validate_json(result.model_dump_json()) == result
            archive = ResearchResultArchive.create(
                tmp_path / "archive", run_id="browser-file-research"
            )
            try:
                archive.write(result, max_bytes=4_000_000)
            finally:
                archive.close()
            assert ResearchResultArchive.read(tmp_path / "archive", max_bytes=4_000_000) == result
            assert site[1]["/research/report"] == 1
            assert site[1]["/research/landing"] == int(click)

            for mutate in ("strip", "bytes", "status", "format"):
                wire = result.harvest.model_dump()
                if mutate == "strip":
                    wire["documents"][0]["human_browser"] = None
                elif mutate == "bytes":
                    wire["documents"][0]["human_browser"]["file_bytes"] += 1
                elif mutate == "status":
                    wire["ledger"][row.sequence]["status"] = 200
                else:
                    wire["documents"][0]["human_browser"]["media_observation"] = (
                        "docx_archive_members"
                        if mime == "application/pdf"
                        else "pdf_header_at_start"
                    )
                with pytest.raises(ValidationError):
                    Harvest.model_validate(wire)

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


@pytest.mark.parametrize("failure", ["file_limit", "invalid_media", "landing_robots"])
def test_failed_download_does_not_refetch_and_preserves_budget_and_caller(tmp_path, failure):
    raw = b"%PDF-" + b"x" * 4096 if failure != "invalid_media" else b"not a document"
    robots = (
        b"User-agent: *\nDisallow: /research/landing\n"
        if failure == "landing_robots"
        else b"User-agent: *\nDisallow:\n"
    )
    with download_site(raw, robots=robots) as site:
        _, scope, previous, _, origin = state(site)

        async def scenario(selected, page, unrelated):
            selected = download_policy(
                selected,
                origin,
                click=failure == "landing_robots",
                limit=1024 if failure == "file_limit" else 100_000,
            )
            values = previous.config.model_dump()
            values.update(human_browser=selected.model_dump(), request_timeout_seconds=30.0)
            cfg = GhimeraConfig.model_validate(values)
            ladder = FetchLadder(
                (
                    HumanBrowserRoute(
                        selected, BoundPageHumanSession(selected, page=page, assistant=None)
                    ),
                    CurlRoute(cfg, resolver=ResolverFixture()),
                )
            )
            ledger, budget = Ledger(), RunBudget(cfg, time.monotonic)
            scope = scope_for_source(site[0])
            with pytest.raises(GhimeraRefused) as caught:
                await ladder.fetch(origin + "/research/report", scope, budget, ledger)
            expected = {
                "file_limit": "budget_exhausted",
                "invalid_media": "content_type_unwanted",
                "landing_robots": "robots_disallowed",
            }[failure]
            assert caught.value.code.value == expected
            assert budget.bytes_read == sum(row.bytes_read for row in ledger.snapshot())
            assert budget.quiescent
            if failure == "landing_robots":
                assert not site[1]["/research/report"] and not site[1]["/research/landing"]
                assert page.url == "about:blank"
            else:
                assert site[1]["/research/report"] == 1
                row = ledger.snapshot()[-1]
                assert row.human_browser is None and row.status is None
                assert (
                    row.bytes_read == 1025
                    if failure == "file_limit"
                    else row.bytes_read == len(raw)
                )

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


def scope_for_source(port):
    from ghimera.models import Scope

    return Scope(
        allowed_hosts=("fixture.example",),
        allowed_ports=(port,),
        max_depth=0,
        content_types=("text/html", "application/pdf", DOCX_TYPE),
    )


def test_download_capture_cannot_replay_html_as_a_pdf():
    from ghimera.human_browser_types import BrowserDownloadEvidence

    selected = download_policy(policy(), "https://publisher.example")
    raw = b"<html>viewer only</html>"
    evidence = BrowserDownloadEvidence(
        schema="ghimera.browser-download-evidence/1",
        acquisition="browser_download",
        capture_id="capture",
        request_url="https://publisher.example/research/report",
        final_url="https://publisher.example/research/report",
        navigation_url="https://publisher.example/research/report",
        initiator_url=None,
        session_id=selected.session_id,
        target_id=selected.target_id,
        policy_digest=selected.content_digest(),
        adapter_revision=selected.adapter_revision,
        download_adapter_revision=selected.downloads.adapter_revision,
        driver_version=selected.driver_version,
        browser_version="fixture",
        lifecycle="caller_managed",
        network_boundary="operator_managed_browser",
        declared_route="direct",
        route_verification="operator_declaration_only",
        browser_subresource_bytes=None,
        browser_subresource_requests=None,
        browser_download_bytes=None,
        content_type="application/pdf",
        media_observation="pdf_header_at_start",
        file_sha256=hashlib.sha256(raw).hexdigest(),
        file_bytes=len(raw),
        collector_file_bytes_read=len(raw),
        collector_dom_bytes_read=0,
        assistance=(),
    )
    with pytest.raises(ValidationError):
        BrowserDownloadCapture(body=raw, evidence=evidence)
    with pytest.raises(ValidationError):
        Page(
            url=evidence.request_url,
            final_url=evidence.final_url,
            status=None,
            content_type=evidence.content_type,
            body=raw,
            human_browser=evidence,
        )


def test_public_collector_composes_the_same_page_with_real_adapter_protocols(
    tmp_path,
    search_endpoint,
    model_endpoint,
    encoder_endpoint,
):
    raw = native_pdf()
    with download_site(raw) as site:
        metadata = (site[0], site[1], [], [], {})
        cfg, _ = assembled(tmp_path, metadata, search_endpoint, model_endpoint, encoder_endpoint)
        search_endpoint[2]["body"] = b'{"results":[]}'
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(selected, page, unrelated):
            selected = download_policy(selected, origin)
            values = cfg.model_dump()
            values.update(
                human_browser=selected.model_dump(),
                request_timeout_seconds=30.0,
                document_extraction=document_config(tmp_path).document_extraction.model_dump(),
            )
            values["research"]["content_types"] = ["text/html", "application/pdf"]
            effective = GhimeraConfig.model_validate(values)
            with pytest.raises(GhimeraRefused, match="source_session_unavailable"):
                Collector(effective, source_resolver=ResolverFixture())
            session = BoundPageHumanSession(selected, page=page, assistant=None)
            collector = Collector(
                effective, source_resolver=ResolverFixture(), human_browser_session=session
            )
            result = await collector.run(
                ResearchRequest(intent="find ports", seeds=(origin + "/research/report",))
            )
            assert result.status == "answered", [
                (row.event, row.refusal) for row in result.harvest.ledger
            ]
            assert result.harvest.documents[0].raw == raw
            assert result.harvest.documents[0].human_browser.acquisition == "browser_download"
            assert result.answer.claims[0].citations[0].matches(result.harvest.documents[0])
            assert model_endpoint[1] and encoder_endpoint[1]
            assert site[1]["/research/report"] == 1

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


def test_human_assistance_resumes_one_download_and_cancellation_preserves_caller(tmp_path):
    raw = docx()
    with download_site(raw, assisted=True) as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(selected, page, unrelated):
            selected = download_policy(selected, origin, mime=DOCX_TYPE, click=True)
            selected = selected.model_copy(
                update={
                    "assistance_reasons": ("challenge_not_solved",),
                    "max_assistance_attempts": 1,
                }
            )
            calls = []

            class FixtureHuman:
                async def assist(self, request):
                    calls.append(request)
                    await page.locator("#human").click()
                    return AssistanceDecision(
                        request_digest=request.content_digest(), action="resume"
                    )

            session = BoundPageHumanSession(selected, page=page, assistant=FixtureHuman())
            capture = await session.capture(
                origin + "/research/report", scope=scope_for_source(site[0])
            )
            assert capture.body == raw and len(calls) == 1
            assert len(capture.evidence.assistance) == 1
            assert capture.evidence.collector_dom_bytes_read > calls[0].observed_dom_bytes
            capture.validate_policy(selected)
            entered = asyncio.Event()

            class WaitingHuman:
                async def assist(self, request):
                    entered.set()
                    await asyncio.Event().wait()

            session = BoundPageHumanSession(selected, page=page, assistant=WaitingHuman())
            task = asyncio.create_task(
                session.capture(origin + "/research/report", scope=scope_for_source(site[0]))
            )
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert caught.value.bytes_read > 0
            assert caught.value.assistance[0].action == "cancelled"
            assert site[1]["/research/report"] == 1
            assert not page.is_closed() and not unrelated.is_closed()

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


def test_cancelling_inflight_file_cancels_only_its_download_and_keeps_tabs(tmp_path):
    released = Event()
    with download_site(b"%PDF-" + b"x" * 8192, slow_body=released) as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(selected, page, unrelated):
            selected = download_policy(selected, origin)
            started = asyncio.Event()
            observed = []

            def record(download):
                observed.append(download)
                started.set()

            page.on("download", record)
            session = BoundPageHumanSession(selected, page=page, assistant=None)
            task = asyncio.create_task(session.capture(origin + "/research/report"))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError) as caught:
                    await task
                assert caught.value.bytes_read == 0
                assert await observed[0].failure() == "canceled"
                assert not page.is_closed() and not unrelated.is_closed()
                assert site[1]["/research/report"] == 1
            finally:
                released.set()
                page.remove_listener("download", record)
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))
