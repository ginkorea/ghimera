"""Configured concurrency preserves run ownership and bounded spend."""

import asyncio
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.execution import StageSlots
from ghimera.execution_config import ExecutionConfig
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Extracted, Goal
from ghimera.refusals import GhimeraRefused, RefusalCode
from tests.test_c0 import config, scope
from tests.test_http_fetch import site as site
from tests.test_http_fetch import state


def policy(**updates):
    raw = dict(
        schema="ghimera.execution/1",
        active_sources=2,
        extraction_workers=2,
        scoring_workers=2,
        judge_workers=2,
        semantic_workers=2,
        visual_workers=2,
    )
    raw.update(updates)
    return ExecutionConfig.model_validate(raw)


def configured(**updates):
    return config(
        execution=policy().model_dump(by_alias=True),
        grade_interval=100,
        saturation_window=100,
        **updates,
    )


class LeafExtractor(FakeExtractor):
    async def extract(self, page):
        return Extracted(title=page.url, text=page.body.decode(), language="en", links=())


def loop_for(cfg, extractor, judge=None, route=None):
    return GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route or FakeRoute(),)),
        extractor=extractor,
        scorer=KeywordScorer(),
        judge=judge or FakeJudge(),
    )


def test_execution_is_explicit_immutable_and_legacy_wire_is_unchanged():
    assert "execution" not in config().model_dump(by_alias=True)
    with Path("examples/execution.toml").open("rb") as stream:
        configured = ExecutionConfig.model_validate(tomllib.load(stream))
    assert configured.active_sources == 8
    for update in (dict(active_sources=0), dict(judge_workers=True), dict(unknown=1)):
        with pytest.raises(ValidationError):
            policy(**update)
    with pytest.raises(ValidationError):
        configured.judge_workers = 10


def test_slow_extraction_does_not_block_another_source_and_checkpoints_refuse_inflight():
    async def run():
        slow = asyncio.Event()
        fast_reviewed = asyncio.Event()
        observed = []

        class Extractor(LeafExtractor):
            async def extract(self, page):
                if page.url.endswith("/a"):
                    slow.set()
                    await fast_reviewed.wait()
                else:
                    await slow.wait()
                return await super().extract(page)

        class Judge(FakeJudge):
            async def document(self, goal, document, *, second_look):
                observed.append(document.title)
                if document.title.endswith("/b"):
                    fast_reviewed.set()
                return await super().document(goal, document, second_look=second_look)

        loop = loop_for(configured(), Extractor(), Judge())
        goal = Goal(text="ports", seeds=("https://example.org/a", "https://example.org/b"))
        session = await loop.open(goal)
        task = asyncio.create_task(loop.collect(session, scope(), goal.seeds))
        await slow.wait()
        with pytest.raises(ValueError):
            session.checkpoint_state()
        with pytest.raises(GhimeraRefused):
            loop.finish(session, "failed")
        with pytest.raises(GhimeraRefused):
            await loop.collect(session, scope(), ())
        stop = await asyncio.wait_for(task, timeout=2)
        assert observed[0].endswith("/b")
        assert session.budget.quiescent
        session.checkpoint_state()
        harvest = loop.finish(session, stop)
        assert len(harvest.documents) == harvest.receipt.fetches == 2
        assert harvest.receipt.bytes_read == sum(row.bytes_read for row in harvest.ledger)
        assert [row.sequence for row in harvest.ledger] == list(range(len(harvest.ledger)))

    asyncio.run(run())


