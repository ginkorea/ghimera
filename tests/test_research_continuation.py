"""Durable round-boundary continuation, not a claim of live model quality."""

import asyncio
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.continuation import CheckpointStore, ResearchSuspended
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.journal import DirectoryLedgerSink, read_journal
from ghimera.loop import GoalLoop
from ghimera.models import LedgerRow
from ghimera.refusals import GhimeraRefused
from ghimera.research import ResearchLoop
from ghimera.research_types import ResearchRequest
from tests.test_c0 import config
from tests.test_intent_research import (
    AnalystFixture,
    PlannerFixture,
    ReviewerFixture,
    SearchFixture,
    policy,
)


def configured(tmp_path, *, judge_budget=40, **updates):
    graph = tomllib.loads(Path("examples/research-graph.toml").read_text())
    graph["sink_path"] = str(tmp_path / "graph")
    return config(
        graph=graph,
        journal=dict(
            schema="chimera.run-journal-config/1",
            directory=str(tmp_path / "journal"),
            max_record_bytes=2000000,
            max_journal_bytes=10000000,
            max_summary_bytes=2000000,
            max_records=2000,
        ),
        continuation=dict(
            schema="ghimera.continuation/1",
            max_checkpoint_bytes=4000000,
            clock_policy="include_downtime",
        ),
        research=policy(max_pages_per_round=1, max_depth=3),
        page_budget=30,
        judge_budget=judge_budget,
        **updates,
    )


class FollowupPlanner(PlannerFixture):
    def __init__(self):
        self.requests = []

    async def plan(self, request):
        self.requests.append(request)
        plan = await super().plan(request)
        # A resumed run has its original question pack and prior documents.
        return plan.model_copy(update={"queries": () if request.documents else plan.queries})


def assemble(cfg, *, missing=False, route=None):
    route = route or FakeRoute()
    search, planner = SearchFixture(), FollowupPlanner()
    collection = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route,)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    research = ResearchLoop(
        config=cfg,
        collector=collection,
        search=search,
        planner=planner,
        analyst=AnalystFixture(missing=missing),
        reviewer=ReviewerFixture(),
    )
    return research, route, search, planner


def suspend(loop, run_id="research"):
    with pytest.raises(ResearchSuspended) as stopped:
        asyncio.run(
            loop.run(
                ResearchRequest(intent="find ports"),
                run_id=run_id,
                suspend_after_rounds=1,
            )
        )
    return stopped.value.receipt


def test_resume_after_answer_ready_does_not_repeat_collection_search_or_assessment(tmp_path):
    cfg = configured(tmp_path)
    first, route, search, _ = assemble(cfg)
    receipt = suspend(first)
    checkpoint = CheckpointStore(cfg, "research").read(receipt.sha256)
    assert checkpoint.next_action == "answer"
    assert read_journal(cfg.journal, "research").state == "unsealed"
    before = checkpoint.progress.harvest.ledger
    assert not any(row.event == "stop" for row in before)
    # Reconstruct every collaborator, simulating a different process.
    resumed, new_route, new_search, new_planner = assemble(cfg)
    result = asyncio.run(resumed.resume("research", checkpoint_sha256=receipt.sha256))
    assert result.status == "answered"
    assert not new_route.requests and not new_search.requests and not new_planner.requests
    assert len(route.requests) == len(search.requests) == 1
    assert result.harvest.ledger[: len(before)] == before
    assert result.harvest.receipt.judge_calls == checkpoint.progress.harvest.receipt.judge_calls + 2
    assert result.harvest.graph == checkpoint.progress.harvest.graph
    assert read_journal(cfg.journal, "research").state == "complete"
    assert result == type(result).model_validate_json(result.model_dump_json())


def test_resume_preserves_pending_frontier_and_does_not_refetch_completed_sources(tmp_path):
    cfg = configured(tmp_path)
    first, route, _, _ = assemble(cfg, missing=True)
    receipt = suspend(first)
    checkpoint = CheckpointStore(cfg, "research").read(receipt.sha256)
    assert checkpoint.next_action == "plan" and checkpoint.session.frontier
    resumed, new_route, new_search, planner = assemble(cfg, missing=True)
    result = asyncio.run(resumed.resume("research", checkpoint_sha256=receipt.sha256))
    assert planner.requests[0].documents == checkpoint.progress.harvest.source_documents
    assert planner.requests[0].questions == checkpoint.progress.questions
    assert not new_search.requests
    assert all(request.url != route.requests[0].url for request in new_route.requests)
    assert len(new_route.requests) == 2
    assert [round_.number for round_ in result.rounds] == [1, 2, 3]
    assert result.search_calls == checkpoint.progress.search_calls == 1
    assert result.harvest.receipt.fetches == checkpoint.progress.harvest.receipt.fetches + 2


