"""Configured ordinary search UI, observed links only, shared bounded accounting."""

import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.ledger import Ledger
from ghimera.refusals import GhimeraRefused
from ghimera.research_types import SearchQuery
from ghimera.search_config import SearxConfig
from ghimera.search_history import SearchHistory
from tests.test_html_extraction import policy as extraction_policy
from tests.test_http_fetch import ResolverFixture
from tests.test_search_conformance import endpoint, provider

__all__ = ["endpoint"]

HTML = """<!doctype html><html><body>
<a href="https://irrelevant.example/preferences">Preferences</a>
<div id="results"><div id="urls">
<article class="result result-default category-general"><div class="result_inner">
<a class="url_header" href="https://example.org/one">display URL</a>
<h3><a href="https://example.org/one">港口 <b>報告</b></a></h3>
<p class="content">原文 <strong>摘要</strong> &amp; evidence.</p></div>
<a class="cache_link" href="https://cache.example/one">cached</a></article>
<article class="result result-default"><h3><a href="https://example.org/two">Second</a></h3>
<p class="content">Second snippet.</p></article>
</div></div></body></html>""".encode()


def html_provider(tmp_path, endpoint, **updates):
    from ghimera.searxng import SearxHtmlSearch

    _, cfg = provider(endpoint)
    raw = cfg.model_dump(by_alias=True)
    raw["search"] = {
        "schema": "chimera.searxng/2",
        "endpoint": f"http://fixture.example:{endpoint[0]}/search",
        "language": "zh-TW",
        "safe_search": 1,
        "time_range": "month",
        "response_format": "html",
    }
    raw["extraction"] = extraction_policy(tmp_path)
    raw.update(updates)
    cfg = GhimeraConfig.model_validate(raw)
    return SearxHtmlSearch(cfg, cfg.search, resolver=ResolverFixture()), cfg


def test_search_modes_are_explicit_and_legacy_identity_does_not_change():
    values = {
        "schema": "chimera.searxng/1",
        "endpoint": "https://example.invalid/search",
        "language": "all",
        "safe_search": 1,
        "time_range": "",
    }
    assert SearxConfig.model_validate(values).model_dump() == values
    with pytest.raises(ValidationError):
        SearxConfig.model_validate(dict(values, response_format="html"))
    values["schema"] = "chimera.searxng/2"
    with pytest.raises(ValidationError):
        SearxConfig.model_validate(values)
    for mode in ("html", "json"):
        assert (
            SearxConfig.model_validate(dict(values, response_format=mode)).response_format == mode
        )


def test_html_search_retains_native_results_and_excludes_navigation(tmp_path, endpoint):
    endpoint[2].update(body=HTML, content_type="text/html; charset=utf-8")
    adapter, cfg = html_provider(tmp_path, endpoint)
    ledger = Ledger()
    budget = RunBudget(cfg, lambda: 0.0)
    history = SearchHistory(adapter, budget, ledger)
    response = asyncio.run(history.discover(SearchQuery(text="ports", question_ids=("q1",))))
    assert [hit.url for hit in response.hits] == [
        "https://example.org/one",
        "https://example.org/two",
    ]
    assert response.hits[0].title == "港口 報告"
    assert response.hits[0].snippet == "原文 摘要 & evidence."
    assert response.raw == HTML == history.observations[0].response.raw
    assert ledger.snapshot()[0].search_response_sha256 == response.content_digest()
    assert budget.search_calls == budget.fetches == len(endpoint[1]) == 1
    parameters = parse_qs(urlsplit(endpoint[1][0]).query)
    assert "format" not in parameters and parameters["theme"] == ["simple"]
    assert parameters["language"] == ["zh-TW"]


@pytest.mark.parametrize(
    "failure", ("layout", "missing_link", "challenge", "substation", "redirect")
)
def test_html_search_does_not_turn_failed_layout_or_access_into_results(
    tmp_path, endpoint, failure
):
    body = HTML
    if failure == "layout":
        body = b"<html><h3><a href='https://example.org/one'>Not a results page</a></h3></html>"
    elif failure == "missing_link":
        body = HTML.replace(b"<h3><a", b"<h3><span").replace(b"</a></h3>", b"</span></h3>")
    elif failure == "challenge":
        body = (
            b"<html><title>Just a moment...</title><div id='cf-chl-widget'>challenge</div></html>"
        )
    elif failure == "substation":
        body = (
            b"<html><title>Security check - Substation</title>"
            b"<script>document.cookie='__substation_pow=value';</script></html>"
        )
    else:
        endpoint[2]["status"] = 302
    endpoint[2].update(body=body, content_type="text/html")
    adapter, cfg = html_provider(tmp_path, endpoint)
    budget, ledger = RunBudget(cfg, lambda: 0.0), Ledger()
    with pytest.raises(GhimeraRefused):
        asyncio.run(
            adapter.discover(SearchQuery(text="ports", question_ids=("q1",)), budget, ledger)
        )
    assert len(endpoint[1]) == 1
    assert budget.bytes_read == ledger.snapshot()[0].bytes_read == len(body)
    assert ledger.snapshot()[0].refusal is not None
    if failure in {"challenge", "substation"}:
        assert ledger.snapshot()[0].refusal.value == "challenge_not_solved"
        assert not cfg.extraction.work_directory.exists()


