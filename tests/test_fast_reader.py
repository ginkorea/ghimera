"""0.4.12: first-pass in-process reading and persistent reader workers."""

import asyncio
import hashlib
import sys
import time

import pytest

from ghimera.extraction import HtmlExtractor
from ghimera.fast_reader import fast_reader_revision, read_fast
from ghimera.passive_worker import PassiveWorker
from ghimera.persistent_worker import PersistentWorkerPool
from ghimera.refusals import GhimeraRefused, RefusalCode
from tests.test_html_extraction import ARTICLE, extractor, page, policy

FAST = {"schema": "ghimera.fast-reader/1", "min_text_chars": 80}


def megabyte_page() -> str:
    paragraph = (
        "<p>The port authority published a report about infrastructure investment, "
        "maritime transport and the construction of a new container terminal.</p>"
        '<a href="/reports/{0}">Report {0}</a>'
    )
    body = "".join(paragraph.format(index) for index in range(6000))
    html = (
        "<!doctype html><html lang='en'><head><title>Ports</title>"
        "<script>" + "var x = 1;" * 2000 + "</script></head><body><nav>menu</nav>"
        f"<article>{body}</article><footer>footer</footer></body></html>"
    )
    assert len(html.encode()) >= 1_000_000
    return html


def refuse_subprocess(monkeypatch):
    async def never(self, request):
        raise AssertionError("the first pass must not start a parser subprocess")

    monkeypatch.setattr(PassiveWorker, "run", never)


def test_fast_reader_is_in_process_and_drops_boilerplate(tmp_path, monkeypatch):
    refuse_subprocess(monkeypatch)
    reader = extractor(tmp_path, fast_reader=FAST)
    parsed = asyncio.run(reader.extract(page()))
    assert parsed.title == "Port infrastructure report"
    assert parsed.language == "en"
    assert "infrastructure investment" in parsed.text and "Taiwan" in parsed.text
    assert "EVIL SCRIPT" not in parsed.text and "COOKIE FOOTER" not in parsed.text
    assert "Unrelated navigation" not in parsed.text
    assert parsed.byline == "Research Office" and parsed.date == "2026-10-06"
    assert parsed.canonical_url == "https://example.org/reports/ports"
    assert [(link.url, link.anchor) for link in parsed.links] == [
        ("https://example.org/reports/appendix.pdf", "Supporting appendix")
    ]
    evidence = parsed.extraction
    assert evidence is not None and evidence.parser_revision == fast_reader_revision()
    assert evidence.text_sha256 == hashlib.sha256(parsed.text.encode()).hexdigest()
    evidence.validate_policy(policy(tmp_path, fast_reader=FAST))
    assert reader.revision.startswith("ghimera.fast-reader/1:lxml@")
    assert reader.revision.endswith("|scrapling@0.4.2+crawl4ai@0.9.4+lingua@2.1.1")
    assert "crawl4ai" not in sys.modules


def test_one_megabyte_page_reads_in_under_100_ms(tmp_path):
    config = policy(tmp_path, fast_reader=FAST)
    source = page(megabyte_page())
    read_fast(source, config)  # first call loads the language model once per process
    timings = []
    for _ in range(3):  # best of three: a shared test host is not a benchmark rig
        started = time.perf_counter()
        parsed = read_fast(source, config)
        timings.append(time.perf_counter() - started)
    elapsed = min(timings)
    assert parsed is not None and len(parsed.text) == config.max_text_chars
    assert parsed.extraction is not None and parsed.extraction.omitted_text_chars > 400_000
    assert "var x" not in parsed.text and "menu" not in parsed.text
    assert len(parsed.links) == config.max_links
    assert parsed.extraction is not None and parsed.extraction.omitted_links == 6000 - 200
    assert elapsed < 0.1, f"1 MB page took {elapsed:.3f}s in-process"


def test_thirty_pages_read_concurrently_without_subprocesses(tmp_path, monkeypatch):
    refuse_subprocess(monkeypatch)
    reader = extractor(tmp_path, fast_reader=FAST)
    pages = [
        page(ARTICLE.replace("Taiwan", f"Taiwan {index}"), final_url=f"https://example.org/{index}")
        for index in range(30)
    ]

    async def read_all():
        started = time.perf_counter()
        results = await asyncio.gather(*(reader.extract(item) for item in pages))
        return results, time.perf_counter() - started

    results, elapsed = asyncio.run(read_all())
    assert [f"Taiwan {index}" in item.text for index, item in enumerate(results)] == [True] * 30
    assert elapsed < 2.0


