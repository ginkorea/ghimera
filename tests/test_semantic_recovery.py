"""Failed windows remain gaps; controlled responses do not prove model quality."""

import asyncio
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.continuation import CheckpointStore, ResearchSuspended
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.graph import MemoryGraphSink
from ghimera.graph_planning import build_context, validate_context
from ghimera.loop import GoalLoop
from ghimera.model_client import SelfHostedModel
from ghimera.models import Extracted, Goal, Harvest, LinkCandidate, Page
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research import ResearchLoop
from ghimera.research_types import ResearchRequest
from ghimera.semantic_graph import validate_rows
from ghimera.semantic_types import SemanticFailurePolicy
from tests.test_c0 import scope
from tests.test_intent_research import AnalystFixture, ReviewerFixture, SearchFixture, policy
from tests.test_research_continuation import FollowupPlanner
from tests.test_semantic_graph import SemanticWire, document
from tests.test_semantic_verification import ReviewWire, reviewed_config, run_stage


def recovery_config(tmp_path, *, limit=2, research=False, **changes):
    raw = reviewed_config(tmp_path).model_dump(by_alias=True)
    raw["semantics"]["failure"] = dict(
        schema="ghimera.semantic-failure-policy/1",
        action="record_gap",
        allowed_refusals=["semantic_extraction_failed", "model_unavailable"],
        max_failed_windows_per_run=limit,
    )
    raw["semantics"].update(changes)
    if research:
        raw["research"] = policy(
            require_distinct_reviewer=False,
            max_model_input_chars=30000,
            graph_context=dict(
                schema="ghimera.graph-planning/4",
                entity_roles=["entity"],
                relation_rules=["reports_to"],
                selection="newest_first",
                max_entities=8,
                max_relations=4,
                max_evidence_chars=4000,
                max_context_chars=18000,
                max_gaps=4,
            ),
        )
    return GhimeraConfig.model_validate(raw)


@pytest.mark.parametrize("defect", ["digest", "missing_relation", "truncated"])
def test_review_failure_retains_proposal_and_calls_without_projecting(tmp_path, defect):
    cfg = recovery_config(tmp_path, research=True)
    doc = document()
    extractor, reviewer = (
        SemanticWire(cfg.models.analyst),
        ReviewWire(cfg.models.reviewer, defect=defect),
    )
    graph, rows, budget = run_stage(cfg, extractor, reviewer, (doc,))
    failed = rows[-1].semantic_refusal
    assert failed.phase == "review" and failed.continued
    assert failed.proposal.model_call == rows[-1].model_call
    assert failed.proposal.mentions[0].surface == "甲委員會"
    assert failed.review_sequences == (rows[0].sequence,)
    assert rows[0].refusal is not None and rows[0].semantic_review is None
    assert budget.judge_calls == 2 and budget.semantic_review_calls == 1
    assert not any(edge.claim_status for edge in graph.snapshot().edges)
    assert validate_rows(cfg, rows) == (rows[-1],)
    context = build_context(cfg, rows)
    gap = context.gaps[0]
    assert context.prompt_revision == "ghimera-graph-planning/4"
    assert gap.kind == "semantic_refusal" and gap.phase == "review"
    assert gap.proposal_digest == failed.proposal.content_digest()
    assert "coverage" not in gap.model_dump()
    assert gap.id in context.references
    assert not context.entities and not context.relations
    validate_context(cfg, context, (doc,))
    assert context == type(context).model_validate_json(context.model_dump_json())


def test_failure_limit_stops_before_unbounded_skipping_and_replay_enforces_it(tmp_path):
    cfg = recovery_config(tmp_path, limit=1)
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            SemanticWire(cfg.models.analyst),
            ReviewWire(cfg.models.reviewer, defect="digest"),
            (document(), document(url="https://example.org/second")),
        )
    _, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        ReviewWire(cfg.models.reviewer, defect="digest"),
        (document(),),
    )
    forged = rows[-1].model_copy(
        update={
            "semantic_refusal": rows[-1].semantic_refusal.model_copy(
                update={"policy_digest": "0" * 64}
            )
        }
    )
    with pytest.raises(ValueError, match="failure policy"):
        validate_rows(cfg, (rows[0], forged))


class FirstReviewFails(ReviewWire):
    async def post(self, body):
        self.defect = "digest" if not self.requests else None
        return await super().post(body)


def test_success_after_failed_native_window_is_not_reordered_as_first_window(tmp_path):
    text = document().extracted.text
    cfg = recovery_config(tmp_path, window_chars=len(text))
    graph, rows, budget = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        FirstReviewFails(cfg.models.reviewer),
        (document(text=text * 2),),
    )
    attempts = validate_rows(cfg, rows)
    assert attempts[0].semantic_refusal.start == 0
    assert attempts[1].semantic_window.start == len(text)
    assert budget.semantic_calls == 2 and budget.semantic_review_calls == 2
    assert sum(edge.rule == "reports_to" for edge in graph.snapshot().edges) == 1


