"""0.4.17: one page's invalid worker answer is a page fault, never a run failure."""

import asyncio
import time
from pathlib import Path

import pytest
from test_extraction_recovery import client
from test_graph_planning import NativeFixture
from test_html_extraction import ARTICLE, page
from test_semantic_graph import SemanticWire, configured, document

from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.extraction import ExtractionRequest
from ghimera.extraction_attempts import InvalidExtractionResponse
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Goal, Scope
from ghimera.refusals import RefusalCode

BAD, GOOD = "https://example.org/bad", "https://example.org/good"


class HtmlRoute(FakeRoute):
    def __init__(self, bodies):
        super().__init__()
        self.bodies = bodies

    async def attempt(self, request):
        self.requests.append(request)
        return page(self.bodies[request.url], url=request.url, final_url=request.url)


def collect(tmp_path, monkeypatch, bodies):
    parser = client(tmp_path)
    run = parser._worker.run

    async def corrupt_bad_page(raw):
        if ExtractionRequest.model_validate_json(raw).page.final_url == BAD:
            return b"not JSON at all"
        return await run(raw)

    monkeypatch.setattr(parser._worker, "run", corrupt_bad_page)
    settings = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    settings.update(extraction=parser.config.model_dump(by_alias=True))
    return asyncio.run(
        GoalLoop(
            config=GhimeraConfig.model_validate(settings),
            fetcher=FetchLadder((HtmlRoute(bodies),)),
            extractor=parser,
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ).run(
            Goal(text="ports", seeds=(BAD, GOOD)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
    )


def test_invalid_worker_answer_is_a_page_extraction_fault_with_detail():
    fault = InvalidExtractionResponse("answer is not JSON")
    assert fault.code == RefusalCode.EXTRACTION_FAILED
    assert "failed result validation: answer is not JSON" in fault.detail


def test_an_invalid_answer_for_one_page_falls_back_and_the_round_completes(tmp_path, monkeypatch):
    harvest = collect(tmp_path, monkeypatch, {BAD: ARTICLE, GOOD: ARTICLE.replace("42", "43")})
    assert harvest.receipt.stop_reason != "failed"
    kept = {doc.url: doc for doc in harvest.documents}
    assert set(kept) == {BAD, GOOD}
    bad = kept[BAD].extracted.extraction
    assert bad.tier == "fallback_plain" and bad.fallback_refusal == RefusalCode.EXTRACTION_FAILED
    assert [a.outcome for a in bad.fallback_attempts] == ["invalid_response"] * 2
    assert all(a.refusal == RefusalCode.EXTRACTION_FAILED for a in bad.fallback_attempts)
    assert "not JSON at all" not in harvest.model_dump_json()
    assert not any(row.refusal == RefusalCode.ADAPTER_CONTRACT for row in harvest.ledger)


def test_an_unreadable_page_with_an_invalid_answer_is_refused_and_others_kept(
    tmp_path, monkeypatch
):
    empty = "<html><body><script>x()</script></body></html>"
    harvest = collect(tmp_path, monkeypatch, {BAD: empty, GOOD: ARTICLE})
    assert harvest.receipt.stop_reason != "failed"
    assert [doc.url for doc in harvest.documents] == [GOOD]
    refused = [r for r in harvest.ledger if r.event == "refusal" and r.url == BAD]
    assert refused and refused[-1].refusal == RefusalCode.EXTRACTION_FAILED
    assert "result validation" in (refused[-1].detail or "")


def test_legacy_adapter_contract_attempts_still_load():
    from ghimera.extraction_attempts import HtmlExtractionAttempt

    raw = dict(
        schema="chimera.html-extraction-attempt/1",
        sequence=0,
        phase="initial",
        generic_only=False,
        source_url=BAD,
        source_sha256="a" * 64,
        rendered_sha256=None,
        config_digest="b" * 64,
        parser_revision="parser@1",
        outcome="invalid_response",
        refusal="adapter_contract",
        response_sha256="c" * 64,
        latency_seconds=0.1,
    )
    assert HtmlExtractionAttempt.model_validate(raw).refusal == RefusalCode.ADAPTER_CONTRACT


@pytest.mark.parametrize(
    ("bad", "said"),
    [
        ("wrong_surface", "is not in the window"),
        ("unknown_citation", "cites a window other than the one it was read from"),
    ],
)
def test_semantic_window_refusals_say_which_claim_failed_and_why(tmp_path, bad, said):
    cfg = configured(tmp_path)
    _, rows, _ = exercise_refused(cfg, SemanticWire(cfg.models.analyst, **{bad: True}))
    refused = [row for row in rows if row.reason == "semantic_window_refused"]
    assert refused and refused[0].refusal == RefusalCode.SEMANTIC_EXTRACTION_FAILED
    detail = refused[0].detail or ""
    assert detail.startswith("window 0-") and "mention 'm1'" in detail and said in detail
    assert "refused by ghimera" not in detail


def exercise_refused(cfg, wire):
    from ghimera.graph import MemoryGraphSink, ResearchGraph
    from ghimera.ledger import Ledger
    from ghimera.model_client import SelfHostedModel
    from ghimera.refusals import GhimeraRefused
    from ghimera.semantic_graph import SemanticStage
    from ghimera.spend import RunSpend

    doc, ledger = document(), Ledger()

    async def run():
        graph = ResearchGraph(cfg.graph, "refused", MemoryGraphSink())
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
        stage = SemanticStage(cfg, SelfHostedModel(cfg, cfg.models.analyst, http=wire))
        with pytest.raises(GhimeraRefused):
            await stage.extract(
                "map the organization", doc, identity, graph, RunSpend(cfg, time.monotonic), ledger
            )
        return graph.snapshot()

    return asyncio.run(run()), ledger.snapshot(), doc


def test_a_page_whose_semantic_window_is_refused_keeps_its_graph_node_and_reading(tmp_path):
    from ghimera.model_client import SelfHostedModel

    cfg = configured(tmp_path)
    wire = SemanticWire(cfg.models.analyst, wrong_surface=True)
    harvest = asyncio.run(
        GoalLoop(
            config=cfg,
            fetcher=FetchLadder((FakeRoute(),)),
            extractor=NativeFixture(),
            scorer=KeywordScorer(),
            judge=FakeJudge(),
            semantic_extractor=SelfHostedModel(cfg, cfg.models.analyst, http=wire),
        ).run(
            Goal(text="map the organization", seeds=(GOOD,)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
            run_id="semantic-refused",
        )
    )
    assert harvest.receipt.stop_reason != "failed"
    assert [doc.url for doc in harvest.documents] == [GOOD]
    assert harvest.documents[0].verdict.decision == "accept"
    assert harvest.graph is not None
    refused = [r for r in harvest.ledger if r.reason == "semantic_window_refused"]
    assert refused and "is not in the window" in (refused[0].detail or "")