@pytest.mark.parametrize("change", ["recipe", "hash", "tail", "torn", "sealed"])
def test_resume_refuses_changed_or_unreconciled_evidence_before_any_io(tmp_path, change):
    cfg = configured(tmp_path)
    first, _, _, _ = assemble(cfg, missing=True)
    receipt = suspend(first)
    run_dir = cfg.journal.directory / "research"
    pin = receipt.sha256
    if change == "recipe":
        cfg = GhimeraConfig.model_validate(dict(cfg.model_dump(), judge_budget=41))
    elif change == "hash":
        pin = "0" * 64
    elif change == "tail":
        report = read_journal(cfg.journal, "research")
        sink = DirectoryLedgerSink(
            cfg,
            "research",
            report.header.goal,
            report.header.judge,
            resume_rows=report.rows,
        )
        sink.append(LedgerRow(sequence=len(report.rows), event="refusal", reason="later work"))
        sink.close()
    elif change == "torn":
        with (run_dir / "ledger.jsonl").open("ab") as stream:
            stream.write(b'{"unfinished":')
    else:
        resumed, _, _, _ = assemble(cfg)
        asyncio.run(resumed.resume("research", checkpoint_sha256=pin))
    resumed, route, search, planner = assemble(cfg)
    with pytest.raises((ValueError, GhimeraRefused)):
        asyncio.run(resumed.resume("research", checkpoint_sha256=pin))
    assert not route.requests and not search.requests and not planner.requests


def test_journal_writer_lock_prevents_resume_beside_a_live_writer(tmp_path):
    cfg = configured(tmp_path)
    first, _, _, _ = assemble(cfg)
    receipt = suspend(first)
    report = read_journal(cfg.journal, "research")
    sink = DirectoryLedgerSink(
        cfg, "research", report.header.goal, report.header.judge, resume_rows=report.rows
    )
    resumed, route, search, planner = assemble(cfg)
    try:
        with pytest.raises(GhimeraRefused, match="ledger_sink_failed"):
            asyncio.run(resumed.resume("research", checkpoint_sha256=receipt.sha256))
        assert not route.requests and not search.requests and not planner.requests
    finally:
        sink.close()


def test_configuration_requires_the_existing_durable_journal(tmp_path):
    cfg = configured(tmp_path)
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(dict(cfg.model_dump(), journal=None))
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(
            dict(
                cfg.model_dump(),
                continuation=dict(cfg.continuation.model_dump(), clock_policy="reset"),
            )
        )
    unconfigured, route, search, planner = assemble(
        GhimeraConfig.model_validate(dict(cfg.model_dump(), continuation=None))
    )
    with pytest.raises(ValueError):
        asyncio.run(
            unconfigured.run(
                ResearchRequest(intent="find ports"), run_id="none", suspend_after_rounds=1
            )
        )
    assert not route.requests and not search.requests and not planner.requests


