"""Real parsing dependencies, native text and bounded child-process contracts."""

import asyncio
import hashlib
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.extraction import HtmlExtractor
from ghimera.extraction_config import ExtractionConfig
from ghimera.models import Page
from ghimera.refusals import GhimeraRefused, RefusalCode

ARTICLE = """<!doctype html><html lang="en"><head>
<title>Port infrastructure report</title><meta name="author" content="Research Office">
<meta property="article:published_time" content="2026-10-06">
<link rel="canonical" href="/reports/ports">
</head><body><nav>Unrelated navigation and advertising</nav>
<article id="story"><h1>Port infrastructure report</h1>
<p>The port authority published a report about infrastructure investment.
The document describes maritime transport and construction of a new terminal.</p>
<p>Analysts reviewed the evidence and the report contains original source links.</p>
<table><tr><th>Location</th><th>Capacity</th></tr><tr><td>Taiwan</td><td>42</td></tr></table>
<a href="appendix.pdf">Supporting appendix</a>
<a href="javascript:alert(1)">Not a document</a>
<script>EVIL SCRIPT TEXT</script></article><footer>COOKIE FOOTER</footer></body></html>"""


def policy(tmp_path: Path, **updates: object) -> ExtractionConfig:
    with Path("examples/extraction.toml").open("rb") as stream:
        data = tomllib.load(stream)
    data.update(
        work_directory=str(tmp_path / "worker"), locator_directory=str(tmp_path / "locators")
    )
    data.update(updates)
    return ExtractionConfig.model_validate(data)


def extractor(tmp_path: Path, **updates: object) -> HtmlExtractor:
    data = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    data["extraction"] = policy(tmp_path, **updates).model_dump(by_alias=True)
    return HtmlExtractor(GhimeraConfig.model_validate(data))


def page(html: str = ARTICLE, **updates: object) -> Page:
    data: dict[str, object] = dict(
        url="https://example.org/reports/ports",
        final_url="https://example.org/reports/ports",
        status=200,
        content_type="text/html",
        body=html.encode(),
    )
    data.update(updates)
    return Page.model_validate(data)


def test_real_html_retains_native_text_metadata_tables_and_safe_document_links(tmp_path):
    parsed = asyncio.run(extractor(tmp_path).extract(page()))
    assert parsed.title == "Port infrastructure report"
    assert parsed.language == "en"
    assert "infrastructure investment" in parsed.text
    assert "Taiwan" in parsed.text and "42" in parsed.text
    assert "EVIL SCRIPT" not in parsed.text and "COOKIE FOOTER" not in parsed.text
    assert "Unrelated navigation" not in parsed.text
    assert parsed.byline == "Research Office" and parsed.date == "2026-10-06"
    assert parsed.canonical_url == "https://example.org/reports/ports"
    assert [link.url for link in parsed.links] == ["https://example.org/reports/appendix.pdf"]
    record = parsed.extraction
    assert record is not None
    assert record.source_sha256 == hashlib.sha256(page().body).hexdigest()
    assert record.text_sha256 == hashlib.sha256(parsed.text.encode()).hexdigest()
    assert record.config_digest == policy(tmp_path).content_digest()
    assert record.parser_revision == "scrapling@0.4.2+crawl4ai@0.9.4+lingua@2.1.1"
    assert not {"crawl4ai", "openai", "torch", "transformers"} & set(sys.modules)


def test_chinese_is_native_and_language_hint_is_not_trusted(tmp_path):
    html = """<html lang="en"><head><title>臺灣港口建設</title></head><body><article>
    <h1>臺灣港口建設</h1><p>臺灣港務公司公布高雄港碼頭建設與海運基礎設施投資計畫。
    研究報告說明港口貨物運輸、航道安全和新碼頭工程，並提供原始資料來源。</p>
    <p>地方政府與研究人員檢視相關證據，交通部將持續公布工程進度。</p>
    </article></body></html>"""
    parsed = asyncio.run(extractor(tmp_path).extract(page(html)))
    assert parsed.language == "zh"
    assert "高雄港" in parsed.text and "infrastructure" not in parsed.text
    assert parsed.extraction.declared_language == "en"
    assert parsed.extraction.language_hint_disagrees


def test_saved_locator_relocates_after_redesign_without_other_host_contamination(tmp_path):
    profiles = [dict(host="example.org", profile_id="news-v1", body="#story", title="h1")]
    client = extractor(tmp_path, profiles=profiles)
    first = asyncio.run(client.extract(page()))
    second = asyncio.run(client.extract(page(ARTICLE.replace('id="story"', 'id="renamed"'))))
    assert first.text == second.text
    assert second.extraction.selection == "relocated"
    assert any(
        row.field == "body" and row.status == "relocated" for row in second.extraction.locators
    )
    foreign = asyncio.run(client.extract(page(ARTICLE, final_url="https://other.example/news")))
    assert foreign.extraction.selection == "generic"
    assert foreign.extraction.profile_id is None


