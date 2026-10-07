"""Actual browser -> native extraction -> research graph/archive and round resume."""

import asyncio
import json
import time

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.collector import Collector
from ghimera.config import GhimeraConfig
from ghimera.continuation import ResearchSuspended
from ghimera.fetch import FetchLadder
from ghimera.http import CurlRoute
from ghimera.human_browser import ChromiumHumanSession
from ghimera.human_browser_route import HumanBrowserRoute
from ghimera.human_browser_types import AssistanceDecision
from ghimera.ledger import Ledger
from ghimera.models import Harvest, Page
from ghimera.refusals import GhimeraRefused
from ghimera.result_archive import ResearchResultArchive
from tests.test_collector import (
    assembled,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)
from tests.test_html_extraction import ARTICLE
from tests.test_http_fetch import ResolverFixture
from tests.test_http_fetch import state as source_state
from tests.test_human_browser import with_browser
from tests.test_research_continuation import configured

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


@pytest.mark.parametrize("resume", [False, True])
def test_human_session_completes_concrete_collector_and_private_archive(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, resume
):
    cfg, url = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    origin = url.removesuffix("/plain")
    ready = json.dumps(ARTICLE).replace("</", "<\\/")
    source_site[4]["/plain"] = (
        "<title>Just a moment</title><p>Verify you are human</p>"
        "<button id='human'>Fixture human assistance</button>"
        f"<script>document.querySelector('#human').onclick=()=>"
        f"{{document.documentElement.innerHTML={ready};}};</script>"
    ).encode()

    async def scenario(selected, page, unrelated):
        selected = selected.model_copy(
            update={
                "origins": (selected.origins[0].model_copy(update={"path_prefixes": ("/plain",)}),)
            }
        )
        values = cfg.model_dump()
        continuation = configured(tmp_path)
        values.update(
            human_browser=selected.model_dump(),
            graph=continuation.graph.model_dump(),
            request_timeout_seconds=30.0,
            wall_seconds=120.0,
        )
        if resume:
            values.update(
                journal=continuation.journal.model_dump(),
                continuation=continuation.continuation.model_dump(),
            )
        effective = GhimeraConfig.model_validate(values)
        calls = []

        class FixtureHuman:
            async def assist(self, request):
                calls.append(request)
                await page.locator("#human").click()
                await page.locator("article").wait_for()
                return AssistanceDecision(request_digest=request.content_digest(), action="resume")

        collector = Collector(
            effective, source_resolver=ResolverFixture(), human_assistant=FixtureHuman()
        )
        if resume:
            with pytest.raises(ResearchSuspended) as caught:
                await collector.run("find ports", run_id="human-research", suspend_after_rounds=1)
            prior_count = source_site[1]["/plain"]
            result = await Collector(
                effective, source_resolver=ResolverFixture(), human_assistant=FixtureHuman()
            ).resume("human-research", checkpoint_sha256=caught.value.receipt.sha256)
            assert source_site[1]["/plain"] == prior_count
        else:
            result = await collector.run("find ports", run_id="human-research")
        assert result.status == "answered"
        document = result.harvest.documents[0]
        assert document.human_browser is not None
        evidence = document.human_browser
        assert evidence.target_id == selected.target_id
        assert evidence.dom_sha256 == document.sha256
        assert len(calls) == 1
        assert document.rendered is None and document.transport is None
        assert document.extracted.title == "Port infrastructure report"
        assert document.extracted.extraction.source_sha256 == evidence.dom_sha256
        assert result.answer.claims[0].citations[0].matches(document)
        row = next(row for row in result.harvest.ledger if row.human_browser is not None)
        assert row.status is None and row.transport is None
        assert row.bytes_read == evidence.collector_dom_bytes_read > len(document.raw)
        assert row.human_browser == evidence
        assert result.harvest.receipt.bytes_read == sum(
            row.bytes_read for row in result.harvest.ledger
        )
        assert source_site[1]["/plain"] == 1
        node = next(node for node in result.harvest.graph.nodes if node.role == "document")
        assert node.human_browser == evidence
        assert Harvest.model_validate_json(result.harvest.model_dump_json()) == result.harvest
        archive = ResearchResultArchive.create(tmp_path / "result", run_id="human-research")
        try:
            archive.write(result, max_bytes=4_000_000)
        finally:
            archive.close()
        assert ResearchResultArchive.read(tmp_path / "result", max_bytes=4_000_000) == result

        # The paired reader rejects plausible-looking HTTP attribution and metadata stripping.
        raw = result.harvest.model_dump()
        raw["ledger"][row.sequence]["status"] = 200
        with pytest.raises(ValidationError):
            Harvest.model_validate(raw)
        raw = result.harvest.model_dump()
        raw["documents"][0]["human_browser"] = None
        with pytest.raises(ValidationError):
            Harvest.model_validate(raw)
        raw = result.harvest.model_dump()
        document_node = next(item for item in raw["graph"]["nodes"] if item["role"] == "document")
        document_node["human_browser"] = None
        with pytest.raises(ValidationError):
            Harvest.model_validate(raw)
        raw = result.harvest.model_dump()
        raw["ledger"][row.sequence]["human_browser"] = None
        raw["documents"][0]["human_browser"] = None
        for item in raw["graph"]["nodes"]:
            item["human_browser"] = None
        with pytest.raises(ValidationError):
            Harvest.model_validate(raw)

    asyncio.run(with_browser(tmp_path, origin, scenario))


