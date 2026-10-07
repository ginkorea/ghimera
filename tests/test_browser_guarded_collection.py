"""Native caller browser -> guarded collector -> parsed, cited, durable evidence."""

import asyncio
import hashlib
import json
import time
import tomllib
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.collector import Collector
from ghimera.config import GhimeraConfig
from ghimera.document_media import DOCX_TYPE
from ghimera.documents import DocumentExtractionSuite, DocumentExtractor
from ghimera.doubles import FakeJudge, KeywordScorer
from ghimera.extraction import HtmlExtractor
from ghimera.fetch import FetchLadder
from ghimera.http import CurlRoute
from ghimera.human_browser import BoundPageHumanSession
from ghimera.human_browser_route import HumanBrowserRoute
from ghimera.human_browser_types import AssistanceDecision, HumanBrowserConfig
from ghimera.journal import read_journal
from ghimera.ledger import Ledger
from ghimera.loop import GoalLoop
from ghimera.models import Goal, Scope
from ghimera.refusals import GhimeraRefused
from ghimera.research import ResearchLoop
from ghimera.research_types import ResearchRequest, ResearchResult
from ghimera.result_archive import ResearchResultArchive
from tests.test_browser_downloads import download_policy
from tests.test_browser_inline_documents import inline_policy
from tests.test_browser_navigation_guard import navigation_policy
from tests.test_collector import assembled, encoder_endpoint, model_endpoint, search_endpoint
from tests.test_document_extraction import config as document_config
from tests.test_document_extraction import docx, native_pdf
from tests.test_html_extraction import ARTICLE
from tests.test_html_extraction import policy as extraction_policy
from tests.test_http_fetch import ResolverFixture, state
from tests.test_human_browser import with_browser
from tests.test_intent_research import (
    AnalystFixture,
    PlannerFixture,
    ReviewerFixture,
    SearchFixture,
)
from tests.test_intent_research import policy as research_policy

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint"]


@contextmanager
def source(*, kind="pdf", robots=None, cross_origin=False, assistance=False):
    calls, cookies = Counter(), Counter()
    raw = docx() if kind == "docx" else native_pdf()
    responses = {}
    denied_robots = robots

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls[(self.headers.get("Host"), self.path)] += 1
            cookies[self.path] += int("synthetic_caller=yes" in self.headers.get("Cookie", ""))
            location = {
                "/research/start": "/research/middle",
                "/research/middle": "/research/final",
                "/research/landing": "/research/ready",
            }.get(self.path)
            if self.path == "/research/middle" and cross_origin:
                location = f"http://127.0.0.1:{server.server_port}/research/final"
            if self.path == "/research/middle" and "middle_location" in responses:
                location = responses["middle_location"]
            if location:
                self.send_response(302 if self.path != "/research/middle" else 307)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            mime, body = "text/html", ARTICLE.encode()
            if self.path == "/robots.txt":
                mime, body = "text/plain", denied_robots or b"User-agent: *\nDisallow:\n"
            elif self.path == "/research/ready":
                body = (
                    b"<article>Port report</article>"
                    b"<a id='get-report' href='/research/start'>Original</a>"
                )
            elif self.path == "/research/final" and kind != "html":
                mime, body = (DOCX_TYPE if kind == "docx" else "application/pdf"), raw
            elif (
                self.path == "/research/final"
                and assistance
                and "human_fixture=complete" not in self.headers.get("Cookie", "")
            ):
                body = b"""<h1>Verify you are human</h1><button id='human'>Continue</button>
                <script>document.querySelector('#human').onclick=()=>{
                    document.cookie='human_fixture=complete; path=/'; location.reload();
                };</script>"""
            self.send_response(200)
            self.send_header("Content-Type", mime)
            if self.path == "/research/final" and kind != "html":
                self.send_header(
                    "Content-Disposition", "inline" if kind == "inline" else "attachment"
                )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *unused):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (server.server_port, calls, cookies, raw, responses)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def selected_policy(previous, origin, *, kind="pdf", click=False, **updates):
    selected = (
        inline_policy(previous, origin)
        if kind == "inline"
        else download_policy(
            previous, origin, mime=DOCX_TYPE if kind == "docx" else "application/pdf", click=click
        )
    )
    data = selected.model_dump()
    data["navigation"] = navigation_policy(**updates).model_dump()
    if click:
        data["downloads"]["actions"][0].update(
            source_url=origin + "/research/start", navigation_url=origin + "/research/landing"
        )
    else:
        data["downloads"].pop("actions", None)
        data["downloads"]["navigation_content_types"] = ["application/pdf", DOCX_TYPE]
    return HumanBrowserConfig.model_validate(data)


