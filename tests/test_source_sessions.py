"""Authorized collection uses explicit exact-origin secrets, never bypass logic."""

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import SecretStr, ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.http import CurlRoute
from ghimera.ledger import Ledger
from ghimera.loop import GoalLoop
from ghimera.models import Goal, Harvest, Scope
from ghimera.refusals import GhimeraRefused
from ghimera.source_session_types import SourceSessionPolicy
from ghimera.source_sessions import SourceCredentials, SourceSessions
from tests.test_http_fetch import ResolverFixture
from tests.test_http_fetch import state as http_state

AUTH = "Bearer owned-fixture-credential"
COOKIE = "fixture_session=owned-fixture-cookie"


@pytest.fixture
def site():
    observations = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            observations.append(
                (
                    self.headers["Host"],
                    self.path,
                    {key.lower(): value for key, value in self.headers.items()},
                )
            )
            status, headers = 200, {}
            if self.path == "/robots.txt":
                body, kind = b"User-agent: *\nAllow: /\n", "text/plain"
            elif self.path.startswith("/search?"):
                body, kind = b'{"results":[]}', "application/json"
            elif self.path.startswith("/private/"):
                kind = "text/html"
                if (
                    self.headers.get("Authorization") != AUTH
                    or self.headers.get("Cookie") != COOKIE
                ):
                    status, body = 401, b"Please authenticate"
                elif self.path == "/private/cross":
                    status, body = 302, b"redirect"
                    headers["Location"] = f"http://other.example:{self.server.server_port}/public"
                elif self.path == "/private/outside":
                    status, body = 302, b"redirect"
                    headers["Location"] = "/public"
                elif self.path == "/private/browser":
                    body = (
                        b"<html><body>Enable JavaScript<article>Loading ports</article>"
                        b'<script src="/private/script.js"></script></body></html>'
                    )
                elif self.path == "/private/script.js":
                    kind = "application/javascript"
                    body = (
                        b"const a=document.querySelector('article');"
                        b"a.textContent='Authorized port infrastructure';a.dataset.ready='true';"
                    )
                else:
                    body = b"<article>Authorized port infrastructure report</article>"
            else:
                body, kind = b"<article>Public report</article>", "text/html"
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server.server_port, observations
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def policy(origin="https://publisher.example", **updates):
    raw = dict(
        schema="chimera.source-session/1",
        session_id="publisher-session",
        origin=origin,
        path_prefixes=("/private/",),
        header_names=("authorization", "cookie"),
        allow_http=False,
    )
    raw.update(updates)
    return SourceSessionPolicy.model_validate(raw)


def credentials(*, authorization=AUTH):
    return SourceCredentials(
        headers=(("authorization", SecretStr(authorization)), ("cookie", SecretStr(COOKIE)))
    )


def setup(site, *, authorization=AUTH):
    port = site[0]
    _, _, budget, _, origin = http_state((port,))
    raw = budget.config.model_dump(by_alias=True)
    raw["source_sessions"] = (policy(origin, allow_http=True).model_dump(by_alias=True),)
    cfg = GhimeraConfig.model_validate(raw)
    route = CurlRoute(
        cfg,
        resolver=ResolverFixture(),
        source_credentials={"publisher-session": credentials(authorization=authorization)},
    )
    scope = Scope(
        allowed_hosts=("fixture.example", "other.example"),
        allowed_ports=(port,),
        max_depth=0,
        content_types=("text/html",),
    )
    return cfg, FetchLadder((route,)), scope, origin


def test_supplied_session_collects_entitled_content_and_records_only_nonsecret_policy(site):
    cfg, ladder, scope, origin = setup(site)
    budget, ledger = RunBudget(cfg, time.monotonic), Ledger()
    page = asyncio.run(ladder.fetch(origin + "/private/report", scope, budget, ledger))
    assert page.status == 200 and b"Authorized" in page.body
    assert page.source_session.session_id == "publisher-session"
    assert [item[1] for item in site[1]] == ["/robots.txt", "/private/report"]
    assert "authorization" not in site[1][0][2]
    assert site[1][1][2]["authorization"] == AUTH
    wire = page.model_dump_json() + "".join(row.model_dump_json() for row in ledger.snapshot())
    assert AUTH not in wire and COOKIE not in wire
    assert ledger.snapshot()[-1].source_session == page.source_session


@pytest.mark.parametrize("path", ["/private/cross", "/private/outside"])
def test_redirect_reselects_credentials_without_forwarding_them_outside_origin_or_path(site, path):
    cfg, ladder, scope, origin = setup(site)
    page = asyncio.run(ladder.fetch(origin + path, scope, RunBudget(cfg, time.monotonic), Ledger()))
    assert page.status == 200 and page.source_session is None
    targets = [headers for _, target, headers in site[1] if target == "/public"]
    assert len(targets) == 1
    assert "authorization" not in targets[0] and "cookie" not in targets[0]


def test_expired_session_is_a_recorded_refusal_not_an_authentication_bypass(site):
    cfg, ladder, scope, origin = setup(site, authorization="Bearer expired-owned-fixture")
    ledger = Ledger()
    with pytest.raises(GhimeraRefused, match="fetch_failed"):
        asyncio.run(
            ladder.fetch(origin + "/private/report", scope, RunBudget(cfg, time.monotonic), ledger)
        )
    assert ledger.snapshot()[-1].status == 401
    assert ledger.snapshot()[-1].source_session.session_id == "publisher-session"
    assert len([item for item in site[1] if item[1] == "/private/report"]) == 1


