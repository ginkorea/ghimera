"""Complete bounded review partitions; local fixtures are not model quality."""

import json

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.model_http import ModelHttpResponse
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_graph import validate_rows
from tests.test_semantic_graph import SemanticWire, document
from tests.test_semantic_grounding import GroundedWire, missing_mention
from tests.test_semantic_verification import reviewed_config, run_stage, with_planning


def batching_config(tmp_path, **updates):
    verification = dict(
        schema="ghimera.semantic-verification/4",
        model_role="reviewer",
        max_calls_per_run=8,
        max_coverage_findings=2,
        max_mentions_per_call=1,
        max_relations_per_call=1,
    )
    verification.update(updates)
    raw = reviewed_config(tmp_path, verification=verification).model_dump()
    raw["models"]["reviewer"]["response_format"] = "json_schema"
    return GhimeraConfig.model_validate(raw)


class BatchWire(GroundedWire):
    def __init__(self, bound, *, findings=(), fail_at=None, defect=None):
        super().__init__(bound)
        self.batch_findings, self.fail_at, self.batch_defect = findings, fail_at, defect

    async def post(self, body):
        packet = json.loads(json.loads(body)["messages"][1]["content"])
        selection = packet["semantic_review_selection"]
        self.findings = self.batch_findings if selection["coverage"] else ()
        response = await super().post(body)
        if self.fail_at == len(self.requests):
            return ModelHttpResponse(500, b"{}", "application/json")
        wire = json.loads(response.body)
        result = json.loads(wire["choices"][0]["message"]["content"])
        result["mentions"] = [
            item for item in result["mentions"] if item["key"] in selection["mention_keys"]
        ]
        result["relations"] = [
            item for item in result["relations"] if item["index"] in selection["relation_indices"]
        ]
        if not selection["coverage"]:
            result.update(coverage="uncertain", coverage_findings=[])
        if self.batch_defect == "invented" and result["mentions"]:
            result["mentions"][0]["key"] = "invented"
        if self.batch_defect == "coverage" and not selection["coverage"]:
            result["coverage"] = "adequate"
        wire["choices"][0]["message"]["content"] = json.dumps(result)
        return ModelHttpResponse(200, json.dumps(wire).encode(), "application/json")


def test_every_original_item_and_coverage_are_reviewed_once_and_replayed(tmp_path):
    cfg = batching_config(tmp_path)
    extraction, reviews = SemanticWire(cfg.models.analyst), BatchWire(cfg.models.reviewer)
    graph, rows, budget = run_stage(cfg, extraction, reviews, (document(),))
    window = rows[-1].semantic_window
    assert len(extraction.requests) == 1 and len(reviews.requests) == 4
    assert [packet["semantic_review_selection"] for _, packet in reviews.requests] == [
        dict(
            schema="ghimera.review-selection/1",
            mention_keys=["m1"],
            relation_indices=[],
            coverage=False,
        ),
        dict(
            schema="ghimera.review-selection/1",
            mention_keys=["m2"],
            relation_indices=[],
            coverage=False,
        ),
        dict(
            schema="ghimera.review-selection/1",
            mention_keys=[],
            relation_indices=[0],
            coverage=False,
        ),
        dict(
            schema="ghimera.review-selection/1", mention_keys=[], relation_indices=[], coverage=True
        ),
    ]
    packets = [packet for _, packet in reviews.requests]
    assert all(packet["semantic_proposal"] == packets[0]["semantic_proposal"] for packet in packets)
    assert all(packet["proposal_digest"] == window.proposal.content_digest() for packet in packets)
    assert window.review.schema_version == "ghimera.semantic-review/4"
    assert window.review.model_call == window.review.parts[-1].review.model_call
    assert len(window.review.parts) == 4 and len(window.entities) == 2
    assert any(edge.rule == "reports_to" for edge in graph.snapshot().edges)
    assert (
        budget.semantic_calls == 1 and budget.semantic_review_calls == 4 and budget.judge_calls == 5
    )
    assert [row.semantic_review_selection for row in rows[:-1]] == [
        part.selection for part in window.review.parts
    ]
    assert all(
        row.model_call.prompt_revision == "ghimera-semantic-verification/4" for row in rows[:-1]
    )
    assert type(window).model_validate_json(window.model_dump_json()) == window
    assert validate_rows(cfg, rows) == (rows[-1],)