def test_html_empty_results_are_success_only_in_the_observed_results_envelope(tmp_path, endpoint):
    endpoint[2].update(
        body=b'<html><div id="results"><div id="urls"></div></div></html>', content_type="text/html"
    )
    adapter, cfg = html_provider(tmp_path, endpoint)
    response = asyncio.run(
        adapter.discover(
            SearchQuery(text="ports", question_ids=("q1",)), RunBudget(cfg, lambda: 0.0), Ledger()
        )
    )
    assert response.hits == ()


def test_json_mode_does_not_silently_retry_html(tmp_path, endpoint):
    endpoint[2].update(body=HTML, content_type="text/html")
    adapter, cfg = provider(endpoint)
    with pytest.raises(GhimeraRefused, match="search_unavailable"):
        asyncio.run(
            adapter.discover(
                SearchQuery(text="ports", question_ids=("q1",)),
                RunBudget(cfg, lambda: 0.0),
                Ledger(),
            )
        )
    assert len(endpoint[1]) == 1


def test_html_provider_requires_its_explicit_format_and_parser_recipe(tmp_path, endpoint):
    from ghimera.searxng import SearxHtmlSearch, SearxSearch

    adapter, cfg = html_provider(tmp_path, endpoint)
    assert adapter.revision == "search-html/1"
    with pytest.raises(ValueError, match="format"):
        SearxSearch(cfg, cfg.search, resolver=ResolverFixture())
    values = cfg.model_dump(by_alias=True)
    values.pop("extraction")
    with pytest.raises(ValueError, match="extraction"):
        SearxHtmlSearch(
            GhimeraConfig.model_validate(values), cfg.search, resolver=ResolverFixture()
        )
    assert not endpoint[1]


def test_html_response_limit_and_parser_input_budget_are_real(tmp_path, endpoint):
    from tests.test_intent_research import policy

    endpoint[2].update(body=HTML, content_type="text/html")
    adapter, cfg = html_provider(tmp_path, endpoint, research=policy(results_per_query=1))
    response = asyncio.run(
        adapter.discover(
            SearchQuery(text="ports", question_ids=("q1",)), RunBudget(cfg, lambda: 0.0), Ledger()
        )
    )
    assert len(response.hits) == 1 and response.raw == HTML
    adapter, cfg = html_provider(
        tmp_path / "limited",
        endpoint,
        extraction=extraction_policy(tmp_path / "limited", max_input_bytes=len(HTML) - 1),
    )
    budget, ledger = RunBudget(cfg, lambda: 0.0), Ledger()
    with pytest.raises(GhimeraRefused, match="search_unavailable"):
        asyncio.run(
            adapter.discover(SearchQuery(text="ports", question_ids=("q1",)), budget, ledger)
        )
    assert budget.bytes_read == len(HTML)
    assert not cfg.extraction.work_directory.exists()


def test_parser_cancel_keeps_bytes_already_fetched(tmp_path, endpoint, monkeypatch):
    endpoint[2].update(body=HTML, content_type="text/html")
    adapter, cfg = html_provider(tmp_path, endpoint)

    async def cancelled(payload):
        raise asyncio.CancelledError

    monkeypatch.setattr(adapter._worker, "run", cancelled)
    budget, ledger = RunBudget(cfg, lambda: 0.0), Ledger()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            adapter.discover(SearchQuery(text="ports", question_ids=("q1",)), budget, ledger)
        )
    assert budget.bytes_read == ledger.snapshot()[0].bytes_read == len(HTML)
    assert len(endpoint[1]) == 1 and ledger.snapshot()[0].refusal is not None


def test_html_search_uses_configured_tor_and_never_source_dns(tmp_path, endpoint):
    from ghimera.searxng import SearxHtmlSearch
    from tests.test_tor_transport import NoLocalDNS, policy, socks_server

    endpoint[2].update(body=HTML, content_type="text/html")

    async def scenario():
        seen = []
        server = await socks_server(endpoint[0], seen)
        async with server:
            transport = policy(server.sockets[0].getsockname()[1]).model_dump(by_alias=True)
            transport["tor"]["allowed_ports"] = [endpoint[0]]
            _, cfg = html_provider(tmp_path, endpoint, transport=transport)
            adapter = SearxHtmlSearch(cfg, cfg.search, resolver=NoLocalDNS())
            response = await adapter.discover(
                SearchQuery(text="ports", question_ids=("q1",)),
                RunBudget(cfg, asyncio.get_running_loop().time),
                Ledger(),
            )
            assert response.transport.mode == "tor" and response.raw == HTML
            assert [row[0] for row in seen] == [0xF0, 1]

    asyncio.run(scenario())


def test_reporting_about_a_security_check_is_not_an_interstitial():
    from ghimera.http import page_barrier
    from ghimera.models import Page

    body = (
        b"<html><title>Security research report</title><article>"
        b"Substation uses document.cookie and __substation_pow in a security check."
        b"</article></html>"
    )
    page = Page(
        url="https://example.org/report",
        final_url="https://example.org/report",
        status=200,
        content_type="text/html",
        body=body,
    )
    assert page_barrier(page) is None
