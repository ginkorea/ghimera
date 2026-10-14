"""Every implemented route obeys the same declared, bounded, logged template.

Mutation witnesses: disable execute-final, scope check, reservation or terminal-challenge
guard; test_contract_mutations runs these isolated changes and requires a test failure.
"""

import asyncio
from pathlib import Path

import pytest
from test_http_fetch import site as site
from test_http_fetch import state as http_state

from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeRoute
from ghimera.fetch import FetchLadder, FetchRoute
from ghimera.http import CurlRoute
from ghimera.ledger import Ledger
from ghimera.models import FetchRequest, Page, Scope
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.spend import NO_TRANSFER_BOUND, RunSpend


class BrowserFixture(FakeRoute):
    name = "browser_fixture"
    needs_browser = True
    cost = 2


ROUTES = (FakeRoute, BrowserFixture, CurlRoute)


def state():
    cfg = GhimeraConfig.from_toml(Path("examples/chimera.toml"))
    return (
        Scope(allowed_hosts=("example.org",), max_depth=1, content_types=("text/html",)),
        RunSpend(cfg, lambda: 0.0),
        Ledger(),
    )


@pytest.mark.parametrize("route_type", ROUTES)
def test_fetch_conformance(route_type, site):
    if route_type is CurlRoute:
        ladder, scope, spend, ledger, origin = http_state(site)
        url = origin + "/plain"
        expected_attempts, expected_name = 2, "curl_cffi"
    else:
        route = route_type()
        scope, spend, ledger = state()
        ladder, url = FetchLadder((route,)), "https://example.org/a"
        expected_attempts, expected_name = 1, route.name
    result = asyncio.run(ladder.fetch(url, scope, spend, ledger))
    assert scope.permits(result.final_url)
    assert spend.fetches == expected_attempts
    assert spend.bytes_read == sum(row.bytes_read for row in ledger.snapshot())
    assert len(ledger.snapshot()) == expected_attempts
    assert ledger.snapshot()[-1].route == expected_name
    assert ledger.snapshot()[-1].bytes_read == len(result.body)
    if route_type is not CurlRoute:
        # No run byte ceiling: a non-HTTP route is bounded only by its own reader.
        assert route.requests[0].max_bytes == NO_TRANSFER_BOUND


@pytest.mark.parametrize("route_type", ROUTES)
def test_scope_refuses_before_fetch(route_type, site):
    if route_type is CurlRoute:
        ladder, scope, spend, ledger, _ = http_state(site)
    else:
        scope, spend, ledger = state()
        ladder = FetchLadder((route_type(),))
    with pytest.raises(GhimeraRefused, match="out_of_scope"):
        asyncio.run(ladder.fetch("https://other.example/a", scope, spend, ledger))
    assert ledger.snapshot() == ()
    assert not site[1]
    assert spend.fetches == 0


@pytest.mark.parametrize(
    "code",
    (
        RefusalCode.CHALLENGE_NOT_SOLVED,
        RefusalCode.ROBOTS_DISALLOWED,
        RefusalCode.LOGIN_WALL,
        RefusalCode.PAYWALL,
    ),
)
def test_terminal_refusal_never_escalates(code):
    first = FakeRoute(refusal=code)
    second = BrowserFixture()
    scope, spend, ledger = state()
    with pytest.raises(GhimeraRefused, match=code.value):
        asyncio.run(
            FetchLadder((second, first)).fetch("https://example.org/a", scope, spend, ledger)
        )
    assert second.requests == []
    assert ledger.snapshot()[0].refusal == code


def test_a_route_cannot_override_the_final_template():
    with pytest.raises(TypeError):
        type("BadRoute", (FakeRoute,), {"execute": lambda *args: None})
    with pytest.raises(TypeError):
        type("BadLadder", (FetchLadder,), {"fetch": lambda *args: None})
    assert FetchRoute.REFERENCE["template"] == "FakeRoute"


class OversizedRoute(FakeRoute):
    name = "oversized_fixture"
    uses_http = True

    async def attempt(self, request: FetchRequest) -> Page:
        return Page(
            url=request.url,
            final_url=request.url,
            status=200,
            content_type="text/html",
            body=b"x" * (request.max_bytes + 1),
        )


def test_adapter_overrun_is_accounted_and_refused():
    scope, spend, ledger = state()
    cfg = spend.config.model_dump()
    cfg["http"] = {
        "schema": "chimera.http/1",
        "max_response_bytes": 3,
        "max_header_bytes": 16_384,
        "max_redirects": 0,
        "retry_backoff_seconds": 0.001,
        "retry_jitter_seconds": 0.001,
        "robots_cache_seconds": 60.0,
        "conditional_cache_entries": 1,
        "robots": {"schema": "chimera.robots/1", "mode": "honor", "product_token": "Chimera"},
        "network": {"schema": "chimera.network/1", "mode": "public"},
    }
    spend = RunSpend(GhimeraConfig.model_validate(cfg), lambda: 0.0)
    with pytest.raises(GhimeraRefused, match="adapter_contract"):
        asyncio.run(
            FetchLadder((OversizedRoute(),)).fetch("https://example.org/a", scope, spend, ledger)
        )
    assert spend.bytes_read == ledger.snapshot()[0].bytes_read == 4