@pytest.mark.parametrize("defect", ["invented", "coverage"])
def test_selected_assessments_cannot_be_substituted_or_claim_whole_coverage(tmp_path, defect):
    cfg = batching_config(tmp_path)
    wire = BatchWire(cfg.models.reviewer, defect=defect)
    with pytest.raises(GhimeraRefused):
        run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))
    assert len(wire.requests) == 1


def test_mismatched_partition_and_amputated_ledger_refuse(tmp_path):
    cfg = batching_config(tmp_path)
    _, rows, _ = run_stage(
        cfg, SemanticWire(cfg.models.analyst), BatchWire(cfg.models.reviewer), (document(),)
    )
    with pytest.raises(ValueError):
        validate_rows(cfg, rows[1:])
    raw = rows[-1].semantic_window.model_dump(by_alias=True)
    raw["review"]["parts"][0]["selection"]["mention_keys"] = ["m2"]
    with pytest.raises(ValidationError):
        type(rows[-1].semantic_window).model_validate(raw)


def test_coverage_findings_survive_into_graph_planning_without_becoming_entities(tmp_path):
    from ghimera.graph_planning import build_context, validate_context

    cfg = with_planning(batching_config(tmp_path))
    raw = cfg.model_dump()
    raw["research"]["max_model_input_chars"] = 20000
    cfg = GhimeraConfig.model_validate(raw)
    doc = document("甲委員會隸屬乙委員會。丙委員會發布報告。")
    graph, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        BatchWire(cfg.models.reviewer, findings=(missing_mention(doc),)),
        (doc,),
    )
    window = rows[-1].semantic_window
    context = build_context(cfg, rows)
    assert window.review.coverage == "incomplete"
    assert context.gaps[0].coverage_findings == window.review.coverage_findings
    assert (
        context.gaps[0].review_request_sha256
        == window.review.parts[-1].review.model_call.request_sha256
    )
    assert not any(node.label == "丙委員會" for node in graph.snapshot().nodes)
    validate_context(cfg, context, (doc,))


@pytest.mark.parametrize(
    "missing", ["max_mentions_per_call", "max_relations_per_call", "max_coverage_findings"]
)
def test_batching_requires_every_explicit_bound(tmp_path, missing):
    with pytest.raises(ValidationError):
        batching_config(tmp_path, **{missing: None})


def test_legacy_profile_cannot_acquire_batching_silently(tmp_path):
    with pytest.raises(ValidationError):
        batching_config(tmp_path, schema="ghimera.semantic-verification/3")


def test_failed_second_call_is_not_retried_or_projected(tmp_path):
    import asyncio
    import time

    from ghimera.budget import RunBudget
    from ghimera.graph import MemoryGraphSink, ResearchGraph
    from ghimera.ledger import Ledger
    from ghimera.model_client import SelfHostedModel
    from ghimera.semantic_graph import SemanticStage

    cfg = batching_config(tmp_path)
    wire = BatchWire(cfg.models.reviewer, fail_at=2)
    budget, ledger = RunBudget(cfg, time.monotonic), Ledger()
    graph = ResearchGraph(cfg.graph, "failed-batch", MemoryGraphSink())
    stage = SemanticStage(
        cfg,
        SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst)),
        reviewer=SelfHostedModel(cfg, cfg.models.reviewer, http=wire),
    )
    doc = document()

    async def run():
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
        before = graph.snapshot()
        with pytest.raises(GhimeraRefused, match="model_unavailable"):
            await stage.extract("map the organization", doc, identity, graph, budget, ledger)
        assert graph.snapshot() == before

    asyncio.run(run())
    assert len(wire.requests) == 2
    rows = ledger.snapshot()
    assert [row.event for row in rows] == ["semantic_review", "semantic_review", "semantic"]
    assert rows[0].semantic_review is not None and rows[1].semantic_review is None
    assert rows[1].model_call.status == 500 and rows[1].model_call.outcome == "refused"
    assert rows[2].model_call.task == "semantic_extract" and rows[2].model_call.outcome == "success"
    assert rows[2].semantic_window is None
    assert (
        budget.semantic_calls == 1 and budget.semantic_review_calls == 2 and budget.judge_calls == 3
    )
    assert validate_rows(cfg, rows) == (rows[-1],)


