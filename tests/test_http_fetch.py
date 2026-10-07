"""C1 contracts first: real libcurl against a controlled laptop HTTP server."""

import asyncio
import gzip
import threading
import time
from collections import Counter
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig, NetworkPolicy
from ghimera.fetch import FetchLadder
from ghimera.http import CurlRoute, NetworkGuard
from ghimera.ledger import Ledger
from ghimera.models import Scope
from ghimera.politeness import Politeness
from ghimera.refusals import GhimeraRefused


class ResolverFixture:
    def __init__(self, addresses=("127.0.0.1",)):
        self.addresses = addresses
        self.calls = []

    async def resolve(self, host, port):
        self.calls.append((host, port))
        return self.addresses


@pytest.fixture
def site():
    counts = Counter()
    starts = []
    seen_headers = []
    bodies = {"/plain": b"<article>port infrastructure evidence</article>"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            counts[self.path] += 1
            starts.append((self.path, time.monotonic()))
            seen_headers.append((self.path, dict(self.headers)))
            headers = {}
            status = 200
            if self.path == "/robots.txt":
                body = bodies.get("/robots.txt", b"User-agent: *\nDisallow: /blocked\n")
                headers["Content-Type"] = "text/plain"
            elif self.path == "/offscope":
                status, body = 302, b"redirect"
                headers["Location"] = "http://other.example/private"
            elif self.path == "/redirect":
                status, body = 302, b"redirect"
                headers["Location"] = "/plain"
            elif self.path == "/loop":
                status, body = 302, b"redirect"
                headers["Location"] = "/loop"
            elif self.path == "/flaky" and counts[self.path] == 1:
                status, body = 503, b"retry later"
            elif self.path in {"/limited", "/always-limited"} and (
                self.path == "/always-limited" or counts[self.path] == 1
            ):
                status, body = 429, b"please wait"
                headers["Retry-After"] = "1"
            elif self.path == "/forbidden":
                status, body = 403, b"denied"
            elif self.path == "/challenge":
                body = (
                    b"<html><title>Just a moment...</title><div class='cf-turnstile'></div></html>"
                )
            elif self.path == "/login":
                body = b"<form><input type='password' required>Sign in to continue</form>"
            elif self.path == "/paywall":
                body = b"<article>Subscribe to continue reading</article>"
            elif self.path == "/oversized":
                body = gzip.compress(b"x" * 200_000)
                headers["Content-Encoding"] = "gzip"
            elif self.path == "/conditional":
                body = bodies["/plain"]
                headers["ETag"] = '"original"'
                if self.headers.get("If-None-Match") == '"original"':
                    status, body = 304, b""
            elif self.path == "/slow":
                time.sleep(0.12)
                body = bodies["/plain"]
            elif self.path == "/partial":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", "10000")
                self.end_headers()
                self.wfile.write(b"partial")
                self.wfile.flush()
                time.sleep(0.3)
                return
            else:
                body = bodies["/plain"]
            self.send_response(status)
            self.send_header("Content-Type", headers.pop("Content-Type", "text/html"))
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server.server_port, counts, starts, seen_headers, bodies
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def state(site, **updates):
    port = site[0]
    raw = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    raw.update(
        page_budget=40,
        per_host_delay_seconds=0.01,
        global_requests_per_second=1000.0,
        per_host_concurrency=2,
        global_concurrency=4,
        http={
            "schema": "chimera.http/1",
            "max_response_bytes": 100_000,
            "max_header_bytes": 16_384,
            "max_redirects": 3,
            "retry_backoff_seconds": 0.001,
            "retry_jitter_seconds": 0.001,
            "robots_cache_seconds": 60.0,
            "conditional_cache_entries": 5,
            "robots": {"schema": "chimera.robots/1", "mode": "honor", "product_token": "Chimera"},
            "network": {
                "schema": "chimera.network/1",
                "mode": "loopback_fixture",
                "fixture_addresses": ["127.0.0.1"],
                "fixture_ports": [port],
            },
        },
    )
    raw.update(updates)
    cfg = GhimeraConfig.model_validate(raw)
    assert cfg.http is not None
    scope = Scope(
        allowed_hosts=("fixture.example",),
        allowed_ports=(port,),
        max_depth=2,
        content_types=("text/html",),
    )
    route = CurlRoute(cfg, resolver=ResolverFixture())
    return (
        FetchLadder((route,)),
        scope,
        RunBudget(cfg, time.monotonic),
        Ledger(),
        f"http://fixture.example:{port}",
    )


def fetch(site, path, **updates):
    ladder, scope, budget, ledger, origin = state(site, **updates)
    page = asyncio.run(ladder.fetch(origin + path, scope, budget, ledger))
    return page, budget, ledger


def pacing_state(site, **updates):
    _, _, previous, _, _ = state(site)
    raw = previous.config.model_dump(by_alias=True)
    raw["cadence"] = {
        "schema": "ghimera.cadence/1",
        "jitter_seconds": 0.01,
        "throttle_statuses": [429, 503],
        "throttle_base_seconds": 0.02,
        "throttle_multiplier": 2.0,
        "max_backoff_seconds": 0.1,
        "respect_retry_after": True,
    }
    raw.update(updates)
    return state(site, **raw)


def test_real_http_throttle_retries_after_server_wait_with_exact_spend(site):
    ladder, scope, budget, ledger, origin = pacing_state(site)
    result = asyncio.run(ladder.fetch(origin + "/limited", scope, budget, ledger))
    assert result.status == 200
    starts = [stamp for path, stamp in site[2] if path == "/limited"]
    assert len(starts) == 2
    assert starts[1] - starts[0] >= 0.99
    assert budget.fetches == 3
    assert sum(row.bytes_read for row in ledger.snapshot()) == budget.bytes_read
    assert [row.status for row in ledger.snapshot() if row.event == "fetch"] == [200, 429, 200]


def test_real_http_exhausted_throttle_does_not_fall_back_to_another_route(site):
    from ghimera.doubles import FakeRoute

    original, scope, budget, ledger, origin = pacing_state(site, retry_budget=0)
    fallback = FakeRoute()
    ladder = FetchLadder(original._routes + (fallback,))
    with pytest.raises(GhimeraRefused, match="fetch_failed"):
        asyncio.run(ladder.fetch(origin + "/always-limited", scope, budget, ledger))
    assert site[1]["/always-limited"] == 1
    assert not fallback.requests
    assert ledger.snapshot()[-1].status == 429


def test_real_http_robots_and_every_request_counted(site):
    page, budget, ledger = fetch(site, "/plain")
    assert page.body.startswith(b"<article>")
    assert budget.fetches == site[1]["/robots.txt"] + site[1]["/plain"] == 2
    assert budget.bytes_read == sum(row.bytes_read for row in ledger.snapshot())
    assert all(row.route == "curl_cffi" for row in ledger.snapshot())
    assert all(headers["User-Agent"].startswith("Chimera/") for _, headers in site[3])
    assert all("Cookie" not in headers and "Authorization" not in headers for _, headers in site[3])


def test_robots_disallow_prevents_target_request(site):
    with pytest.raises(GhimeraRefused, match="robots_disallowed"):
        fetch(site, "/blocked")
    assert site[1]["/blocked"] == 0
    assert site[1]["/robots.txt"] == 1


def test_redirects_checked_before_network_io_and_accounted(site):
    page, budget, ledger = fetch(site, "/redirect")
    assert page.final_url.endswith("/plain")
    assert budget.fetches == 3
    assert any(row.event == "fallback" and row.reason == "redirect" for row in ledger.snapshot())
    with pytest.raises(GhimeraRefused, match="out_of_scope"):
        fetch(site, "/offscope")
    assert site[1]["/offscope"] == 1


def test_redirect_loop_is_bounded(site):
    with pytest.raises(GhimeraRefused, match="fetch_failed"):
        fetch(site, "/loop")
    assert site[1]["/loop"] <= 4


def test_only_transient_errors_retry_and_all_attempts_count(site):
    _, budget, _ = fetch(site, "/flaky")
    assert budget.fetches == 3
    assert site[1]["/flaky"] == 2
    with pytest.raises(GhimeraRefused, match="fetch_failed"):
        fetch(site, "/forbidden")
    assert site[1]["/forbidden"] == 1


@pytest.mark.parametrize(
    "path,code",
    [
        ("/challenge", "challenge_not_solved"),
        ("/login", "login_wall"),
        ("/paywall", "paywall"),
    ],
)
def test_interstitials_refuse_without_retry_or_escalation(site, path, code):
    with pytest.raises(GhimeraRefused, match=code):
        fetch(site, path)
    assert site[1][path] == 1


def test_decoded_stream_is_bounded_not_just_compressed_body(site):
    ladder, scope, budget, ledger, origin = state(site, byte_budget=4000)
    with pytest.raises(GhimeraRefused, match="budget_exhausted"):
        asyncio.run(ladder.fetch(origin + "/oversized", scope, budget, ledger))
    assert budget.bytes_read <= 4000
    assert sum(row.bytes_read for row in ledger.snapshot()) == budget.bytes_read
    assert site[1]["/oversized"] == 1


def test_conditional_304_uses_exact_prior_bytes_and_accounts_only_new_bytes(site):
    ladder, scope, budget, ledger, origin = state(site)

    async def run():
        first = await ladder.fetch(origin + "/conditional", scope, budget, ledger)
        second = await ladder.fetch(origin + "/conditional", scope, budget, ledger)
        assert first.body == second.body
        assert second.revalidated is True

    asyncio.run(run())
    assert site[1]["/robots.txt"] == 1
    assert site[1]["/conditional"] == 2
    assert ledger.snapshot()[-1].status == 304
    assert ledger.snapshot()[-1].bytes_read == 0


def test_global_and_host_limits_allow_parallel_work_with_delay(site, monkeypatch):
    ladder, scope, budget, ledger, origin = state(site)
    admitted = []
    original_slot = Politeness.slot

    @asynccontextmanager
    async def observed_slot(self, url):
        async with original_slot(self, url):
            admitted.append((url, time.monotonic()))
            yield

    monkeypatch.setattr(Politeness, "slot", observed_slot)

    async def run():
        began = time.monotonic()
        await asyncio.gather(
            *(ladder.fetch(origin + "/slow", scope, budget, ledger) for _ in range(4))
        )
        return time.monotonic() - began

    elapsed = asyncio.run(run())
    assert 0.24 <= elapsed < 0.48
    # Delay is enforced at client admission. Server arrivals can bunch or reorder
    # after socket scheduling, and are not a witness for the spacing contract.
    starts = [stamp for url, stamp in admitted if url == origin + "/slow"]
    assert len(starts) == 4
    assert all(right - left >= 0.009 for left, right in zip(starts, starts[1:], strict=False))
    assert budget.fetches == 5
    assert sum(row.bytes_read for row in ledger.snapshot()) == budget.bytes_read


def test_page_budget_includes_robots_and_stops_before_target(site):
    with pytest.raises(GhimeraRefused, match="budget_exhausted"):
        fetch(site, "/plain", page_budget=1)
    assert site[1]["/plain"] == 0


def test_public_network_refuses_private_and_mixed_dns_answers_before_io():
    policy = NetworkPolicy(schema="chimera.network/1", mode="public")
    for addresses in (("127.0.0.1",), ("169.254.169.254",), ("8.8.8.8", "10.0.0.1")):
        guard = NetworkGuard(policy, ResolverFixture(addresses))
        with pytest.raises(GhimeraRefused, match="out_of_scope"):
            asyncio.run(guard.destination("https://fixture.example/a"))
    with pytest.raises(ValidationError):
        NetworkPolicy(schema="chimera.network/1", mode="public", fixture_addresses=("127.0.0.1",))


def test_fixture_policy_cannot_permit_private_nonloopback_addresses():
    with pytest.raises(ValidationError):
        NetworkPolicy(
            schema="chimera.network/1",
            mode="loopback_fixture",
            fixture_addresses=("10.0.0.1",),
            fixture_ports=(8080,),
        )


def test_robots_toggle_requires_decision_and_records_scoped_override(site):
    _, scope, budget, ledger, origin = state(site)
    raw = budget.config.model_dump(by_alias=True)
    raw["http"]["robots"] = {
        "schema": "chimera.robots/1",
        "mode": "recorded_override",
        "product_token": "Chimera",
        "decision": {
            "decision_id": "fixture-decision",
            "decided_by": "fixture-owner",
            "reason": "Controlled test server",
            "allowed_hosts": ["fixture.example"],
        },
    }
    cfg = GhimeraConfig.model_validate(raw)
    ladder = FetchLadder((CurlRoute(cfg, resolver=ResolverFixture()),))
    budget = RunBudget(cfg, time.monotonic)
    page = asyncio.run(ladder.fetch(origin + "/blocked", scope, budget, ledger))
    assert page.status == 200
    assert site[1]["/robots.txt"] == 0
    assert site[1]["/blocked"] == 1
    assert any(
        row.event == "policy" and "fixture-decision" in row.reason for row in ledger.snapshot()
    )
    raw["http"]["robots"].pop("decision")
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)