def effective(tmp_path, site, selected, **updates):
    previous = state(site)[2]
    graph = tomllib.loads(Path("examples/research-graph.toml").read_text())
    graph["sink_path"] = str(tmp_path / "graph")
    values = previous.config.model_dump()
    values.update(
        human_browser=selected.model_dump(),
        global_concurrency=1,
        per_host_concurrency=1,
        byte_budget=200_000,
        request_timeout_seconds=30.0,
        wall_seconds=120.0,
        extraction=extraction_policy(tmp_path).model_dump(),
        document_extraction=document_config(tmp_path).document_extraction.model_dump(),
        research=research_policy(
            allowed_ports=[site[0]], content_types=["text/html", "application/pdf", DOCX_TYPE]
        ),
        graph=graph,
        journal=dict(
            schema="chimera.run-journal-config/1",
            directory=str(tmp_path / "journal"),
            max_record_bytes=1_000_000,
            max_journal_bytes=10_000_000,
            max_summary_bytes=1_000_000,
            max_records=1000,
        ),
    )
    values.update(updates)
    return GhimeraConfig.model_validate(values)


def ladder_for(cfg, page, assistant=None):
    return FetchLadder(
        (
            HumanBrowserRoute(
                cfg.human_browser,
                BoundPageHumanSession(cfg.human_browser, page=page, assistant=assistant),
            ),
            CurlRoute(cfg, resolver=ResolverFixture()),
        )
    )


def test_navigation_example_is_consumed_by_the_actual_parent_policy():
    from tests.test_human_browser import policy

    fragment = tomllib.loads(Path("examples/browser-navigation-guard.toml").read_text())
    selected = policy().model_dump()
    selected.pop("control_endpoint")
    selected.update(adapter="patchright_page", **fragment["human_browser"])
    parsed = HumanBrowserConfig.model_validate(selected)
    assert parsed.navigation is not None and parsed.navigation.max_redirects == 4
    assert HumanBrowserConfig.model_validate_json(parsed.model_dump_json()) == parsed
    selected["adapter"] = "patchright_cdp"
    selected["control_endpoint"] = policy().control_endpoint
    with pytest.raises(ValidationError, match="caller-bound page"):
        HumanBrowserConfig.model_validate(selected)