@pytest.mark.parametrize("defect", ["forbidden_code", "limit", "unknown", "legacy"])
def test_failure_policy_is_explicit_bounded_and_never_includes_storage(tmp_path, defect):
    raw = recovery_config(tmp_path).model_dump(by_alias=True)
    if defect == "forbidden_code":
        raw["semantics"]["failure"]["allowed_refusals"] = ["graph_sink_failed"]
    elif defect == "limit":
        raw["semantics"]["failure"]["max_failed_windows_per_run"] = 9
    elif defect == "unknown":
        raw["semantics"]["failure"]["retry_forever"] = True
    else:
        raw["semantics"]["schema"] = "ghimera.semantics/1"
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)


def test_research_requires_failure_aware_view_instead_of_silent_skipping(tmp_path):
    cfg = recovery_config(tmp_path, research=True)
    for version in (1, 2, 3):
        raw = cfg.model_dump(by_alias=True)
        raw["research"]["graph_context"]["schema"] = f"ghimera.graph-planning/{version}"
        with pytest.raises(ValidationError):
            GhimeraConfig.model_validate(raw)


class NativeFixtureRoute(FakeRoute):
    async def attempt(self, request):
        self.requests.append(request)
        return Page(
            url=request.url,
            final_url=request.url,
            status=200,
            content_type="text/html",
            body=document().raw + request.url.encode(),
        )


class NativeFixtureExtractor(FakeExtractor):
    async def extract(self, page):
        return Extracted(
            title="fixture",
            text=page.body.decode(),
            language="zh",
            links=(LinkCandidate(url=page.url + "/next", anchor="organization"),),
        )


def assemble(cfg, *, failed=True, sink=None):
    route = NativeFixtureRoute()
    extraction = SemanticWire(cfg.models.analyst)
    review = ReviewWire(cfg.models.reviewer, defect="digest" if failed else None)
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route,)),
        extractor=NativeFixtureExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        semantic_extractor=SelfHostedModel(cfg, cfg.models.analyst, http=extraction),
        semantic_reviewer=SelfHostedModel(cfg, cfg.models.reviewer, http=review),
        graph_sink=sink,
    )
    return loop, route


