"""Actual HTTP gateway and source requests; not a claim of vendor CAPTCHA accuracy."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.challenge_config import ChallengeConfig
from ghimera.challenges import ChallengeSessions
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.http import CurlRoute
from ghimera.journal import read_journal
from ghimera.ledger import Ledger
from ghimera.loop import GoalLoop
from ghimera.models import Goal, Harvest
from ghimera.refusals import GhimeraRefused
from tests.test_http_fetch import ResolverFixture, state

SECRET = "owned-fixture-clearance-not-a-real-credential"
AGENT = "FixtureBrowser/1.0"
CHALLENGE = b"<html><title>Just a moment...</title><div class='cf-turnstile'></div></html>"
ARTICLE = b"<article>Port infrastructure evidence</article>"


@pytest.fixture
def endpoints():
    observations, solves, controls = [], [], {}

    class Source(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            observations.append((self.path, dict(self.headers)))
            if self.path == "/robots.txt":
                status, body, mime = 200, b"User-agent: *\nDisallow: /blocked\n", "text/plain"
                if controls.get("robots_challenge") and self.headers.get("Cookie") is None:
                    status, body, mime = 403, CHALLENGE, "text/html"
            elif self.path == "/login":
                status, body, mime = (
                    200,
                    (
                        b"<form><input type='password'>Sign in to continue"
                        b"<div class='cf-turnstile'></div></form>"
                    ),
                    "text/html",
                )
            elif self.path == "/paywall":
                status, body, mime = (
                    200,
                    (b"Subscribe to continue reading <div class='cf-turnstile'></div>"),
                    "text/html",
                )
            elif self.path == "/public":
                status, body, mime = 200, ARTICLE, "text/html"
            elif controls.get("browser_challenge") and self.headers.get("Cookie") is None:
                status, body, mime = (
                    200,
                    (
                        b"<html><body>Enable JavaScript<script>"
                        b"const d=document.createElement('div');d.className='cf-'+'turnstile';"
                        b"document.body.appendChild(d);</script></body></html>"
                    ),
                    "text/html",
                )
            elif controls.get("always_challenge") or (
                self.headers.get("Cookie") != "cf_clearance=" + SECRET
                or self.headers.get("User-Agent") != AGENT
            ):
                status, body, mime = 403, CHALLENGE, "text/html"
            elif self.path == "/redirect":
                status, body, mime = 302, b"redirect", "text/html"
            else:
                status, body, mime = 200, ARTICLE, "text/html"
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            if status == 302:
                self.send_header(
                    "Location", f"http://other.example:{self.server.server_port}/public"
                )
            self.end_headers()
            self.wfile.write(body)

    class Gateway(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            solves.append((self.path, value, dict(self.headers)))
            wire = {
                "status": "ok",
                "version": controls.get("version", "fixture-1"),
                "solution": {
                    "url": controls.get("url", value["url"]),
                    "userAgent": controls.get("agent", AGENT),
                    "cookies": [
                        {
                            "name": "cf_clearance",
                            "value": SECRET,
                            "domain": controls.get("domain", "fixture.example"),
                            "path": "/",
                            "secure": False,
                            "expires": controls.get("expires", time.time() + 60),
                        },
                        {
                            "name": "login",
                            "value": "do-not-import",
                            "domain": "fixture.example",
                            "path": "/",
                            "secure": False,
                        },
                    ],
                },
            }
            body = b"x" * 200000 if controls.get("oversize") else json.dumps(wire).encode()
            self.send_response(302 if controls.get("redirect") else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if controls.get("redirect"):
                self.send_header("Location", "http://127.0.0.1:1/credential-sink")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    source = ThreadingHTTPServer(("127.0.0.1", 0), Source)
    gateway = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    workers = [
        threading.Thread(target=server.serve_forever, daemon=True) for server in (source, gateway)
    ]
    for worker in workers:
        worker.start()
    yield source.server_port, gateway.server_port, observations, solves, controls
    for server, worker in zip((source, gateway), workers, strict=True):
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def setup(endpoints, **updates):
    source_port, service_port = endpoints[:2]
    _, scope, budget, _, origin = state((source_port,))
    raw = budget.config.model_dump(by_alias=True)
    policy = dict(
        schema="ghimera.challenges/1",
        provider="flaresolverr",
        provider_version="fixture-1",
        endpoint=f"http://127.0.0.1:{service_port}/v1",
        network_boundary="operator_managed_local_browser_gateway",
        allowed_origins=(origin,),
        allowed_cookie_names=("cf_clearance",),
        max_attempts_per_run=2,
        timeout_seconds=2,
        max_request_bytes=4000,
        max_response_bytes=4000,
        max_header_bytes=4000,
        max_cookie_bytes=1000,
        session_ttl_seconds=60,
        session_cache_entries=2,
    )
    policy.update(updates)
    raw["challenges"] = policy
    cfg = GhimeraConfig.model_validate(raw)
    return cfg, FetchLadder((CurlRoute(cfg, resolver=ResolverFixture()),)), scope, origin


def test_clearance_retry_reuses_matching_agent_cookie_without_archiving_secrets(endpoints):
    cfg, ladder, scope, origin = setup(endpoints)
    budget, ledger = RunBudget(cfg, time.monotonic), Ledger()

    async def run():
        first = await ladder.fetch(origin + "/first", scope, budget, ledger)
        second = await ladder.fetch(origin + "/second", scope, budget, ledger)
        return first, second

    first, second = asyncio.run(run())
    assert first.body == second.body == ARTICLE and first.status == second.status == 200
    assert len(endpoints[3]) == budget.challenge_attempts == 1
    assert budget.fetches == len(endpoints[2]) + len(endpoints[3]) == 5
    assert budget.bytes_read == sum(row.bytes_read for row in ledger.snapshot())
    assert endpoints[3][0][1]["returnOnlyCookies"] is True
    assert "cookies" not in endpoints[3][0][1] and "session" not in endpoints[3][0][1]
    assert "Authorization" not in endpoints[3][0][2]
    cleared = [headers for _, headers in endpoints[2] if "Cookie" in headers]
    assert all(headers["Cookie"] == "cf_clearance=" + SECRET for headers in cleared)
    assert all(headers["User-Agent"] == AGENT for headers in cleared)
    wire = first.model_dump_json() + second.model_dump_json()
    wire += "".join(row.model_dump_json() for row in ledger.snapshot())
    assert SECRET not in wire and "do-not-import" not in wire
    attempt = next(row for row in ledger.snapshot() if row.event == "challenge")
    assert attempt.challenge.cookie_names == ("cf_clearance",)
    assert attempt.challenge.policy_digest == cfg.challenges.content_digest()
    assert first.challenge_use == second.challenge_use == attempt.challenge


@pytest.mark.parametrize(
    "path,code",
    [("/login", "login_wall"), ("/paywall", "paywall"), ("/blocked", "robots_disallowed")],
)
def test_entitlement_and_robots_walls_do_not_invoke_gateway(endpoints, path, code):
    cfg, ladder, scope, origin = setup(endpoints)
    with pytest.raises(GhimeraRefused, match=code):
        asyncio.run(ladder.fetch(origin + path, scope, RunBudget(cfg, time.monotonic), Ledger()))
    assert not endpoints[3]


@pytest.mark.parametrize(
    "control",
    [
        {"version": "unapproved-version"},
        {"domain": "other.example"},
        {"url": "https://other.example/redirect"},
        {"agent": "unsafe\r\nHeader: value"},
        {"expires": 1},
        {"oversize": True},
        {"redirect": True},
    ],
)
def test_bad_gateway_results_refuse_without_retrying_or_leaking(endpoints, control):
    endpoints[4].update(control)
    cfg, ladder, scope, origin = setup(endpoints)
    ledger, budget = Ledger(), RunBudget(cfg, time.monotonic)
    with pytest.raises(GhimeraRefused, match="challenge_not_solved"):
        asyncio.run(ladder.fetch(origin + "/first", scope, budget, ledger))
    assert len(endpoints[3]) == 1
    assert len([path for path, _ in endpoints[2] if path == "/first"]) == 1
    assert ledger.snapshot()[-1].event == "challenge"
    assert ledger.snapshot()[-1].refusal is not None
    assert budget.bytes_read == sum(row.bytes_read for row in ledger.snapshot())
    assert SECRET not in "".join(row.model_dump_json() for row in ledger.snapshot())


def test_origin_and_route_configuration_do_not_silently_expand_access(endpoints):
    cfg, _, _, origin = setup(endpoints)
    policy = cfg.challenges.model_dump(by_alias=True)
    for change in (
        {"endpoint": "http://169.254.169.254:80/v1"},
        {"endpoint": "http://public.example:8191/v1"},
        {"allowed_origins": (origin + "/subpath",)},
        {"allowed_cookie_names": ("cf_clearance\r\n",)},
    ):
        with pytest.raises(ValueError):
            ChallengeConfig.model_validate(dict(policy, **change))
    raw = cfg.model_dump(by_alias=True)
    raw["http"]["network"] = {"schema": "chimera.network/1", "mode": "public"}
    with pytest.raises(ValidationError, match="HTTPS"):
        GhimeraConfig.model_validate(raw)


def test_default_refusal_remains_and_a_failed_retry_cannot_loop_forever(endpoints):
    cfg, ladder, scope, origin = setup(endpoints, max_attempts_per_run=1)
    endpoints[4]["always_challenge"] = True
    budget = RunBudget(cfg, time.monotonic)
    for path in ("/one", "/two"):
        with pytest.raises(GhimeraRefused, match="challenge_not_solved"):
            asyncio.run(ladder.fetch(origin + path, scope, budget, Ledger()))
    assert len(endpoints[3]) == 1 and budget.challenge_attempts == 1
    raw = cfg.model_dump(by_alias=True)
    raw.pop("challenges")
    disabled = GhimeraConfig.model_validate(raw)
    route = FetchLadder((CurlRoute(disabled, resolver=ResolverFixture()),))
    with pytest.raises(GhimeraRefused, match="challenge_not_solved"):
        asyncio.run(
            route.fetch(origin + "/disabled", scope, RunBudget(disabled, time.monotonic), Ledger())
        )
    assert len(endpoints[3]) == 1


def test_completed_harvest_and_durable_journal_reconcile_gateway_spend(endpoints, tmp_path):
    cfg, ladder, scope, origin = setup(endpoints)
    raw = cfg.model_dump(by_alias=True)
    raw["journal"] = dict(
        schema="chimera.run-journal-config/1",
        directory=str(tmp_path / "runs"),
        max_record_bytes=1000000,
        max_journal_bytes=10000000,
        max_summary_bytes=1000000,
        max_records=1000,
    )
    cfg = GhimeraConfig.model_validate(raw)
    ladder = FetchLadder((CurlRoute(cfg, resolver=ResolverFixture()),))
    loop = GoalLoop(
        config=cfg,
        fetcher=ladder,
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    result = asyncio.run(
        loop.run(Goal(text="ports", seeds=(origin + "/first",)), scope, run_id="clearance")
    )
    assert result.documents and result.documents[0].raw == ARTICLE
    assert Harvest.model_validate_json(result.model_dump_json()) == result
    report = read_journal(cfg.journal, "clearance")
    assert report.state == "complete" and report.rows == result.ledger
    assert report.summary.receipt.fetches == result.receipt.fetches
    assert SECRET not in result.model_dump_json()
    assert result.documents[0].challenge_use is not None
    forged = result.model_dump(by_alias=True)
    forged["documents"][0]["challenge_use"]["origin"] = "https://other.example"
    with pytest.raises(ValidationError, match="clearance evidence"):
        Harvest.model_validate(forged)


def test_clearance_does_not_follow_a_cross_origin_redirect(endpoints):
    cfg, ladder, scope, origin = setup(endpoints)
    scope = type(scope).model_validate(
        dict(scope.model_dump(), allowed_hosts=("fixture.example", "other.example"))
    )
    page = asyncio.run(
        ladder.fetch(origin + "/redirect", scope, RunBudget(cfg, time.monotonic), Ledger())
    )
    assert page.body == ARTICLE and page.challenge_use is None
    headers = next(headers for path, headers in endpoints[2] if path == "/public")
    assert "Cookie" not in headers and headers["User-Agent"] != AGENT


def test_cache_expiry_does_not_send_stale_clearance(endpoints, monkeypatch):
    cfg, _, _, origin = setup(endpoints)
    sessions = ChallengeSessions(cfg.challenges)
    asyncio.run(sessions.resolve(origin + "/first", timeout_seconds=1, max_bytes=4000))
    selected = sessions.select(origin + "/next")
    assert selected is not None
    monkeypatch.setattr("ghimera.challenges.time.time", lambda: selected.evidence.expires_at + 1)
    assert sessions.select(origin + "/next") is None


def test_challenged_robots_file_can_be_read_without_overriding_its_disallow(endpoints):
    endpoints[4]["robots_challenge"] = True
    cfg, ladder, scope, origin = setup(endpoints)
    with pytest.raises(GhimeraRefused, match="robots_disallowed"):
        asyncio.run(
            ladder.fetch(origin + "/blocked", scope, RunBudget(cfg, time.monotonic), Ledger())
        )
    assert len(endpoints[3]) == 1
    assert endpoints[3][0][1]["url"] == origin + "/robots.txt"
    assert not any(path == "/blocked" for path, _ in endpoints[2])


def test_javascript_created_challenge_gets_one_recovery_then_native_refetch(endpoints, tmp_path):
    from ghimera.browser import IsolatedBrowserRenderer
    from tests.test_browser_render import browser_policy

    endpoints[4]["browser_challenge"] = True
    cfg, _, scope, origin = setup(endpoints)
    raw = cfg.model_dump(by_alias=True)
    raw["browser"] = browser_policy(tmp_path, ready_selector=None).model_dump(by_alias=True)
    cfg = GhimeraConfig.model_validate(raw)
    ladder = FetchLadder(
        (CurlRoute(cfg, resolver=ResolverFixture()),), renderer=IsolatedBrowserRenderer(cfg)
    )
    ledger, budget = Ledger(), RunBudget(cfg, time.monotonic)
    page = asyncio.run(ladder.fetch(origin + "/first", scope, budget, ledger))
    assert page.body == ARTICLE and page.challenge_use is not None
    assert len(endpoints[3]) == 1
    assert next(row for row in ledger.snapshot() if row.event == "render").refusal is not None
