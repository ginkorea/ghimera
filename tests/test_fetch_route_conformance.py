"""Every implemented route obeys the same declared, bounded, logged template.

Mutation witnesses: disable execute-final, scope check, reservation or terminal-challenge
guard; test_contract_mutations runs these isolated changes and requires a test failure.
"""

import asyncio
from pathlib import Path

import pytest
from test_http_fetch import site as site
from test_http_fetch import state as http_state

from chimera.budget import RunBudget
from chimera.config import ChimeraConfig
from chimera.doubles import FakeRoute
from chimera.fetch import FetchLadder, FetchRoute
from chimera.http import CurlRoute
from chimera.ledger import Ledger
from chimera.models import FetchRequest, Page, Scope
from chimera.refusals import ChimeraRefused, RefusalCode


class BrowserFixture(FakeRoute):
    name = "browser_fixture"
    needs_browser = True
    cost = 2


ROUTES = (FakeRoute, BrowserFixture, CurlRoute)


def state():
    cfg = ChimeraConfig.from_toml(Path("examples/chimera.toml"))
    return (
        Scope(allowed_hosts=("example.org",), max_depth=1, content_types=("text/html",)),
        RunBudget(cfg, lambda: 0.0),
        Ledger(),
    )


@pytest.mark.parametrize("route_type", ROUTES)
def test_fetch_conformance(route_type, site):
    if route_type is CurlRoute:
        ladder, scope, budget, ledger, origin = http_state(site)
        url = origin + "/plain"
        expected_attempts, expected_name = 2, "curl_cffi"
    else:
        route = route_type()
        scope, budget, ledger = state()
        ladder, url = FetchLadder((route,)), "https://example.org/a"
        expected_attempts, expected_name = 1, route.name
    result = asyncio.run(ladder.fetch(url, scope, budget, ledger))
    assert scope.permits(result.final_url)
    assert budget.fetches == expected_attempts
    assert budget.bytes_read == sum(row.bytes_read for row in ledger.snapshot())
    assert len(ledger.snapshot()) == expected_attempts
    assert ledger.snapshot()[-1].route == expected_name
    assert ledger.snapshot()[-1].bytes_read == len(result.body)
    if route_type is not CurlRoute:
        assert route.requests[0].max_bytes == budget.config.byte_budget


@pytest.mark.parametrize("route_type", ROUTES)
def test_scope_refuses_before_fetch(route_type, site):
    if route_type is CurlRoute:
        ladder, scope, budget, ledger, _ = http_state(site)
    else:
        scope, budget, ledger = state()
        ladder = FetchLadder((route_type(),))
    with pytest.raises(ChimeraRefused, match="out_of_scope"):
        asyncio.run(ladder.fetch("https://other.example/a", scope, budget, ledger))
    assert ledger.snapshot() == ()
    assert not site[1]
    assert budget.fetches == 0


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
    scope, budget, ledger = state()
    with pytest.raises(ChimeraRefused, match=code.value):
        asyncio.run(
            FetchLadder((second, first)).fetch("https://example.org/a", scope, budget, ledger)
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

    async def attempt(self, request: FetchRequest) -> Page:
        return Page(
            url=request.url,
            final_url=request.url,
            status=200,
            content_type="text/html",
            body=b"x" * (request.max_bytes + 1),
        )


def test_adapter_overrun_is_accounted_and_refused():
    scope, budget, ledger = state()
    cfg = budget.config.model_dump()
    cfg["byte_budget"] = 3
    budget = RunBudget(ChimeraConfig.model_validate(cfg), lambda: 0.0)
    with pytest.raises(ChimeraRefused, match="adapter_contract"):
        asyncio.run(
            FetchLadder((OversizedRoute(),)).fetch("https://example.org/a", scope, budget, ledger)
        )
    assert budget.bytes_read == ledger.snapshot()[0].bytes_read == 4
