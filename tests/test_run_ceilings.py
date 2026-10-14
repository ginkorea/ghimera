"""0.4.12: optional run ceilings, named budget refusals and page-local refusals."""

import asyncio

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.ceilings import UNBOUNDED_WALL_SECONDS
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Extracted, Goal, LinkCandidate, Page, Scope
from ghimera.refusals import BudgetExhausted, GhimeraRefused, RefusalCode
from tests.test_c0 import config


def scope() -> Scope:
    return Scope(allowed_hosts=("example.org",), max_depth=2, content_types=("text/html",))


@pytest.mark.parametrize("value", [None, 0, "unbounded", "UNBOUNDED"])
def test_every_named_ceiling_can_be_unbounded(value):
    cfg = config(page_budget=value, byte_budget=value, judge_budget=value, wall_seconds=value)
    assert (cfg.page_budget, cfg.byte_budget, cfg.judge_budget, cfg.wall_seconds) == (None,) * 4
    # The effective recipe round-trips: an unbounded ceiling stays unbounded.
    assert type(cfg).model_validate(cfg.model_dump(by_alias=True)) == cfg
    budget = RunBudget(cfg, lambda: 0.0)
    assert budget.remaining_seconds == UNBOUNDED_WALL_SECONDS
    assert budget.exhausted_ceiling() is None
    for _ in range(10_000):
        budget.reserve_fetch()
        budget.reserve_judge()
    budget.record_bytes(10**12)
    assert budget.exhausted_ceiling() is None


def test_integer_ceilings_parse_and_serialize_unchanged():
    cfg = config(page_budget=7)
    assert cfg.page_budget == 7
    assert cfg.model_dump(by_alias=True)["page_budget"] == 7
    with pytest.raises(ValidationError):
        config(page_budget=-3)
    with pytest.raises(ValidationError):
        config(wall_seconds="hours")


def test_budget_refusal_names_which_ceiling_with_used_and_limit():
    budget = RunBudget(config(page_budget=2, judge_budget=1), lambda: 0.0)
    budget.reserve_fetch()
    budget.reserve_fetch()
    with pytest.raises(BudgetExhausted) as fetch:
        budget.reserve_fetch()
    assert fetch.value.code == RefusalCode.BUDGET_EXHAUSTED
    assert (fetch.value.budget, fetch.value.used, fetch.value.limit) == ("page_budget", 2, 2)
    assert fetch.value.detail == "run budget page_budget exhausted: used 2 of limit 2"
    budget.reserve_judge()
    with pytest.raises(BudgetExhausted) as judge:
        budget.reserve_judge()
    assert "judge_budget" in judge.value.detail and "used 1 of limit 1" in judge.value.detail


def test_run_level_stop_records_the_named_ceiling_in_the_ledger():
    loop = GoalLoop(
        config=config(page_budget=2, saturation_window=10),
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    result = asyncio.run(loop.run(Goal(text="ports", seeds=("https://example.org/a",)), scope()))
    assert result.receipt.stop_reason == "budget_exhausted"
    refusals = [row for row in result.ledger if row.refusal == RefusalCode.BUDGET_EXHAUSTED]
    assert refusals and refusals[-1].detail == (
        "run budget page_budget exhausted: used 2 of limit 2"
    )


class OnePageTimesOut(FakeExtractor):
    """One page's own reader bound; every other page is ordinary."""

    async def extract(self, page: Page) -> Extracted:
        if page.final_url.endswith("/slow"):
            raise GhimeraRefused(
                RefusalCode.BUDGET_EXHAUSTED, "html reader exceeded timeout_seconds=1"
            )
        if page.final_url.endswith("/a"):
            return Extracted(
                title="hub",
                text="ports hub",
                language="en",
                links=(
                    LinkCandidate(url="https://example.org/slow", anchor="ports slow"),
                    LinkCandidate(url="https://example.org/fast", anchor="ports fast"),
                ),
            )
        return Extracted(title="leaf", text=page.body.decode(), language="en", links=())


@pytest.mark.parametrize("parallel", [False, True])
def test_one_pages_own_bound_never_ends_the_round(parallel):
    execution = (
        dict(
            schema="ghimera.execution/1",
            active_sources=4,
            extraction_workers=4,
            scoring_workers=4,
            judge_workers=4,
            semantic_workers=1,
            visual_workers=1,
        )
        if parallel
        else None
    )
    cfg = config(page_budget=None, saturation_window=50, execution=execution)
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=OnePageTimesOut(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    result = asyncio.run(loop.run(Goal(text="ports", seeds=("https://example.org/a",)), scope()))
    assert result.receipt.stop_reason == "frontier_empty"
    assert {doc.url for doc in result.documents} == {
        "https://example.org/a",
        "https://example.org/fast",
    }
    slow = [row for row in result.ledger if row.url == "https://example.org/slow" and row.refusal]
    assert slow[-1].refusal == RefusalCode.BUDGET_EXHAUSTED
    assert slow[-1].detail == "html reader exceeded timeout_seconds=1"


def test_unbounded_research_rounds_require_their_own_saturation_stop():
    from ghimera.research_config import ResearchConfig
    from tests.test_intent_research import policy

    with pytest.raises(ValidationError, match="saturation_rounds"):
        ResearchConfig.model_validate(policy(max_rounds="unbounded"))
    unbounded = ResearchConfig.model_validate(
        policy(max_rounds="unbounded", query_budget=0, saturation_rounds=2)
    )
    assert (unbounded.max_rounds, unbounded.query_budget, unbounded.saturation_rounds) == (
        None,
        None,
        2,
    )
    # Absent saturation_rounds is omitted, preserving existing recipe identities.
    assert "saturation_rounds" not in ResearchConfig.model_validate(policy()).model_dump()
