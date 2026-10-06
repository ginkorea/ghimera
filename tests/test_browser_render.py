"""Browser contracts and real isolated Chromium rendering, not a mocked DOM."""

import asyncio
import hashlib
import os
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.browser import IsolatedBrowserRenderer
from ghimera.browser_config import BrowserConfig
from ghimera.browser_types import RenderResource
from ghimera.config import GhimeraConfig
from ghimera.models import Page, Scope
from ghimera.refusals import GhimeraRefused, RefusalCode


def browser_policy(tmp_path, **updates):
    executable = Path(os.environ["CHIMERA_TEST_BROWSER"])
    with executable.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    data = dict(
        schema="chimera.browser/1",
        engine="patchright",
        executable=str(executable),
        executable_sha256=digest,
        isolator=os.environ["CHIMERA_TEST_ISOLATOR"],
        work_directory=str(tmp_path / "browser"),
        sandbox_work_directory="/run/chimera",
        max_workers=1,
        timeout_seconds=20.0,
        cleanup_timeout_seconds=5.0,
        max_input_bytes=100_000,
        max_rendered_bytes=100_000,
        max_protocol_bytes=500_000,
        max_diagnostic_bytes=20_000,
        max_resources=10,
        resource_types=["script", "fetch", "xhr", "stylesheet"],
        resource_content_types=["application/javascript", "application/json", "text/css"],
        ready_selector="article[data-ready]",
        settle_seconds=0.05,
        viewport_width=1000,
        viewport_height=800,
    )
    data.update(updates)
    return BrowserConfig.model_validate(data)


def source(html):
    return Page(
        url="https://example.org/story",
        final_url="https://example.org/story",
        status=200,
        content_type="text/html",
        body=html.encode(),
    )


