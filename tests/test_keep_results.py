"""0.4.12: results are never thrown away for a reader or judge failure."""

import asyncio
import hashlib
from types import SimpleNamespace

from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.extraction import HtmlExtractor
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Extracted, Goal, Page, Scope
from ghimera.refusals import GhimeraRefused, RefusalCode
from tests.test_c0 import config
from tests.test_html_extraction import extractor, page


def scope() -> Scope:
    return Scope(allowed_hosts=("example.org",), max_depth=2, content_types=("text/html",))


def test_a_rich_tier_failure_keeps_the_page_as_plain_text(tmp_path, monkeypatch):
    async def fails(self, page, size):
        raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED, "rich reader: no title")

    monkeypatch.setattr(HtmlExtractor, "_extract", fails)
    source = page(
        "<html><head><script>EVIL SCRIPT</script><style>x{}</style></head>"
        "<body><div>Port authority report on maritime terminal construction.</div></body></html>"
    )
    parsed = asyncio.run(extractor(tmp_path).extract(source))
    evidence = parsed.extraction
    assert evidence is not None and evidence.tier == "fallback_plain"
    assert evidence.fallback_refusal == RefusalCode.EXTRACTION_FAILED
    assert evidence.fallback_detail == "rich reader: no title"
    assert evidence.parser_revision.startswith("ghimera.fallback-plain/1:")
    assert evidence.source_sha256 == hashlib.sha256(source.body).hexdigest()
    assert "maritime terminal construction" in parsed.text
    assert "EVIL SCRIPT" not in parsed.text
    assert type(evidence).model_validate(evidence.model_dump(by_alias=True)) == evidence


def test_a_page_with_no_text_at_all_is_still_refused(tmp_path, monkeypatch):
    async def fails(self, page, size):
        raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED, "rich reader: empty")

    monkeypatch.setattr(HtmlExtractor, "_extract", fails)
    empty = page("<html><head><script>only script</script></head><body> </body></html>")
    try:
        asyncio.run(extractor(tmp_path).extract(empty))
    except GhimeraRefused as exc:
        assert exc.code == RefusalCode.EXTRACTION_FAILED
    else:
        raise AssertionError("a page with no text must be refused")


def test_a_refused_pages_bytes_are_kept_under_the_run(tmp_path):
    root = tmp_path / ".refused-pages" / "run-1"
    session = SimpleNamespace(refused_archive=root)
    raw = page("<html></html>")
    name = GoalLoop._archive_refused(session, raw)  # type: ignore[arg-type]
    digest = hashlib.sha256(raw.body).hexdigest()
    assert name == f".refused-pages/run-1/{digest}"
    assert (root / digest).read_bytes() == raw.body
    assert (root / digest).stat().st_mode & 0o077 == 0


class JudgeOutage(FakeJudge):
    async def document(self, goal, document, *, second_look):
        raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE, "judge answered HTTP 503")

    async def grade(self, goal, documents):
        raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE, "judge answered HTTP 503")


def test_a_judge_outage_keeps_the_pages_and_the_round_completes():
    loop = GoalLoop(
        config=config(saturation_window=50, grade_interval=1),
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=JudgeOutage(),
    )
    result = asyncio.run(loop.run(Goal(text="ports", seeds=("https://example.org/a",)), scope()))
    assert result.receipt.stop_reason == "frontier_empty"
    assert len(result.documents) == 3
    assert all(doc.verdict.kind == "unjudged" for doc in result.documents)
    assert all(doc.verdict.reason.startswith("unjudged:") for doc in result.documents)
    refused = [row for row in result.ledger if row.reason == "served_judge_failed"]
    assert refused and refused[0].refusal == RefusalCode.MODEL_UNAVAILABLE


class BrokenAdapter(FakeExtractor):
    async def extract(self, page: Page) -> Extracted:
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT, "adapter returned an unbound link")


def test_a_failed_stop_row_carries_what_failed():
    loop = GoalLoop(
        config=config(),
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=BrokenAdapter(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    result = asyncio.run(loop.run(Goal(text="ports", seeds=("https://example.org/a",)), scope()))
    assert result.receipt.stop_reason == "failed"
    stop = result.ledger[-1]
    assert stop.event == "stop" and stop.reason == "failed"
    assert stop.detail == (
        "adapter_contract at https://example.org/a: adapter returned an unbound link"
    )