def test_robots_wildcards_and_more_specific_allow_are_obeyed(site):
    site[4]["/robots.txt"] = b"User-agent: *\nDisallow: /private/*\nAllow: /private/public$\n"
    _, _, _ = fetch(site, "/private/public")
    with pytest.raises(GhimeraRefused, match="robots_disallowed"):
        fetch(site, "/private/secret")
    assert site[1]["/private/secret"] == 0


def test_robots_override_never_leaks_to_other_hosts_or_changed_config(site):
    ladder, scope, budget, ledger, origin = state(site)
    asyncio.run(ladder.fetch(origin + "/plain", scope, budget, ledger))
    changed = budget.config.model_dump(by_alias=True)
    changed["http"]["robots"] = {
        "schema": "chimera.robots/1",
        "mode": "recorded_override",
        "product_token": "Chimera",
        "decision": {
            "decision_id": "specific",
            "decided_by": "owner",
            "reason": "Fixture",
            "allowed_hosts": ["other.example"],
        },
    }
    cfg = GhimeraConfig.model_validate(changed)
    with pytest.raises(GhimeraRefused, match="adapter_contract"):
        asyncio.run(
            ladder.fetch(origin + "/blocked", scope, RunBudget(cfg, time.monotonic), ledger)
        )
    new_ladder = FetchLadder((CurlRoute(cfg, resolver=ResolverFixture()),))
    with pytest.raises(GhimeraRefused, match="robots_disallowed"):
        asyncio.run(
            new_ladder.fetch(origin + "/blocked", scope, RunBudget(cfg, time.monotonic), Ledger())
        )
    assert site[1]["/blocked"] == 0


