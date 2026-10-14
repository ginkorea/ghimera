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


def test_research_rounds_are_off_by_default_and_saturation_is_the_stop():
    from ghimera.research_config import DEFAULT_SATURATION_ROUNDS, ResearchConfig
    from tests.test_intent_research import policy

    raw = policy()
    del raw["max_rounds"], raw["query_budget"]
    unbounded = ResearchConfig.model_validate(raw)
    assert (unbounded.max_rounds, unbounded.query_budget) == (None, None)
    assert unbounded.effective_saturation_rounds == DEFAULT_SATURATION_ROUNDS
    assert "max_rounds" not in unbounded.model_dump(by_alias=True)
    explicit = ResearchConfig.model_validate(policy(max_rounds="unbounded", saturation_rounds=4))
    assert explicit.effective_saturation_rounds == 4
    # An explicit round limit keeps the original behaviour and identity.
    limited = ResearchConfig.model_validate(policy())
    assert limited.max_rounds == 3 and limited.effective_saturation_rounds is None
    assert "saturation_rounds" not in limited.model_dump()


def test_run_limits_are_off_by_default_and_spend_is_still_accounted():
    from ghimera.config import GhimeraConfig

    raw = config().model_dump(by_alias=True)
    for key in ("page_budget", "byte_budget", "wall_seconds", "judge_budget"):
        del raw[key]
    cfg = GhimeraConfig.model_validate(raw)
    assert (cfg.page_budget, cfg.byte_budget, cfg.wall_seconds, cfg.judge_budget) == (None,) * 4
    assert not {"page_budget", "byte_budget", "wall_seconds", "judge_budget"} & set(
        cfg.model_dump(by_alias=True)
    )
    loop = GoalLoop(
        config=cfg.model_copy(update={"saturation_window": 50}),
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    result = asyncio.run(loop.run(Goal(text="ports", seeds=("https://example.org/a",)), scope()))
    assert result.receipt.stop_reason == "frontier_empty"
    assert result.receipt.fetches == 3 and result.receipt.judge_calls == 3
    assert result.receipt.bytes_read > 0
    assert not any(row.refusal == RefusalCode.BUDGET_EXHAUSTED for row in result.ledger)