def test_missing_profile_body_has_recorded_generic_fallback(tmp_path):
    client = extractor(
        tmp_path, profiles=[dict(host="example.org", profile_id="missing", body="#absent")]
    )
    parsed = asyncio.run(client.extract(page()))
    assert parsed.extraction.selection == "generic"
    assert parsed.extraction.missing_fields == ("body",)


def test_invalid_config_and_mismatched_content_refuse_before_worker(tmp_path):
    with pytest.raises(ValidationError):
        policy(tmp_path, max_output_bytes=0)
    with pytest.raises(ValidationError):
        policy(tmp_path, profiles=[dict(host="example.org", profile_id="bad", body="")])
    with pytest.raises(ValidationError):
        policy(tmp_path, languages=["bogus"])
    client = extractor(tmp_path)
    with pytest.raises(GhimeraRefused) as exc:
        asyncio.run(client.extract(page(content_type="application/pdf")))
    assert exc.value.code == RefusalCode.CONTENT_TYPE_UNWANTED
    assert not (tmp_path / "worker").exists()


def test_limits_do_not_turn_truncated_or_empty_extraction_into_evidence(tmp_path):
    for updates, html in [
        ({"max_input_bytes": 10}, ARTICLE),
        ({"max_text_chars": 10}, ARTICLE),
        ({}, "<html><title>Empty</title><body><script>only script</script></body></html>"),
    ]:
        with pytest.raises(GhimeraRefused):
            asyncio.run(extractor(tmp_path, **updates).extract(page(html)))


def test_worker_timeout_is_terminal_and_releases_its_capacity(tmp_path):
    client = extractor(tmp_path, timeout_seconds=0.001)
    with pytest.raises(GhimeraRefused) as exc:
        asyncio.run(client.extract(page()))
    assert exc.value.code == RefusalCode.BUDGET_EXHAUSTED


def test_real_extraction_links_work_in_collection_and_roundtrip(tmp_path):
    from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
    from ghimera.fetch import FetchLadder
    from ghimera.loop import GoalLoop
    from ghimera.models import Goal, Harvest, Scope

    class HtmlRoute(FakeRoute):
        async def attempt(self, request):
            return page()

    client = extractor(tmp_path)
    data = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    data.update(page_budget=1, extraction=client.config.model_dump(by_alias=True))
    result = asyncio.run(
        GoalLoop(
            config=GhimeraConfig.model_validate(data),
            fetcher=FetchLadder((HtmlRoute(),)),
            extractor=client,
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ).run(
            Goal(text="ports", seeds=(page().url,)),
            Scope(allowed_hosts=("example.org",), max_depth=1, content_types=("text/html",)),
        )
    )
    assert len(result.documents) == 1
    assert result.documents[0].raw == page().body
    assert result.documents[0].extracted.extraction is not None
    assert Harvest.model_validate_json(result.model_dump_json()) == result
    tampered = result.model_dump(mode="json", by_alias=True)
    tampered["documents"][0]["extracted"]["text"] += " invented statement"
    with pytest.raises(ValidationError):
        Harvest.model_validate(tampered)


def test_worker_cancellation_reaps_child_and_next_call_can_run(tmp_path, monkeypatch):
    children = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await create(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)

    async def exercise():
        client = extractor(tmp_path, max_workers=1)
        task = asyncio.create_task(client.extract(page()))
        while not children:
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None
        assert (await client.extract(page())).language == "en"
        assert children[1].returncode == 0

    asyncio.run(exercise())


def test_passive_worker_refuses_network_and_subprocess_attempts():
    from ghimera.html_worker import no_network

    for event in (
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.sendto",
        "subprocess.Popen",
        "os.system",
    ):
        with pytest.raises(PermissionError):
            no_network(event, ())
    no_network("open", ())


def test_extractor_binding_refuses_before_collection(tmp_path):
    client = extractor(tmp_path)
    with pytest.raises(GhimeraRefused) as exc:
        client.validate_config(GhimeraConfig.from_toml(Path("examples/chimera.toml")))
    assert exc.value.code == RefusalCode.ADAPTER_CONTRACT


def test_shared_state_directory_is_not_reused(tmp_path):
    shared = tmp_path / "worker"
    shared.mkdir(mode=0o755)
    with pytest.raises(GhimeraRefused) as exc:
        asyncio.run(extractor(tmp_path).extract(page()))
    assert exc.value.code == RefusalCode.ADAPTER_CONTRACT


def test_bounded_worker_reader_does_not_buffer_excess_output():
    from ghimera.extraction import read_bounded

    async def exercise():
        stream = asyncio.StreamReader()
        stream.feed_data(b"x" * 11)
        stream.feed_eof()
        with pytest.raises(GhimeraRefused):
            await read_bounded(stream, 10)

    asyncio.run(exercise())