@pytest.mark.parametrize(
    "kind,click",
    [
        ("html", False),
        ("pdf", False),
        ("docx", False),
        ("inline", False),
        ("docx", True),
    ],
)
def test_guarded_native_result_is_parsed_cited_journalled_graphed_and_archived(
    tmp_path, kind, click
):
    with source(kind=kind) as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(previous, page, unrelated):
            selected = selected_policy(previous, origin, kind=kind, click=click)
            await page.context.add_cookies([dict(name="synthetic_caller", value="yes", url=origin)])
            cfg = effective(tmp_path, site, selected)
            collection = GoalLoop(
                config=cfg,
                fetcher=ladder_for(cfg, page),
                extractor=DocumentExtractionSuite(
                    html=HtmlExtractor(cfg), documents=DocumentExtractor(cfg)
                ),
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
                ResearchRequest(intent="find ports", seeds=(origin + "/research/start",)),
                run_id="guarded-research",
            )
            assert result.status == "answered", [(r.event, r.reason) for r in result.harvest.ledger]
            document = result.harvest.documents[0]
            evidence = document.human_browser
            assert document.url == origin + "/research/final"
            assert [hop.url for hop in evidence.navigation.hops] == [
                origin + "/research/" + path for path in ("start", "middle", "final")
            ]
            if click:
                assert evidence.initiator_url == origin + "/research/ready"
                assert len(evidence.landing_navigation.hops) == 2
            if kind != "html":
                assert document.raw == site[3]
                assert document.sha256 == hashlib.sha256(site[3]).hexdigest()
                assert document.extracted.document_parse.source_sha256 == document.sha256
            else:
                assert document.extracted.title == "Port infrastructure report"
            assert (
                "terminal construction" if kind in {"pdf", "inline"} else "Taiwan"
            ) in document.extracted.text
            assert result.answer.claims[0].citations[0].matches(document)
            row = next(r for r in result.harvest.ledger if r.human_browser is not None)
            assert row.bytes_read == evidence.collector_bytes_read
            assert result.harvest.receipt.bytes_read == sum(
                r.bytes_read for r in result.harvest.ledger
            )
            assert result.harvest.receipt.fetches == 1 + 3 + (2 if click else 0) + int(
                kind == "inline"
            )
            assert read_journal(cfg.journal, "guarded-research").rows == result.harvest.ledger
            assert (
                next(n for n in result.harvest.graph.nodes if n.role == "document").human_browser
                == evidence
            )
            assert "synthetic_caller" not in result.model_dump_json()
            archive = ResearchResultArchive.create(tmp_path / "archive", run_id="guarded-research")
            try:
                archive.write(result, max_bytes=4_000_000)
            finally:
                archive.close()
            assert ResearchResultArchive.read(tmp_path / "archive", max_bytes=4_000_000) == result
            altered = result.model_dump()
            for part in altered["harvest"]["documents"]:
                part["human_browser"].pop("navigation")
            with pytest.raises(ValidationError):
                ResearchResult.model_validate(altered)
            uncharged = result.model_dump()
            for part in uncharged["harvest"]["ledger"]:
                part.pop("browser_action", None)
            # Keep the summary internally plausible: replay must still catch
            # removal of spend that the retained native chain proves occurred.
            uncharged["harvest"]["receipt"]["fetches"] = 1
            with pytest.raises(
                ValidationError,
                match=(
                    "explicit second fetch spend"
                    if kind == "inline"
                    else "prior source-action spend"
                ),
            ):
                ResearchResult.model_validate(uncharged)
            assert site[2]["/research/start"] == 1
            assert site[2]["/research/middle"] == 1
            assert site[2]["/research/final"] == (2 if kind == "inline" else 1)
            assert not page.is_closed() and not unrelated.is_closed()

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


@pytest.mark.parametrize("refused", ["robots", "scope", "pages", "limit", "bytes"])
def test_full_ladder_denies_before_final_contact_and_releases_every_reservation(tmp_path, refused):
    robots = b"User-agent: *\nDisallow: /research/final\n" if refused == "robots" else None
    with source(robots=robots, cross_origin=refused == "scope") as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(previous, page, unrelated):
            selected = selected_policy(
                previous, origin, max_redirects=1 if refused == "limit" else 3
            )
            cfg = effective(
                tmp_path,
                site,
                selected,
                page_budget=3 if refused == "pages" else 40,
                byte_budget=1 if refused == "bytes" else 200_000,
            )
            scope = Scope(
                allowed_hosts=("fixture.example",),
                allowed_ports=(site[0],),
                max_depth=0,
                content_types=("text/html", "application/pdf", DOCX_TYPE),
            )
            budget, ledger = RunBudget(cfg, time.monotonic), Ledger()
            ladder = ladder_for(cfg, page)
            with pytest.raises(GhimeraRefused):
                await asyncio.wait_for(
                    ladder.fetch(origin + "/research/start", scope, budget, ledger), 10
                )
            assert budget.quiescent
            assert budget.bytes_read == sum(row.bytes_read for row in ledger.snapshot())
            assert not any(path == "/research/final" for _, path in site[1])
            assert not page.is_closed() and not unrelated.is_closed()

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