class Resources:
    def __init__(self, pages=()):
        self.pages = {page.url: page for page in pages}
        self.calls = []

    async def fetch(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        return self.pages[url]


def render(tmp_path, html, resources=None, **updates):
    policy = browser_policy(tmp_path, **updates)
    config = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    config["browser"] = policy.model_dump(by_alias=True)
    renderer = IsolatedBrowserRenderer(GhimeraConfig.model_validate(config))
    result = asyncio.run(
        renderer.render(
            source(html),
            Scope(allowed_hosts=("example.org",), max_depth=1, content_types=("text/html",)),
            resources or Resources(),
            timeout_seconds=20.0,
        )
    )
    return result, policy


def test_contract_test_first_requires_browser_implementation():
    assert IsolatedBrowserRenderer.name == "patchright_isolated"


def test_real_js_changes_dom_without_replacing_source(tmp_path):
    html = """<html><body><article id='story'></article><script>
    document.querySelector('article').textContent='Native maritime evidence';
    document.querySelector('article').setAttribute('data-ready','yes');
    </script></body></html>"""
    result, policy = render(tmp_path, html)
    assert result.source_sha256 == hashlib.sha256(html.encode()).hexdigest()
    assert b"Native maritime evidence" in result.html
    assert result.html != html.encode()
    assert result.config_digest == policy.content_digest()
    assert result.network_isolation == "linux_network_namespace"
    assert result.parent_network_namespace != result.worker_network_namespace
    # CDP also observes Chromium's implicit favicon request. It is refused
    # before parent I/O rather than silently omitted from resource evidence.
    assert all(
        item.url.endswith("/favicon.ico") and item.refusal == RefusalCode.OUT_OF_SCOPE
        for item in result.resources
    )
    assert not list((tmp_path / "browser").iterdir())
    result.validate_policy(policy)
    changed = result.model_copy(update={"browser_sha256": "0" * 64})
    with pytest.raises(ValueError):
        changed.validate_policy(policy)


def test_real_browser_subrequests_are_parent_fetched_and_retained(tmp_path):
    script = Page(
        url="https://example.org/app.js",
        final_url="https://example.org/app.js",
        status=200,
        content_type="application/javascript",
        body=b"fetch('/data').then(r=>r.json()).then(r=>{document.querySelector('article').textContent=r.text;document.querySelector('article').setAttribute('data-ready','yes')})",
    )
    data = Page(
        url="https://example.org/data",
        final_url="https://example.org/data",
        status=200,
        content_type="application/json",
        body=b'{"text":"Original port report"}',
    )
    resources = Resources((script, data))
    result, _ = render(
        tmp_path,
        "<article></article><script src='/app.js'></script>",
        resources,
    )
    assert resources.calls == [script.url, data.url]
    assert b"Original port report" in result.html
    assert [item.source_sha256 for item in result.resources if item.refusal is None] == [
        hashlib.sha256(script.body).hexdigest(),
        hashlib.sha256(data.body).hexdigest(),
    ]
    assert [item for item in result.resources if item.refusal is None][1].body == data.body


def test_offscope_browser_subrequest_is_not_sent_to_parent(tmp_path):
    resources = Resources()
    result, _ = render(
        tmp_path,
        """<article data-ready='yes'>Report</article>
    <script>fetch('https://foreign.example/private').catch(()=>{});</script>""",
        resources,
    )
    assert resources.calls == []
    assert result.resources[0].refusal == RefusalCode.OUT_OF_SCOPE


def test_post_and_websocket_are_never_sent_as_http_get(tmp_path):
    resources = Resources()
    result, _ = render(
        tmp_path,
        """<article data-ready='yes'>Report</article><script>
    fetch('/mutate',{method:'POST',body:'x'}).catch(()=>{});
    new WebSocket('wss://example.org/live');</script>""",
        resources,
    )
    assert resources.calls == []
    assert {
        item.resource_type for item in result.resources if not item.url.endswith("/favicon.ico")
    } == {"fetch", "websocket"}
    assert all(item.refusal == RefusalCode.OUT_OF_SCOPE for item in result.resources)


def test_missing_or_unpinned_executable_refuses_before_launch(tmp_path):
    data = browser_policy(tmp_path).model_dump(by_alias=True)
    data["executable_sha256"] = "0" * 64
    cfg = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    cfg["browser"] = data
    with pytest.raises(GhimeraRefused, match="adapter_contract"):
        IsolatedBrowserRenderer(GhimeraConfig.model_validate(cfg))
    assert not (tmp_path / "browser").exists()


def test_render_timeout_cleans_its_own_process_tree_and_next_call_runs(tmp_path):
    started = time.monotonic()
    with pytest.raises(GhimeraRefused, match="budget_exhausted"):
        render(tmp_path, "<article>Never ready</article>", timeout_seconds=0.8)
    assert time.monotonic() - started < 10
    assert not list((tmp_path / "browser").iterdir())
    result, _ = render(tmp_path, "<article data-ready='yes'>Ready</article>")
    assert b"Ready" in result.html


def test_resource_contract_binds_retained_bytes_and_one_outcome():
    with pytest.raises(ValidationError):
        RenderResource(
            url="https://example.org/app.js",
            method="GET",
            resource_type="script",
            status=200,
            content_type="application/javascript",
            body=b"script",
            source_sha256="0" * 64,
        )


def test_ladder_renders_only_js_required_and_budgets_real_subresources(tmp_path):
    from test_http_fetch import ResolverFixture, state
    from test_http_fetch import site as fixture_site

    from ghimera.budget import RunBudget
    from ghimera.fetch import FetchLadder
    from ghimera.http import CurlRoute
    from ghimera.ledger import Ledger

    # Reuse the real C1 server and exact loopback-network exception, not a second
    # HTTP implementation or a browser profile that can reach localhost directly.
    fixture = fixture_site.__wrapped__()
    site = next(fixture)
    try:
        _, scope, previous, _, origin = state(site)
        site[4]["/plain"] = b"""<title>Port report</title><article></article>
        <noscript>enable javascript</noscript><script>
        fetch('/robots.txt').then(r=>r.text()).then(t=>{
            document.querySelector('article').textContent=t;
            document.querySelector('article').setAttribute('data-ready','yes');
        });</script>"""
        cfg = previous.config.model_dump(by_alias=True)
        cfg["browser"] = browser_policy(
            tmp_path,
            resource_content_types=["text/plain"],
        ).model_dump(by_alias=True)
        config = GhimeraConfig.model_validate(cfg)
        route = CurlRoute(config, resolver=ResolverFixture())
        ladder = FetchLadder((route,), renderer=IsolatedBrowserRenderer(config))
        budget, ledger = RunBudget(config, time.monotonic), Ledger()
        page = asyncio.run(ladder.fetch(origin + "/plain", scope, budget, ledger))
        assert page.body == site[4]["/plain"]
        assert page.rendered is not None
        assert page.rendered.resources[0].url == origin + "/robots.txt"
        assert budget.fetches == 3  # robots + initial HTML + one browser subsidiary request
        assert budget.bytes_read == sum(row.bytes_read for row in ledger.snapshot())
        assert site[1]["/plain"] == 1
        assert site[1]["/robots.txt"] == 2
        assert ledger.snapshot()[-1].event == "render"
        assert not any("Authorization" in headers or "Cookie" in headers for _, headers in site[3])
    finally:
        fixture.close()


@pytest.mark.parametrize("dark", [False, True])
def test_browser_open_web_and_onion_subresources_follow_tor_without_local_dns(tmp_path, dark):
    from test_http_fetch import site as fixture_site
    from test_http_fetch import state
    from test_tor_transport import NoLocalDNS, onion, policy, socks_server

    from ghimera.budget import RunBudget
    from ghimera.fetch import FetchLadder
    from ghimera.http import CurlRoute
    from ghimera.ledger import Ledger

    fixture = fixture_site.__wrapped__()
    site = next(fixture)
    seen = []
    site[4]["/plain"] = b"""<article></article><noscript>enable javascript</noscript>
    <script>fetch('/robots.txt').then(r=>r.text()).then(t=>{
      document.querySelector('article').textContent=t;
      document.querySelector('article').setAttribute('data-ready','yes');
    });</script>"""

    async def exercise():
        server = await socks_server(site[0], seen)
        async with server:
            data = state(site)[2].config.model_dump(by_alias=True)
            transport = policy(server.sockets[0].getsockname()[1]).model_dump(by_alias=True)
            transport["tor"]["allowed_ports"] = [site[0]]
            data["transport"] = transport
            data["browser"] = browser_policy(
                tmp_path,
                resource_content_types=["text/plain"],
            ).model_dump(by_alias=True)
            config = GhimeraConfig.model_validate(data)
            host = onion() if dark else "fixture.example"
            scope = Scope(
                allowed_hosts=(host,),
                allowed_ports=(site[0],),
                max_depth=0,
                content_types=("text/html",),
            )
            ladder = FetchLadder(
                (CurlRoute(config, resolver=NoLocalDNS()),),
                renderer=IsolatedBrowserRenderer(config),
            )
            budget, ledger = RunBudget(config, time.monotonic), Ledger()
            page = await ladder.fetch(f"http://{host}:{site[0]}/plain", scope, budget, ledger)
            assert page.transport.mode == "tor"
            assert page.rendered.resources[0].transport.mode == "tor"
            assert page.rendered.resources[0].transport.network_class == (
                "onion" if dark else "open_web"
            )
            assert budget.fetches == 3
            assert all(
                row.transport.mode == "tor" for row in ledger.snapshot() if row.event == "fetch"
            )
        await asyncio.sleep(0.01)

    try:
        asyncio.run(exercise())
        assert len([row for row in seen if row[0] == 1]) == 3
        assert len([row for row in seen if row[0] == 0xF0]) == (0 if dark else 3)
    finally:
        fixture.close()


def test_rendered_html_extraction_and_harvest_bind_raw_dom_and_resources(tmp_path):
    from test_html_extraction import policy as extraction_policy

    from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
    from ghimera.extraction import HtmlExtractor
    from ghimera.fetch import FetchLadder
    from ghimera.loop import GoalLoop
    from ghimera.models import Goal, Harvest

    html = """<title>Maritime infrastructure report</title><article></article>
    <script>document.querySelector('article').innerHTML='<h1>Maritime infrastructure report</h1>'+
    '<p>The port authority published a report about infrastructure investment. The document '+
    'describes maritime transport and construction of a new terminal.</p>';
    document.querySelector('article').setAttribute('data-ready','yes');</script>"""
    data = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    data["browser"] = browser_policy(tmp_path).model_dump(by_alias=True)
    data["extraction"] = extraction_policy(tmp_path / "parse").model_dump(by_alias=True)
    data.update(page_budget=1)
    config = GhimeraConfig.model_validate(data)

    class JsRequired(FakeRoute):
        async def attempt(self, request):
            return source(html)

        def escalation_reason(self, page):
            return (
                None
                if b"data-ready" in page.body.split(b"<script>", 1)[0]
                else "javascript_required"
            )

    route = JsRequired()
    result = asyncio.run(
        GoalLoop(
            config=config,
            fetcher=FetchLadder((route,), renderer=IsolatedBrowserRenderer(config)),
            extractor=HtmlExtractor(config),
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ).run(
            Goal(text="ports", seeds=("https://example.org/story",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
    )
    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.raw == html.encode()
    assert document.rendered.html != document.raw
    assert "maritime transport" in document.extracted.text
    assert document.extracted.extraction.source_sha256 == document.sha256
    assert document.extracted.extraction.rendered_sha256 == document.rendered.html_sha256
    assert Harvest.model_validate_json(result.model_dump_json()) == result
    changed = result.model_dump(mode="json", by_alias=True)
    changed["documents"][0]["extracted"]["extraction"]["rendered_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        Harvest.model_validate(changed)


def test_source_csp_is_preserved(tmp_path):
    original = source("""<article data-ready='yes'>Original</article><script>
    document.querySelector('article').textContent='Should not run';</script>""")
    original = original.model_copy(
        update={
            "headers": (
                ("content-security-policy", "default-src 'none'; script-src 'none'"),
                ("content-security-policy", "script-src 'unsafe-inline'"),
                ("set-cookie", "secret=test-fixture-only"),
            )
        }
    )
    policy = browser_policy(tmp_path)
    data = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    data["browser"] = policy.model_dump(by_alias=True)
    rendered = asyncio.run(
        IsolatedBrowserRenderer(GhimeraConfig.model_validate(data)).render(
            original,
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
            Resources(),
            timeout_seconds=20.0,
        )
    )
    assert b">Original</article>" in rendered.html


def test_js_created_login_wall_is_terminal_after_render(tmp_path):
    from ghimera.budget import RunBudget
    from ghimera.doubles import FakeRoute
    from ghimera.fetch import FetchLadder
    from ghimera.http import page_barrier
    from ghimera.ledger import Ledger

    html = """<article data-ready='yes'></article><noscript>enable javascript</noscript><script>
    const input=document.createElement('input');input.type='pass'+'word';
    document.querySelector('article').append(input);
    document.querySelector('article').append('Sign in'+' to continue');</script>"""

    class DetectedSource(FakeRoute):
        async def attempt(self, request):
            return source(html)

        def escalation_reason(self, page):
            barrier = page_barrier(page)
            if barrier is not None:
                raise GhimeraRefused(barrier)
            return "javascript_required"

    data = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    data["browser"] = browser_policy(tmp_path).model_dump(by_alias=True)
    config = GhimeraConfig.model_validate(data)
    ladder = FetchLadder((DetectedSource(),), renderer=IsolatedBrowserRenderer(config))
    ledger = Ledger()
    with pytest.raises(GhimeraRefused, match="login_wall"):
        asyncio.run(
            ladder.fetch(
                "https://example.org/story",
                Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
                RunBudget(config, time.monotonic),
                ledger,
            )
        )
    assert ledger.snapshot()[-1].event == "render"
    assert ledger.snapshot()[-1].refusal == RefusalCode.LOGIN_WALL


def test_resource_limit_terminates_instead_of_reporting_partial_render_complete(tmp_path):
    data = Page(
        url="https://example.org/one",
        final_url="https://example.org/one",
        status=200,
        content_type="application/json",
        body=b"{}",
    )
    resources = Resources((data,))
    with pytest.raises(GhimeraRefused, match="budget_exhausted"):
        render(
            tmp_path,
            """<article data-ready='yes'>Report</article><script>
          fetch('/one').then(()=>fetch('/two'));</script>""",
            resources,
            max_resources=1,
        )
    assert resources.calls == [data.url]
    assert not list((tmp_path / "browser").iterdir())


def test_cancel_reaps_browser_session_and_releases_capacity(tmp_path, monkeypatch):
    create = asyncio.create_subprocess_exec
    children = []

    async def capture(*args, **kwargs):
        process = await create(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    data = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    data["browser"] = browser_policy(tmp_path).model_dump(by_alias=True)
    renderer = IsolatedBrowserRenderer(GhimeraConfig.model_validate(data))
    scope = Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",))

    async def exercise():
        task = asyncio.create_task(
            renderer.render(
                source("<article>Not ready</article>"),
                scope,
                Resources(),
                timeout_seconds=20.0,
            )
        )
        while not children:
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None
        assert not list((tmp_path / "browser").iterdir())
        result = await renderer.render(
            source("<article data-ready='yes'>Ready</article>"),
            scope,
            Resources(),
            timeout_seconds=20.0,
        )
        assert b"Ready" in result.html

    asyncio.run(exercise())