def test_route_cannot_use_fixture_network_under_a_public_run_config(site):
    ladder, scope, budget, ledger, origin = state(site)
    raw = budget.config.model_dump(by_alias=True)
    raw["http"]["network"] = {"schema": "chimera.network/1", "mode": "public"}
    with pytest.raises(GhimeraRefused, match="adapter_contract"):
        asyncio.run(
            ladder.fetch(
                origin + "/plain",
                scope,
                RunBudget(GhimeraConfig.model_validate(raw), time.monotonic),
                ledger,
            )
        )
    assert not site[1]


def test_ambient_proxy_is_not_used_and_dns_is_pinned(site, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    page, _, _ = fetch(site, "/plain")
    assert page.status == 200
    assert site[1]["/plain"] == 1


def test_cancelled_fetch_releases_reservations_and_records_attempt(site):
    ladder, scope, budget, ledger, origin = state(site)

    async def run():
        task = asyncio.create_task(ladder.fetch(origin + "/slow", scope, budget, ledger))
        while site[1]["/slow"] == 0:
            await asyncio.sleep(0.002)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        page = await ladder.fetch(origin + "/plain", scope, budget, ledger)
        assert page.status == 200

    asyncio.run(run())
    assert budget.fetches == sum(row.event == "fetch" for row in ledger.snapshot())
    assert any(row.reason == "request_cancelled" for row in ledger.snapshot())


def test_partial_transfer_timeout_keeps_byte_spend_and_attempt_rows(site):
    ladder, scope, budget, ledger, origin = state(
        site, request_timeout_seconds=0.09, retry_budget=0
    )
    with pytest.raises((GhimeraRefused, TimeoutError)):
        asyncio.run(ladder.fetch(origin + "/partial", scope, budget, ledger))
    assert site[1]["/partial"] == 1
    assert budget.fetches == sum(row.event == "fetch" for row in ledger.snapshot())
    assert ledger.snapshot()[-1].bytes_read == len(b"partial")
    assert budget.bytes_read == sum(row.bytes_read for row in ledger.snapshot())