def test_absent_browser_evidence_cannot_invent_a_missing_http_status():
    with pytest.raises(ValidationError):
        Page(
            url="https://example.org",
            final_url="https://example.org",
            status=None,
            content_type="text/html",
            body=b"<article>unbound</article>",
        )


@pytest.mark.parametrize("failure", ["byte_budget", "assistant_failed", "robots"])
def test_browser_failure_never_retries_through_http_and_records_actual_spend(
    tmp_path, source_site, failure
):
    _, scope, initial_budget, _, origin = source_state(source_site)
    if failure == "robots":
        source_site[4]["/robots.txt"] = b"User-agent: *\nDisallow: /plain\n"
    source_site[4]["/plain"] = b"<title>Just a moment</title><p>Verify you are human</p>"

    async def scenario(selected, page, unrelated):
        selected = selected.model_copy(
            update={
                "origins": (selected.origins[0].model_copy(update={"path_prefixes": ("/plain",)}),)
            }
        )
        values = initial_budget.config.model_dump()
        values.update(human_browser=selected.model_dump(), request_timeout_seconds=30.0)
        if failure == "byte_budget":
            values["byte_budget"] = 70
        config = GhimeraConfig.model_validate(values)
        calls = []

        class FixtureHuman:
            async def assist(self, request):
                calls.append(request)
                raise RuntimeError("secret fixture message must not enter the ledger")

        ladder = FetchLadder(
            (
                HumanBrowserRoute(
                    selected, ChromiumHumanSession(selected, assistant=FixtureHuman())
                ),
                CurlRoute(config, resolver=ResolverFixture()),
            )
        )
        budget, ledger = RunBudget(config, time.monotonic), Ledger()
        with pytest.raises(GhimeraRefused) as caught:
            await ladder.fetch(origin + "/plain", scope, budget, ledger)
        if failure == "robots":
            assert caught.value.code.value == "robots_disallowed"
            assert source_site[1]["/plain"] == 0
            assert page.url == "about:blank"
        else:
            assert source_site[1]["/plain"] == 1
            row = ledger.snapshot()[-1]
            assert row.route == "human_browser_dom" and row.status is None
            assert row.bytes_read > 0
            assert "secret fixture" not in row.model_dump_json()
            if failure == "assistant_failed":
                assert row.human_assistance[0].action == "assistance_failed"
                assert len(calls) == 1
            else:
                assert caught.value.code.value == "budget_exhausted"
                assert not calls and budget.bytes_read == 70
        assert budget.bytes_read == sum(row.bytes_read for row in ledger.snapshot())
        assert budget.quiescent

    asyncio.run(with_browser(tmp_path, origin, scenario))