def test_process_restart_resumes_a_checkpoint_and_seals_the_same_journal(tmp_path):
    cfg = configured(tmp_path)
    first, _, _, _ = assemble(cfg)
    receipt = suspend(first, "different-process")
    configuration = tmp_path / "configuration.json"
    configuration.write_text(cfg.model_dump_json())
    code = """
import asyncio, json, sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from tests.test_research_continuation import assemble
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
loop, route, search, planner = assemble(cfg)
result = asyncio.run(loop.resume('different-process', checkpoint_sha256=sys.argv[2]))
print(json.dumps({'status': result.status, 'fetches': result.harvest.receipt.fetches,
                  'new_fetches': len(route.requests), 'new_plans': len(planner.requests)}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(configuration), receipt.sha256],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == dict(
        status="answered", fetches=2, new_fetches=0, new_plans=0
    )
    assert not result.stderr
    assert read_journal(cfg.journal, "different-process").state == "complete"


def test_downtime_is_spent_time_not_a_new_wall_budget(tmp_path, monkeypatch):
    cfg = configured(tmp_path, wall_seconds=100)
    first, _, _, _ = assemble(cfg)
    receipt = suspend(first)
    checkpoint = CheckpointStore(cfg, "research").read(receipt.sha256)
    monkeypatch.setattr("ghimera.research.time.time", lambda: checkpoint.saved_at + 101)
    resumed, route, search, planner = assemble(cfg)
    result = asyncio.run(resumed.resume("research", checkpoint_sha256=receipt.sha256))
    assert result.stop_reason == "budget_exhausted" and result.answer is None
    assert result.harvest.receipt.elapsed_seconds >= 101
    assert not route.requests and not search.requests and not planner.requests
    assert result.harvest.receipt.judge_calls == checkpoint.progress.harvest.receipt.judge_calls


def test_graph_changes_refuse_resume_without_rewriting_the_graph(tmp_path):
    from ghimera.graph import DirectoryGraphSink, ResearchGraph

    cfg = configured(tmp_path)
    first, _, _, _ = assemble(cfg)
    receipt = suspend(first)

    async def alter():
        graph = ResearchGraph(cfg.graph, "research", DirectoryGraphSink(cfg.graph, "research"))
        await graph.start("find ports")
        await graph.discovered("https://example.org/later", graph.intent_id)

    asyncio.run(alter())
    files = {p.name: p.read_bytes() for p in (cfg.graph.sink_path / "research").iterdir()}
    resumed, route, search, planner = assemble(cfg)
    with pytest.raises(ValueError, match="graph changed"):
        asyncio.run(resumed.resume("research", checkpoint_sha256=receipt.sha256))
    assert files == {p.name: p.read_bytes() for p in (cfg.graph.sink_path / "research").iterdir()}
    assert not route.requests and not search.requests and not planner.requests


def test_budget_spend_is_not_reset_to_answer_after_resumption(tmp_path):
    cfg = configured(tmp_path, judge_budget=3)
    first, _, _, _ = assemble(cfg)
    receipt = suspend(first)
    checkpoint = CheckpointStore(cfg, "research").read(receipt.sha256)
    assert checkpoint.progress.harvest.receipt.judge_calls == 3
    resumed, route, search, planner = assemble(cfg)
    result = asyncio.run(resumed.resume("research", checkpoint_sha256=receipt.sha256))
    assert result.stop_reason == "budget_exhausted" and result.answer is None
    assert result.harvest.receipt.judge_calls == 3
    assert not route.requests and not search.requests and not planner.requests


def test_changed_model_identity_cannot_reuse_old_judgments(tmp_path):
    from tests.test_intent_research import identity

    cfg = configured(tmp_path)
    first, _, _, _ = assemble(cfg)
    receipt = suspend(first)
    resumed, route, search, planner = assemble(cfg)
    planner.model = identity("new-planner")
    with pytest.raises(ValueError, match="original bound model"):
        asyncio.run(resumed.resume("research", checkpoint_sha256=receipt.sha256))
    assert not route.requests and not search.requests and not planner.requests


def test_rejected_review_is_retained_without_exposing_its_answer(tmp_path):
    cfg = configured(tmp_path)
    first, _, _, _ = assemble(cfg)
    receipt = suspend(first)
    resumed, _, _, _ = assemble(cfg)
    resumed._reviewer = ReviewerFixture(supported=False)
    result = asyncio.run(resumed.resume("research", checkpoint_sha256=receipt.sha256))
    assert result.status == "partial" and result.answer is None
    assert result.review is not None and not result.review.intent_covered
    assert all(claim.verdict == "unsupported" for claim in result.review.claims)


def test_checkpoints_are_private_and_bounded_and_backwards_time_refuses(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    first, _, _, _ = assemble(cfg)
    receipt = suspend(first)
    path = cfg.journal.directory / "research" / "checkpoint.json"
    assert path.stat().st_mode & 0o777 == 0o600
    saved = CheckpointStore(cfg, "research").read(receipt.sha256)
    monkeypatch.setattr("ghimera.research.time.time", lambda: saved.saved_at - 1)
    resumed, route, search, planner = assemble(cfg)
    with pytest.raises(ValueError, match="backwards"):
        asyncio.run(resumed.resume("research", checkpoint_sha256=receipt.sha256))
    assert not route.requests and not search.requests and not planner.requests
    small = GhimeraConfig.model_validate(
        dict(
            cfg.model_dump(),
            continuation=dict(cfg.continuation.model_dump(), max_checkpoint_bytes=1),
        )
    )
    with pytest.raises(GhimeraRefused, match="ledger_sink_failed"):
        CheckpointStore(small, "research").read(receipt.sha256)


def test_continuation_example_uses_the_typed_boundary():
    from ghimera.continuation_config import ContinuationConfig

    raw = tomllib.loads(Path("examples/continuation.toml").read_text())
    configured_policy = ContinuationConfig.model_validate(raw["continuation"])
    assert configured_policy.clock_policy == "include_downtime"