def test_thin_pages_and_profiled_hosts_go_to_the_richer_tier(tmp_path, monkeypatch):
    calls = []

    async def counted(self, request):
        calls.append(len(request))
        raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED, "fixture rich tier")

    monkeypatch.setattr(PassiveWorker, "run", counted)
    shell = "<html><head><title>App</title></head><body><div id=app></div></body></html>"
    with pytest.raises(GhimeraRefused) as thin:
        asyncio.run(extractor(tmp_path, fast_reader=FAST).extract(page(shell)))
    assert calls and thin.value.code == RefusalCode.EXTRACTION_FAILED
    assert thin.value.detail == "fixture rich tier"
    profiled = extractor(
        tmp_path,
        fast_reader=FAST,
        profiles=[{"host": "example.org", "profile_id": "story", "body": "article#story"}],
    )
    calls.clear()
    with pytest.raises(GhimeraRefused):
        asyncio.run(profiled.extract(page()))
    assert calls, "an operator locator profile is always read by the richer tier"


def test_text_beyond_the_cap_is_cut_and_counted(tmp_path):
    config = policy(tmp_path, fast_reader=FAST, max_text_chars=1000)
    parsed = read_fast(page(megabyte_page()), config)
    assert parsed is not None and len(parsed.text) == 1000
    assert parsed.extraction is not None and parsed.extraction.omitted_text_chars > 500_000


def test_persistent_workers_import_once_and_match_per_page_output(tmp_path):
    per_page = asyncio.run(extractor(tmp_path / "a").extract(page()))
    reader = extractor(tmp_path / "b", worker_mode="persistent", max_workers=2)
    pool = reader._worker
    assert isinstance(pool, PersistentWorkerPool)

    async def many():
        first = await reader.extract(page())
        started = time.perf_counter()
        rest = await asyncio.gather(*(reader.extract(page()) for _ in range(6)))
        return first, rest, time.perf_counter() - started

    try:
        first, rest, warm = asyncio.run(many())
        assert first.text == per_page.text and first.links == per_page.links
        assert all(item.text == per_page.text for item in rest)
        assert len(pool._live) <= 2 and sum(worker.served for worker in pool._idle) == 7
        assert warm < 12.0, f"six warm pages took {warm:.2f}s"
        # Workers are not tied to one event loop: a later run reuses them.
        live = set(pool._live)
        again = asyncio.run(reader.extract(page()))
        assert again.text == per_page.text and set(pool._live) == live
    finally:
        reader.close()
    assert not pool._live and not list((tmp_path / "b" / "worker").glob("package-*"))


def test_persistent_worker_deadline_kills_and_names_the_bound(tmp_path):
    reader = extractor(tmp_path, worker_mode="persistent", timeout_seconds=0.01)
    try:
        with pytest.raises(GhimeraRefused) as refused:
            asyncio.run(reader.extract(page()))
    finally:
        pool = reader._worker
        reader.close()
    assert refused.value.code == RefusalCode.BUDGET_EXHAUSTED
    assert "timeout_seconds=0.01" in refused.value.detail
    assert isinstance(pool, PersistentWorkerPool) and not pool._live


def test_extraction_refusals_name_the_reader_and_page_size(tmp_path):
    reader: HtmlExtractor = extractor(tmp_path, max_input_bytes=100)
    with pytest.raises(GhimeraRefused) as large:
        asyncio.run(reader.extract(page()))
    assert large.value.detail.startswith("html page of ")
    assert "exceeds max_input_bytes=100" in large.value.detail


def test_fast_reading_example_is_a_valid_policy(tmp_path):
    import tomllib
    from pathlib import Path

    from ghimera.model_retry import ModelRetryPolicy

    with Path("examples/fast-reading.toml").open("rb") as stream:
        fragment = tomllib.load(stream)
    extraction = policy(tmp_path, **fragment["extraction"])
    assert extraction.worker_mode == "persistent"
    assert extraction.fast_reader is not None and extraction.fast_reader.min_text_chars == 400
    assert ModelRetryPolicy.model_validate(fragment["model_retry"]) == ModelRetryPolicy()