def test_stage_backpressure_does_not_reserve_model_calls_and_cancellation_drains_workers():
    async def run():
        entered = asyncio.Event()
        blocked = asyncio.Event()
        active = 0
        high = 0

        class Extractor(LeafExtractor):
            async def extract(self, page):
                nonlocal active, high
                active += 1
                high = max(high, active)
                entered.set()
                try:
                    await blocked.wait()
                    return await super().extract(page)
                finally:
                    active -= 1

        cfg = config(
            execution=policy(extraction_workers=1).model_dump(by_alias=True),
            grade_interval=100,
            saturation_window=100,
        )
        route = FakeRoute()
        loop = loop_for(cfg, Extractor(), route=route)
        goal = Goal(text="ports", seeds=("https://example.org/a", "https://example.org/b"))
        session = await loop.open(goal)
        task = asyncio.create_task(loop.collect(session, scope(), goal.seeds))
        await entered.wait()
        await asyncio.sleep(0)
        assert len(route.requests) == 2  # fetch ran ahead of the bounded parser
        assert session.budget.judge_calls == 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active == 0 and high == 1
        assert session.budget.quiescent
        session.checkpoint_state()
        assert (
            sum(row.reason == "source_processing_cancelled" for row in session.ledger.snapshot())
            == 2
        )

    asyncio.run(run())


def test_inflight_bytes_wait_instead_of_false_exhaustion():
    async def run():
        budget = RunBudget(config(byte_budget=10), asyncio.get_running_loop().time)
        first = await budget.wait_bytes(10)
        second = asyncio.create_task(budget.wait_bytes(10))
        await asyncio.sleep(0)
        assert not second.done()
        budget.record_bytes(4)
        budget.release_bytes(first)
        allowance = await asyncio.wait_for(second, timeout=1)
        assert allowance == 6
        budget.record_bytes(6)
        budget.release_bytes(allowance)
        assert budget.quiescent
        with pytest.raises(GhimeraRefused):
            await budget.wait_bytes(1)

    asyncio.run(run())


def test_stage_waiters_respect_wall_budget_without_leaking_a_slot():
    async def run():
        budget = RunBudget(config(wall_seconds=0.02), asyncio.get_running_loop().time)
        slots = StageSlots(policy(extraction_workers=1), budget)
        async with slots.slot("extraction"):

            async def waiter():
                async with slots.slot("extraction"):
                    pytest.fail("must not acquire the occupied slot")

            with pytest.raises(TimeoutError):
                await waiter()
        # Fresh budget on the same owner verifies the semaphore was not leaked.
        budget.started = asyncio.get_running_loop().time()
        async with slots.slot("extraction"):
            pass

    asyncio.run(run())


def test_parallel_sources_preserve_page_and_round_budgets_and_unique_dispatch():
    async def run():
        route = FakeRoute()
        loop = loop_for(configured(page_budget=2), LeafExtractor(), route=route)
        seeds = tuple(f"https://example.org/{n}" for n in range(5))
        session = await loop.open(Goal(text="ports", seeds=seeds))
        stop = await loop.collect(session, scope(), seeds + seeds, fetch_limit=1, allow_grade=False)
        assert stop == "round_limit"
        assert len(route.requests) == session.budget.fetches == 1
        assert len(session.documents) == 1
        stop = await loop.collect(session, scope(), (), allow_grade=False)
        assert stop == "budget_exhausted"
        assert len(route.requests) == session.budget.fetches == 2
        assert len({request.url for request in route.requests}) == 2
        assert len(session.documents) == 2

    asyncio.run(run())


def test_saturation_waits_for_inflight_sources_before_deciding_no_new_documents():
    async def run():
        cfg = config(
            execution=policy().model_dump(by_alias=True),
            saturation_window=1,
            grade_interval=100,
        )
        loop = loop_for(cfg, LeafExtractor(), route=FakeRoute(same_content=True))
        seeds = tuple(f"https://example.org/{n}" for n in range(6))
        result = await loop.run(Goal(text="ports", seeds=seeds), scope())
        assert result.receipt.stop_reason == "saturated"
        assert len(result.documents) == 1
        assert result.receipt.fetches > 1
        assert result.documents[0].duplicate_urls

    asyncio.run(run())