def test_concrete_collector_uses_guard_and_retains_native_chain_by_default(
    tmp_path, search_endpoint, model_endpoint, encoder_endpoint
):
    with source(kind="html") as site:
        # assembled's source fixture tuple owns bodies at index 4, but this
        # controlled publisher always serves the actual article at its final URL.
        cfg, _ = assembled(tmp_path, site, search_endpoint, model_endpoint, encoder_endpoint)
        origin = f"http://fixture.example:{site[0]}"
        search_endpoint[2]["body"] = json.dumps(
            {
                "results": [
                    {
                        "url": origin + "/research/start",
                        "title": "Port report",
                        "content": "lead only",
                    }
                ]
            }
        ).encode()

        async def scenario(previous, page, unrelated):
            selected = selected_policy(previous, origin, kind="html")
            values = cfg.model_dump()
            values.update(
                human_browser=selected.model_dump(),
                request_timeout_seconds=30.0,
                wall_seconds=120.0,
            )
            selected_cfg = GhimeraConfig.model_validate(values)
            collector = Collector(
                selected_cfg,
                source_resolver=ResolverFixture(),
                human_browser_session=BoundPageHumanSession(selected, page=page, assistant=None),
            )
            result = await collector.run("find ports", run_id="concrete-guarded")
            assert result.status == "answered"
            document = result.harvest.documents[0]
            assert document.human_browser.navigation.request_url == origin + "/research/start"
            assert (
                document.human_browser.navigation.final_url
                == document.url
                == origin + "/research/final"
            )
            assert result.answer.claims[0].citations[0].matches(document)
            assert ResearchResult.model_validate_json(result.model_dump_json()) == result

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


@pytest.mark.parametrize("denied", [False, True])
def test_fresh_redirect_origin_checks_robots_without_reentrant_slot_or_byte_deadlock(
    tmp_path, denied
):
    robots = b"User-agent: *\nDisallow: /research/final\n" if denied else None
    with source(kind="html") as first, source(kind="html", robots=robots) as second:
        origin = f"http://fixture.example:{first[0]}"
        final_origin = f"http://fixture.example:{second[0]}"
        first[4]["middle_location"] = final_origin + "/research/final"

        async def scenario(previous, page, unrelated):
            values = selected_policy(previous, origin, kind="html").model_dump()
            values["origins"] = (
                *values["origins"],
                dict(origin=final_origin, path_prefixes=["/research"], allow_http=True),
            )
            selected = HumanBrowserConfig.model_validate(values)
            base = effective(tmp_path, first, selected)
            cfg_values = base.model_dump()
            cfg_values["http"]["network"]["fixture_ports"] = [first[0], second[0]]
            cfg_values["byte_budget"] = 10_000
            cfg = GhimeraConfig.model_validate(cfg_values)
            scope = Scope(
                allowed_hosts=("fixture.example",),
                allowed_ports=(first[0], second[0]),
                max_depth=0,
                content_types=("text/html",),
            )
            budget, ledger = RunBudget(cfg, time.monotonic), Ledger()
            ladder = ladder_for(cfg, page)
            if denied:
                with pytest.raises(GhimeraRefused, match="robots_disallowed"):
                    await asyncio.wait_for(
                        ladder.fetch(origin + "/research/start", scope, budget, ledger), 10
                    )
                assert second[1][(f"fixture.example:{second[0]}", "/research/final")] == 0
            else:
                fetched = await asyncio.wait_for(
                    ladder.fetch(origin + "/research/start", scope, budget, ledger), 10
                )
                assert fetched.final_url == final_origin + "/research/final"
            assert first[1][(f"fixture.example:{first[0]}", "/robots.txt")] == 1
            assert second[1][(f"fixture.example:{second[0]}", "/robots.txt")] == 1
            assert budget.quiescent
            assert budget.bytes_read == sum(r.bytes_read for r in ledger.snapshot())
            assert not page.is_closed() and not unrelated.is_closed()

        asyncio.run(with_browser(tmp_path, origin, scenario))