@pytest.mark.parametrize("limit", ["review", "judge"])
def test_whole_partition_budget_is_checked_before_any_review_call(tmp_path, limit):
    import asyncio
    import time

    from ghimera.budget import RunBudget
    from ghimera.graph import MemoryGraphSink, ResearchGraph
    from ghimera.ledger import Ledger
    from ghimera.model_client import SelfHostedModel
    from ghimera.semantic_graph import SemanticStage

    cfg = batching_config(tmp_path, max_calls_per_run=3 if limit == "review" else 8)
    if limit == "judge":
        cfg = GhimeraConfig.model_validate(dict(cfg.model_dump(), judge_budget=4))
    wire = BatchWire(cfg.models.reviewer)
    budget, ledger = RunBudget(cfg, time.monotonic), Ledger()
    graph = ResearchGraph(cfg.graph, "insufficient-batch-budget", MemoryGraphSink())
    stage = SemanticStage(
        cfg,
        SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst)),
        reviewer=SelfHostedModel(cfg, cfg.models.reviewer, http=wire),
    )
    doc = document()

    async def run():
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
        before = graph.snapshot()
        with pytest.raises(GhimeraRefused, match="budget_exhausted"):
            await stage.extract("map the organization", doc, identity, graph, budget, ledger)
        assert graph.snapshot() == before

    asyncio.run(run())
    assert not wire.requests and budget.semantic_review_calls == 0
    assert budget.semantic_calls == budget.judge_calls == 1
    rows = ledger.snapshot()
    assert len(rows) == 1 and rows[0].event == "semantic"
    assert rows[0].semantic_window is None and rows[0].refusal.value == "budget_exhausted"
    assert rows[0].model_call.task == "semantic_extract"
    assert rows[0].model_call.outcome == "success"
    assert validate_rows(cfg, rows) == rows