def test_exact_origin_and_normalized_path_prevent_credential_scope_expansion():
    sessions = SourceSessions((policy(),), {"publisher-session": credentials()})
    assert sessions.select("https://publisher.example/private/report") is not None
    for url in (
        "http://publisher.example/private/report",
        "https://publisher.example:444/private/report",
        "https://sub.publisher.example/private/report",
        "https://publisher.example/public",
        "https://publisher.example/private/../public",
        "https://publisher.example/private/%2e%2e/public",
        "https://publisher.example/private/%2Fpublic",
        "https://publisher.example/private/%5cpublic",
        "https://user@publisher.example/private/report",
    ):
        assert sessions.select(url) is None


def test_missing_and_extra_credentials_refuse_before_source_io(site):
    cfg, _, _, _ = setup(site)
    for supplied in (None, {}, {"unconfigured": credentials()}):
        with pytest.raises(GhimeraRefused, match="source_session_unavailable"):
            CurlRoute(cfg, resolver=ResolverFixture(), source_credentials=supplied)
    assert not site[1]


def test_session_configuration_contains_no_secrets_and_rejects_ambiguous_scope():
    from tests.test_c0 import config

    for updates in (
        dict(origin="https://user:password@publisher.example"),
        dict(origin="https://publisher.example/private"),
        dict(origin="http://publisher.example"),
        dict(origin="https://publisher.example:0"),
        dict(origin="https://*.publisher.example"),
        dict(path_prefixes=("/private/../",)),
        dict(header_names=("host",)),
        dict(header_names=("authorization", "authorization")),
        dict(secret="never-in-config"),
    ):
        with pytest.raises(ValidationError):
            policy(**updates)
    with pytest.raises(ValidationError):
        config(source_sessions=(policy(), policy()))
    secrets = credentials()
    assert AUTH not in repr(secrets) and COOKIE not in secrets.model_dump_json()


def test_session_config_allows_distinct_path_credentials_but_not_overlaps():
    from tests.test_c0 import config

    first = policy(path_prefixes=("/private/a/",))
    second = policy(session_id="second-session", path_prefixes=("/private/b/",))
    assert len(config(source_sessions=(first, second)).source_sessions) == 2
    with pytest.raises(ValidationError, match="overlap"):
        config(source_sessions=(first, policy(session_id="overlap", path_prefixes=("/private/",))))


def test_credential_headers_must_match_policy_before_any_request():
    incomplete = SourceCredentials(headers=(("authorization", SecretStr(AUTH)),))
    with pytest.raises(GhimeraRefused, match="source_session_unavailable"):
        SourceSessions((policy(),), {"publisher-session": incomplete})


def test_source_credentials_do_not_bind_or_leak_to_discovery_service(site):
    from ghimera.research_types import SearchQuery, SearchRequest
    from ghimera.searxng import SearxConfig, SearxSearch

    cfg, _, _, origin = setup(site)
    provider = SearxConfig(
        schema="chimera.searxng/1",
        endpoint=origin + "/search",
        language="en",
        safe_search=0,
        time_range="",
    )
    search = SearxSearch(cfg, provider, resolver=ResolverFixture())
    response = asyncio.run(
        search.request(
            SearchRequest(
                query=SearchQuery(text="ports", question_ids=("q1",)),
                limit=3,
                max_bytes=1000,
                timeout_seconds=5.0,
            )
        )
    )
    assert response.hits == () and len(site[1]) == 1
    assert "authorization" not in site[1][0][2] and "cookie" not in site[1][0][2]


def test_isolated_browser_resources_use_parent_owned_authorized_session(site, tmp_path):
    from ghimera.browser import IsolatedBrowserRenderer
    from tests.test_browser_render import browser_policy

    cfg, _, scope, origin = setup(site)
    raw = cfg.model_dump(by_alias=True)
    raw["browser"] = browser_policy(tmp_path).model_dump(by_alias=True)
    cfg = GhimeraConfig.model_validate(raw)
    route = CurlRoute(
        cfg,
        resolver=ResolverFixture(),
        source_credentials={"publisher-session": credentials()},
    )
    ladder = FetchLadder((route,), renderer=IsolatedBrowserRenderer(cfg))
    page = asyncio.run(
        ladder.fetch(origin + "/private/browser", scope, RunBudget(cfg, time.monotonic), Ledger())
    )
    assert page.rendered is not None and b"Authorized port infrastructure" in page.rendered.html
    assert b"Loading ports" in page.body
    script = next(
        item for item in page.rendered.resources if item.url.endswith("/private/script.js")
    )
    assert (
        script.source_session is not None
        and script.source_session.session_id == "publisher-session"
    )
    assert AUTH not in page.model_dump_json() and COOKIE not in page.model_dump_json()


def test_header_injection_refuses_without_exposing_the_secret():
    secret = "private-credential\r\nInjected: bad"
    with pytest.raises(ValidationError) as failure:
        SourceCredentials(headers=(("authorization", SecretStr(secret)),))
    assert secret not in str(failure.value) and "private-credential" not in str(failure.value)


def test_harvest_reader_binds_session_metadata_to_effective_policy(site):
    cfg, ladder, scope, origin = setup(site)
    loop = GoalLoop(
        config=cfg,
        fetcher=ladder,
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    harvest = asyncio.run(loop.run(Goal(text="ports", seeds=(origin + "/private/report",)), scope))
    assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
    assert harvest.documents[0].source_session is not None
    raw = harvest.model_dump(mode="json", by_alias=True)
    raw["documents"][0]["source_session"]["policy_digest"] = "0" * 64
    with pytest.raises(ValidationError, match="session"):
        Harvest.model_validate(raw)