def test_grade_of_accepted_prefix_runs_while_an_independent_source_is_slow():
    async def run():
        graded = asyncio.Event()

        class Extractor(LeafExtractor):
            async def extract(self, page):
                if page.url.endswith("/b"):
                    await graded.wait()
                return await super().extract(page)

        class Judge(FakeJudge):
            async def grade(self, goal, documents):
                assert documents and documents[0].url.endswith("/a")
                graded.set()
                return await super().grade(goal, documents)

        cfg = config(
            execution=policy().model_dump(by_alias=True),
            grade_interval=1,
            saturation_window=100,
        )
        loop = loop_for(cfg, Extractor(), Judge())
        result = await asyncio.wait_for(
            loop.run(
                Goal(text="ports", seeds=("https://example.org/a", "https://example.org/b")),
                scope(),
            ),
            timeout=2,
        )
        assert len(result.documents) == 2
        assert result.receipt.judge_calls == 4  # the final newly accepted prefix is graded too

    asyncio.run(run())


def test_real_curl_source_flow_keeps_fast_response_ahead_of_slow_response(site):
    observed = []

    class Judge(FakeJudge):
        async def document(self, goal, document, *, second_look):
            observed.append(document.title)
            return await super().document(goal, document, second_look=second_look)

    fetcher, scoped, budget, _, origin = state(
        site,
        execution=policy().model_dump(by_alias=True),
        grade_interval=100,
        saturation_window=100,
    )
    loop = GoalLoop(
        config=budget.config,
        fetcher=fetcher,
        extractor=LeafExtractor(),
        scorer=KeywordScorer(),
        judge=Judge(),
    )
    result = asyncio.run(
        loop.run(Goal(text="ports", seeds=(origin + "/slow", origin + "/plain")), scoped)
    )
    assert observed[0].endswith("/plain")
    assert {url.rsplit("/", 1)[1] for url in observed} == {"plain", "slow"}
    assert site[1]["/slow"] == site[1]["/plain"] == 1
    assert result.receipt.bytes_read == sum(row.bytes_read for row in result.ledger)
    assert not any(row.refusal for row in result.ledger)


def test_goal_satisfaction_cannot_hide_concurrent_graph_storage_failure():
    async def run():
        graded = asyncio.Event()

        class Extractor(LeafExtractor):
            async def extract(self, page):
                if page.url.endswith("/b"):
                    await graded.wait()
                    raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
                return await super().extract(page)

        class Judge(FakeJudge):
            async def grade(self, goal, documents):
                graded.set()
                await asyncio.sleep(0)
                return await super().grade(goal, documents)

        cfg = config(
            execution=policy().model_dump(by_alias=True),
            grade_interval=1,
            saturation_window=100,
        )
        loop = loop_for(cfg, Extractor(), Judge(satisfied=True))
        with pytest.raises(GhimeraRefused, match="graph_sink_failed"):
            await asyncio.wait_for(
                loop.run(
                    Goal(text="ports", seeds=("https://example.org/a", "https://example.org/b")),
                    scope(),
                ),
                timeout=2,
            )

    asyncio.run(run())


def test_final_fast_sources_are_graded_even_when_the_page_budget_is_spent():
    loop = loop_for(
        config(
            execution=policy().model_dump(by_alias=True),
            page_budget=2,
            grade_interval=2,
            saturation_window=100,
        ),
        LeafExtractor(),
        FakeJudge(satisfied=True),
    )
    result = asyncio.run(
        loop.run(
            Goal(text="ports", seeds=("https://example.org/a", "https://example.org/b")), scope()
        )
    )
    assert len(result.documents) == 2
    assert result.receipt.stop_reason == "goal_satisfied"
    assert result.receipt.judge_calls == 3


def test_grade_budget_refusal_stops_without_repeated_reservation_attempts():
    async def run():
        loop = loop_for(
            config(
                execution=policy().model_dump(by_alias=True),
                judge_budget=2,
                grade_interval=2,
                saturation_window=100,
            ),
            LeafExtractor(),
        )
        result = await asyncio.wait_for(
            loop.run(
                Goal(text="ports", seeds=("https://example.org/a", "https://example.org/b")),
                scope(),
            ),
            timeout=1,
        )
        assert result.receipt.stop_reason == "budget_exhausted"
        assert result.receipt.judge_calls == 2
        assert sum(row.event == "verdict" for row in result.ledger) == 2

    asyncio.run(run())