def test_cancelling_human_wait_keeps_spend_and_releases_target_and_run_slots(tmp_path):
    with source(kind="html", assistance=True) as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(previous, page, unrelated):
            values = selected_policy(previous, origin, kind="html").model_dump()
            values.update(assistance_reasons=["challenge_not_solved"], max_assistance_attempts=1)
            selected = HumanBrowserConfig.model_validate(values)
            entered = asyncio.Event()

            class WaitingHuman:
                async def assist(self, request):
                    entered.set()
                    await asyncio.Event().wait()

            cfg = effective(tmp_path, site, selected)
            scope = Scope(
                allowed_hosts=("fixture.example",),
                allowed_ports=(site[0],),
                max_depth=0,
                content_types=("text/html",),
            )
            budget, ledger = RunBudget(cfg, time.monotonic), Ledger()
            ladder = ladder_for(cfg, page, WaitingHuman())
            task = asyncio.create_task(
                ladder.fetch(origin + "/research/start", scope, budget, ledger)
            )
            await asyncio.wait_for(entered.wait(), 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            rows = ledger.snapshot()
            assert rows[-1].reason == "request_cancelled"
            assert rows[-1].human_assistance[0].action == "cancelled"
            assert rows[-1].human_assistance[0].request.navigation is not None
            assert rows[-1].bytes_read > 0
            assert budget.quiescent
            assert budget.bytes_read == sum(r.bytes_read for r in rows)
            # Same run/target can immediately continue; cancellation must not
            # strand exclusive interception or the sole global request slot.
            resumed = await asyncio.wait_for(
                ladder.fetch(origin + "/research/ready", scope, budget, ledger), 10
            )
            assert resumed.final_url == origin + "/research/ready"
            assert budget.quiescent
            assert not page.is_closed() and not unrelated.is_closed()

        asyncio.run(with_browser(tmp_path, origin, scenario))


def test_guard_yields_to_explicit_human_then_readmits_and_preserves_both_native_chains(tmp_path):
    with source(kind="html", assistance=True) as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(previous, page, unrelated):
            values = selected_policy(previous, origin, kind="html").model_dump()
            values.update(assistance_reasons=["challenge_not_solved"], max_assistance_attempts=1)
            selected = HumanBrowserConfig.model_validate(values)
            requests = []

            class FixtureHuman:
                async def assist(self, request):
                    requests.append(request)
                    await page.locator("#human").click()
                    await page.locator("article").wait_for()
                    return AssistanceDecision(
                        request_digest=request.content_digest(), action="resume"
                    )

            cfg = effective(tmp_path, site, selected)
            result = await GoalLoop(
                config=cfg,
                fetcher=ladder_for(cfg, page, FixtureHuman()),
                extractor=HtmlExtractor(cfg),
                scorer=KeywordScorer(),
                judge=FakeJudge(satisfied=True),
            ).run(
                Goal(text="find ports", seeds=(origin + "/research/start",)),
                scope=Scope(
                    allowed_hosts=("fixture.example",),
                    allowed_ports=(site[0],),
                    max_depth=0,
                    content_types=("text/html",),
                ),
                run_id="human-guarded",
            )
            assert len(result.documents) == 1, [(r.event, r.reason) for r in result.ledger]
            evidence = result.documents[0].human_browser
            assert len(requests) == 1
            assert requests[0].navigation.request_url == origin + "/research/start"
            assert evidence.assistance[0].request == requests[0]
            assert (
                evidence.navigation.hops[0].request_id != requests[0].navigation.hops[0].request_id
            )
            assert evidence.collector_bytes_read > evidence.dom_bytes
            assert result.receipt.fetches == 7  # robots + two collector chains; not human traffic
            assert read_journal(cfg.journal, "human-guarded").rows == result.ledger
            assert type(result).model_validate_json(result.model_dump_json()) == result
            assert not page.is_closed() and not unrelated.is_closed()

        asyncio.run(with_browser(tmp_path, origin, scenario))