def test_collector_follows_frontier_and_restores_completed_gap_without_retry(tmp_path):
    cfg = recovery_config(tmp_path)
    loop, route = assemble(cfg)

    async def collect():
        session = await loop.open(Goal(text="organization"), run_id="recovery")
        stop = await loop.collect(
            session, scope(), ("https://example.org/report",), fetch_limit=1, allow_grade=False
        )
        state = session.checkpoint_state()
        harvest = loop.finish(session, stop="frontier_empty")
        return stop, state, harvest

    stop, state, harvest = asyncio.run(collect())
    assert stop == "round_limit" and len(route.requests) == 1
    assert state.frontier and state.semantic_sources
    assert harvest == Harvest.model_validate_json(harvest.model_dump_json())

    # Restore with the original acknowledged graph and budget, not a new model call.
    async def restore():
        fresh, _ = assemble(cfg)
        restored = await fresh.open(harvest.goal, run_id="restored")
        restored.restore_state(state, harvest)
        return restored.checkpoint_state()

    assert asyncio.run(restore()).semantic_sources == state.semantic_sources
    altered = harvest.model_dump(by_alias=True)
    failed_row = next(row for row in altered["ledger"] if row.get("semantic_refusal"))
    failed_row["semantic_refusal"]["document_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        Harvest.model_validate(altered)


def test_run_budget_exhaustion_remains_terminal_under_failure_policy(tmp_path):
    raw = recovery_config(tmp_path).model_dump(by_alias=True)
    raw["judge_budget"] = 1
    cfg = GhimeraConfig.model_validate(raw)
    loop, _ = assemble(cfg)
    result = asyncio.run(
        loop.run(
            Goal(text="organization", seeds=("https://example.org/a",)), scope(), run_id="budget"
        )
    )
    assert result.receipt.stop_reason == "budget_exhausted"
    assert not any(row.semantic_refusal and row.semantic_refusal.continued for row in result.ledger)


def test_prior_recipe_still_skips_failed_source_followups(tmp_path):
    cfg = reviewed_config(tmp_path)
    loop, route = assemble(cfg)
    result = asyncio.run(
        loop.run(
            Goal(text="organization", seeds=("https://example.org/a",)), scope(), run_id="legacy"
        )
    )
    assert result.receipt.stop_reason == "frontier_empty" and len(route.requests) == 1
    assert any(row.refusal is not None and row.event == "semantic" for row in result.ledger)
    assert all(row.semantic_refusal is None for row in result.ledger)
    assert "failure" not in cfg.semantics.model_dump(by_alias=True)


def test_nonactive_policy_fragment_is_valid_and_does_not_enable_itself():
    raw = tomllib.loads(Path("examples/semantic-failure-policy.toml").read_text())
    recipe = SemanticFailurePolicy.model_validate(raw["semantics"]["failure"])
    assert recipe.action == "record_gap" and recipe.max_failed_windows_per_run == 2


def test_completed_round_resume_keeps_gap_spend_and_failure_allowance(tmp_path):
    raw = recovery_config(tmp_path, research=True, limit=1).model_dump(by_alias=True)
    raw["journal"] = dict(
        schema="chimera.run-journal-config/1",
        directory=str(tmp_path / "journal"),
        max_record_bytes=2000000,
        max_journal_bytes=10000000,
        max_summary_bytes=2000000,
        max_records=2000,
    )
    raw["continuation"] = dict(
        schema="ghimera.continuation/1",
        max_checkpoint_bytes=4000000,
        clock_policy="include_downtime",
    )
    raw["research"].update(max_pages_per_round=1, max_depth=3)
    cfg = GhimeraConfig.model_validate(raw)

    def research():
        collector, route = assemble(cfg)
        planner = FollowupPlanner()
        return (
            ResearchLoop(
                config=cfg,
                collector=collector,
                search=SearchFixture(),
                planner=planner,
                analyst=AnalystFixture(missing=True),
                reviewer=ReviewerFixture(),
            ),
            route,
            planner,
        )

    first, route, _ = research()
    with pytest.raises(ResearchSuspended) as suspended:
        asyncio.run(
            first.run(
                ResearchRequest(intent="organization"), run_id="resume-gap", suspend_after_rounds=1
            )
        )
    receipt = suspended.value.receipt
    checkpoint = CheckpointStore(cfg, "resume-gap").read(receipt.sha256)
    before = checkpoint.progress.harvest
    assert checkpoint.next_action == "plan" and checkpoint.session.semantic_sources
    assert len(route.requests) == 1
    assert (
        sum(
            row.semantic_refusal is not None and row.semantic_refusal.continued
            for row in before.ledger
        )
        == 1
    )
    resumed, new_route, planner = research()
    result = asyncio.run(resumed.resume("resume-gap", checkpoint_sha256=receipt.sha256))
    assert planner.requests[0].graph_context.gaps[0].kind == "semantic_refusal"
    assert len(new_route.requests) == 1 and new_route.requests[0].url != route.requests[0].url
    assert result.status == "failed"  # The next failed window exceeds the original cap.
    failures = [row.semantic_refusal for row in result.harvest.ledger if row.semantic_refusal]
    assert [item.continued for item in failures] == [True, False]
    assert result.harvest.receipt.judge_calls > before.receipt.judge_calls
    assert result.harvest.ledger[: len(before.ledger)] == before.ledger


class FailingSemanticSink(MemoryGraphSink):
    async def append(self, batch):
        if any(edge.claim_status is not None for edge in batch.edges):
            raise GhimeraRefused(RefusalCode.GRAPH_SINK_FAILED)
        return await super().append(batch)


def test_storage_failure_is_not_a_recoverable_model_gap(tmp_path):
    cfg = recovery_config(tmp_path)
    loop, _ = assemble(cfg, failed=False, sink=FailingSemanticSink())

    async def refused():
        session = await loop.open(Goal(text="organization"), run_id="storage")
        with pytest.raises(GhimeraRefused, match="graph_sink_failed"):
            await loop.collect(session, scope(), ("https://example.org/a",), allow_grade=False)
        return session.ledger.snapshot()

    failure = next(row for row in asyncio.run(refused()) if row.semantic_refusal)
    assert failure.refusal == RefusalCode.GRAPH_SINK_FAILED
    assert failure.semantic_refusal.phase == "projection"
    assert not failure.semantic_refusal.continued


class RepeatedNativeRoute(NativeFixtureRoute):
    async def attempt(self, request):
        page = await super().attempt(request)
        return page.model_copy(update={"body": document().raw * 2 + request.url.encode()})


def test_full_archive_keeps_successful_window_after_gap_at_original_offsets(tmp_path):
    cfg = recovery_config(
        tmp_path, window_chars=len(document().extracted.text), max_windows_per_document=2
    )
    collector = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((RepeatedNativeRoute(),)),
        extractor=NativeFixtureExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        semantic_extractor=SelfHostedModel(
            cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst)
        ),
        semantic_reviewer=SelfHostedModel(
            cfg, cfg.models.reviewer, http=FirstReviewFails(cfg.models.reviewer)
        ),
    )

    async def collect():
        session = await collector.open(Goal(text="organization"), run_id="mixed-windows")
        await collector.collect(
            session, scope(), ("https://example.org/a",), fetch_limit=1, allow_grade=False
        )
        return collector.finish(session, "frontier_empty")

    result = asyncio.run(collect())
    assert result == Harvest.model_validate_json(result.model_dump_json())
    rows = validate_rows(cfg, result.ledger)
    assert rows[0].semantic_refusal.start == 0 and rows[0].semantic_refusal.continued
    assert rows[1].semantic_window.start == len(document().extracted.text)
    assert rows[1].semantic_window.omitted_chars == len("https://example.org/a")
    assert len([edge for edge in result.graph.edges if edge.rule == "reports_to"]) == 1