def test_late_cancellation_preserves_completed_and_cancelled_parts_without_projection(tmp_path):
    import asyncio
    import time

    from ghimera.budget import RunBudget
    from ghimera.graph import MemoryGraphSink, ResearchGraph
    from ghimera.ledger import Ledger
    from ghimera.model_client import SelfHostedModel
    from ghimera.semantic_graph import SemanticStage

    cfg = batching_config(tmp_path)

    async def run():
        ready = asyncio.Event()

        class BlockingWire(BatchWire):
            async def post(self, body):
                if self.requests:
                    ready.set()
                    await asyncio.Event().wait()
                return await super().post(body)

        wire = BlockingWire(cfg.models.reviewer)
        budget, ledger = RunBudget(cfg, time.monotonic), Ledger()
        graph = ResearchGraph(cfg.graph, "cancelled-batch", MemoryGraphSink())
        doc = document()
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
        before = graph.snapshot()
        stage = SemanticStage(
            cfg,
            SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst)),
            reviewer=SelfHostedModel(cfg, cfg.models.reviewer, http=wire),
        )
        task = asyncio.create_task(
            stage.extract("map the organization", doc, identity, graph, budget, ledger)
        )
        await asyncio.wait_for(ready.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert graph.snapshot() == before
        rows = ledger.snapshot()
        assert [row.event for row in rows] == ["semantic_review", "semantic_review", "semantic"]
        assert rows[0].semantic_review is not None and rows[0].model_call.outcome == "success"
        assert rows[1].semantic_review is None and rows[1].model_call.outcome == "cancelled"
        assert rows[1].semantic_review_selection.mention_keys == ("m2",)
        assert rows[2].semantic_window is None and rows[2].model_call.task == "semantic_extract"
        assert budget.semantic_review_calls == 2 and budget.judge_calls == 3
        assert validate_rows(cfg, rows) == (rows[-1],)

    asyncio.run(run())


def test_nonactive_example_has_explicit_partition_and_coverage_bounds():
    import tomllib
    from pathlib import Path

    from ghimera.semantic_types import SemanticConfig

    policy = SemanticConfig.model_validate(
        tomllib.loads(Path("examples/semantics-batched.toml").read_text())
    )
    assert policy.verification.schema_version == "ghimera.semantic-verification/4"
    assert policy.verification.max_mentions_per_call == 4
    assert policy.verification.max_relations_per_call == 2
    assert policy.verification.max_coverage_findings == 4


def test_generated_schema_has_exact_selected_sizes_keys_indices_and_no_partial_coverage(tmp_path):
    cfg = batching_config(tmp_path)
    wire = BatchWire(cfg.models.reviewer)
    run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))
    for request, packet in wire.requests:
        schema = request["response_format"]["json_schema"]["schema"]
        selection = packet["semantic_review_selection"]
        for field, values in (
            ("mentions", selection["mention_keys"]),
            ("relations", selection["relation_indices"]),
        ):
            assert schema["properties"][field]["minItems"] == len(values)
            assert schema["properties"][field]["maxItems"] == len(values)
        if not selection["coverage"]:
            assert schema["properties"]["coverage"]["enum"] == ["uncertain"]
            assert schema["properties"]["coverage_findings"]["maxItems"] == 0
        if selection["mention_keys"]:
            assert (
                schema["$defs"]["FactorizedMentionAssessment"]["properties"]["key"]["enum"]
                == selection["mention_keys"]
            )
        if selection["relation_indices"]:
            assert (
                schema["$defs"]["GroundedRelationAssessment"]["properties"]["index"]["enum"]
                == selection["relation_indices"]
            )


def test_assembled_loop_journal_archive_and_budget_restore_all_actual_calls(tmp_path):
    import asyncio
    import time

    from ghimera.budget import RunBudget
    from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
    from ghimera.fetch import FetchLadder
    from ghimera.journal import read_journal
    from ghimera.loop import GoalLoop
    from ghimera.model_client import SelfHostedModel
    from ghimera.models import Goal, Harvest, Scope
    from tests.test_graph_planning import NativeFixture, with_journal

    cfg = with_journal(batching_config(tmp_path), tmp_path)
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=NativeFixture(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        semantic_extractor=SelfHostedModel(
            cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst)
        ),
        semantic_reviewer=SelfHostedModel(
            cfg, cfg.models.reviewer, http=BatchWire(cfg.models.reviewer)
        ),
    )
    harvest = asyncio.run(
        loop.run(
            Goal(text="map the organization", seeds=("https://example.org/report",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
            run_id="batched-loop",
        )
    )
    assert Harvest.model_validate_json(harvest.model_dump_json(by_alias=True)) == harvest
    assert read_journal(cfg.journal, "batched-loop").rows == harvest.ledger
    assert harvest.receipt.judge_calls == 6
    budget = RunBudget(cfg, time.monotonic)
    budget.restore(harvest.receipt, harvest.ledger, search_calls=0, downtime_seconds=0)
    assert (
        budget.semantic_calls == 1 and budget.semantic_review_calls == 4 and budget.judge_calls == 6
    )
    altered = harvest.model_dump(mode="json", by_alias=True)
    semantic = next(row for row in altered["ledger"] if row["event"] == "semantic")
    semantic["semantic_window"]["review"]["parts"].pop(0)
    with pytest.raises(ValidationError):
        Harvest.model_validate(altered)
