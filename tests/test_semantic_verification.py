"""Independent semantic checks and quarantine; fixtures are not model accuracy."""

import asyncio
import json
import time

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.graph import MemoryGraphSink, ResearchGraph
from ghimera.graph_planning import build_context, validate_context
from ghimera.ledger import Ledger
from ghimera.model_client import SelfHostedModel
from ghimera.model_http import ModelHttpResponse
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_graph import SemanticStage, validate_rows
from tests.test_semantic_graph import SemanticWire, configured, document
from tests.test_served_models import service


def reviewed_config(tmp_path, **changes):
    raw = configured(tmp_path).model_dump(by_alias=True)
    raw["semantics"].update(
        schema="ghimera.semantics/4",
        prompt_profile="defined_ontology",
        role_definitions=[
            dict(
                name="entity",
                definition="A named organizational institution, not an ideology or population.",
            )
        ],
        relation_definitions=[
            dict(
                name="reports_to",
                definition=(
                    "The source explicitly states that the source institution "
                    "reports to the target institution."
                ),
            )
        ],
        verification=dict(
            schema="ghimera.semantic-verification/1", model_role="reviewer", max_calls_per_run=8
        ),
    )
    raw["semantics"].update(changes)
    raw["models"]["reviewer"] = service(
        8769, model_id="independent-fixture", served_model="independent-fixture"
    ).model_dump(by_alias=True)
    return GhimeraConfig.model_validate(raw)


class ReviewWire:
    def __init__(
        self,
        bound,
        *,
        rejected=None,
        relation_verdict="supported",
        coverage="adequate",
        defect=None,
    ):
        self.config = bound
        self.rejected, self.relation_verdict = rejected, relation_verdict
        self.coverage, self.defect, self.requests = coverage, defect, []

    async def post(self, body):
        request = json.loads(body)
        packet = json.loads(request["messages"][1]["content"])
        self.requests.append((request, packet))
        proposal = packet["semantic_proposal"]
        output = dict(
            proposal_digest=packet["proposal_digest"],
            mentions=[
                dict(
                    key=item["key"],
                    verdict="unsupported" if item["key"] == self.rejected else "supported",
                    reason="Independent fixture assessment.",
                )
                for item in proposal["mentions"]
            ],
            relations=[
                dict(
                    index=index,
                    verdict=self.relation_verdict,
                    reason="Independent fixture relation assessment.",
                )
                for index, _ in enumerate(proposal["relations"])
            ],
            coverage=self.coverage,
            coverage_reason="Fixture coverage assessment, not independent accuracy evidence.",
        )
        if self.defect == "digest":
            output["proposal_digest"] = "0" * 64
        if self.defect == "missing_key":
            output["mentions"].pop()
        if self.defect == "invented_key":
            output["mentions"].append(dict(key="invented", verdict="supported", reason="fixture"))
        if self.defect == "missing_relation":
            output["relations"] = []
        wire = dict(
            id="independent-fixture-call",
            model=self.config.served_model,
            choices=[
                dict(
                    index=0,
                    finish_reason="length" if self.defect == "truncated" else "stop",
                    message=dict(role="assistant", content=json.dumps(output)),
                )
            ],
            usage=dict(prompt_tokens=25, completion_tokens=15, total_tokens=40),
        )
        return ModelHttpResponse(200, json.dumps(wire).encode(), "application/json")


def run_stage(cfg, extraction_wire, review_wire, docs):
    budget, ledger = RunBudget(cfg, time.monotonic), Ledger()
    graph = ResearchGraph(cfg.graph, "reviewed-semantics", MemoryGraphSink())
    extractor = SelfHostedModel(cfg, cfg.models.analyst, http=extraction_wire)
    reviewer = SelfHostedModel(cfg, cfg.models.reviewer, http=review_wire)
    stage = SemanticStage(cfg, extractor, reviewer=reviewer)

    async def run():
        await graph.start("map the organization")
        for doc in docs:
            identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
            await stage.extract("map the organization", doc, identity, graph, budget, ledger)

    asyncio.run(run())
    return graph, ledger.snapshot(), budget


def test_defined_ontology_and_independent_check_reach_the_wire_and_replay(tmp_path):
    cfg = reviewed_config(tmp_path)
    extraction_wire, review_wire = SemanticWire(cfg.models.analyst), ReviewWire(cfg.models.reviewer)
    graph, rows, budget = run_stage(cfg, extraction_wire, review_wire, (document(),))
    assert [row.event for row in rows] == ["semantic_review", "semantic"]
    assert budget.semantic_calls == budget.semantic_review_calls == 1 and budget.judge_calls == 2
    window = rows[-1].semantic_window
    assert window.schema_version == "ghimera.semantic-window/2"
    assert window.review.model_call.service == cfg.models.reviewer
    assert window.review.model_call.task == "semantic_review"
    assert window.review.proposal_digest == window.proposal.content_digest()
    assert len(window.entities) == 2 and len(window.excluded_mentions) == 0
    assert any(edge.rule == "reports_to" for edge in graph.snapshot().edges)
    assert all(edge.claim_status == "model_asserted" for edge in window.edges)
    assert extraction_wire.requests[0]["semantic_recipe"]["role_definitions"] == [
        dict(
            name="entity",
            definition="A named organizational institution, not an ideology or population.",
        )
    ]
    request, packet = review_wire.requests[0]
    assert "model_call" not in packet["semantic_proposal"]
    assert packet["semantic_proposal"]["mentions"][0]["surface"] == "甲委員會"
    assert "untrusted DATA" in request["messages"][0]["content"]
    assert validate_rows(cfg, rows) == (rows[-1],)
    assert type(window).model_validate_json(window.model_dump_json()) == window


def test_rejected_mention_and_dependent_edge_are_quarantined_not_repaired(tmp_path):
    cfg = reviewed_config(tmp_path)
    graph, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        ReviewWire(cfg.models.reviewer, rejected="m2"),
        (document(),),
    )
    window = rows[-1].semantic_window
    assert [item.key for item in window.entities] == ["m1"]
    assert [item.key for item in window.proposal.mentions] == ["m1", "m2"]
    assert [(item.key, item.reason) for item in window.excluded_mentions] == [
        ("m2", "review_unsupported")
    ]
    assert [(item.index, item.reason) for item in window.excluded_relations] == [
        (0, "endpoint_quarantined")
    ]
    assert not any(edge.rule == "reports_to" for edge in graph.snapshot().edges)
    assert window.review.mentions[1].reason == "Independent fixture assessment."


def test_reviewer_cannot_rescue_a_missing_native_span(tmp_path):
    cfg = reviewed_config(tmp_path)
    _, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst, wrong_surface=True),
        ReviewWire(cfg.models.reviewer),
        (document(),),
    )
    window = rows[-1].semantic_window
    assert [item.key for item in window.entities] == ["m2"]
    assert window.proposal.mentions[0].surface == "錯誤"
    assert window.excluded_mentions[0].reason == "native_span_missing"
    assert window.review.mentions[0].verdict == "supported"


@pytest.mark.parametrize("verdict", ["unsupported", "ambiguous"])
def test_relation_check_quarantines_unsupported_or_ambiguous_claims(tmp_path, verdict):
    cfg = reviewed_config(tmp_path)
    _, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        ReviewWire(cfg.models.reviewer, relation_verdict=verdict),
        (document(),),
    )
    window = rows[-1].semantic_window
    assert len(window.entities) == 2
    assert window.excluded_relations[0].reason == "review_" + verdict
    assert not any(edge.rule == "reports_to" for edge in window.edges)


@pytest.mark.parametrize(
    "defect", ["digest", "missing_key", "invented_key", "missing_relation", "truncated"]
)
def test_invalid_independent_reply_refuses_before_projection(tmp_path, defect):
    cfg = reviewed_config(tmp_path)
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            SemanticWire(cfg.models.analyst),
            ReviewWire(cfg.models.reviewer, defect=defect),
            (document(),),
        )


def test_same_declared_model_or_incomplete_ontology_refuses_before_io(tmp_path):
    cfg = reviewed_config(tmp_path)
    raw = cfg.model_dump(by_alias=True)
    raw["models"]["reviewer"] = raw["models"]["analyst"]
    with pytest.raises(ValidationError, match="distinct"):
        GhimeraConfig.model_validate(raw)
    with pytest.raises(ValidationError):
        reviewed_config(tmp_path, role_definitions=[])
    with pytest.raises(ValidationError):
        reviewed_config(tmp_path, relation_definitions=[])
    with pytest.raises(ValidationError):
        reviewed_config(tmp_path, verification=None)
    with pytest.raises(ValidationError):
        reviewed_config(tmp_path, role_definitions=[dict(name="different", definition="fixture")])


def test_review_gaps_feed_planning_without_becoming_asserted_entities(tmp_path):
    cfg = reviewed_config(tmp_path)
    raw = cfg.model_dump(by_alias=True)
    from tests.test_intent_research import policy

    raw["research"] = policy()
    raw["research"]["graph_context"] = dict(
        schema="ghimera.graph-planning/2",
        entity_roles=["entity"],
        relation_rules=["reports_to"],
        selection="newest_first",
        max_entities=10,
        max_relations=10,
        max_gaps=4,
        max_evidence_chars=3000,
        max_context_chars=10000,
    )
    cfg = GhimeraConfig.model_validate(raw)
    doc = document()
    _, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        ReviewWire(cfg.models.reviewer, rejected="m2", coverage="incomplete"),
        (doc,),
    )
    context = build_context(cfg, rows)
    assert context.schema_version == "ghimera.planning-graph/2"
    assert len(context.gaps) == 1 and context.omitted_gaps == 0
    assert context.gaps[0].coverage == "incomplete"
    assert context.gaps[0].excluded_mentions == context.gaps[0].excluded_relations == 1
    assert context.gaps[0].id in context.references
    assert all(entity.node.label != "乙委員會" for entity in context.entities)
    validate_context(cfg, context, (doc,))


def test_missing_reviewer_port_cannot_silently_skip_verification(tmp_path):
    cfg = reviewed_config(tmp_path)
    with pytest.raises(ValueError, match="reviewer"):
        SemanticStage(
            cfg, SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst))
        )


def with_planning(cfg, **limits):
    from tests.test_intent_research import policy

    raw = cfg.model_dump()
    context = dict(
        schema="ghimera.graph-planning/2",
        entity_roles=["entity"],
        relation_rules=["reports_to"],
        selection="newest_first",
        max_entities=10,
        max_relations=10,
        max_gaps=4,
        max_evidence_chars=3000,
        max_context_chars=18000,
    )
    context.update(limits)
    raw["research"] = policy(graph_context=context)
    return GhimeraConfig.model_validate(raw)


def test_invalid_review_spend_is_recorded_separately_from_original_extraction(tmp_path):
    cfg = reviewed_config(tmp_path)
    graph, ledger, budget, doc = (
        ResearchGraph(cfg.graph, "failed-review", MemoryGraphSink()),
        Ledger(),
        RunBudget(cfg, time.monotonic),
        document(),
    )
    stage = SemanticStage(
        cfg,
        SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst)),
        reviewer=SelfHostedModel(
            cfg, cfg.models.reviewer, http=ReviewWire(cfg.models.reviewer, defect="digest")
        ),
    )

    async def run():
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
        before = graph.snapshot()
        with pytest.raises(GhimeraRefused):
            await stage.extract("map the organization", doc, identity, graph, budget, ledger)
        assert graph.snapshot() == before

    asyncio.run(run())
    review, extraction = ledger.snapshot()
    assert review.refusal and extraction.refusal
    assert review.model_call.task == "semantic_review" and review.model_call.outcome == "refused"
    assert (
        extraction.model_call.task == "semantic_extract"
        and extraction.model_call.outcome == "success"
    )
    assert budget.judge_calls == 2
    assert validate_rows(cfg, ledger.snapshot()) == (extraction,)


def test_review_budget_cannot_spend_or_disguise_the_extractor_call(tmp_path):
    cfg = reviewed_config(tmp_path)
    cfg = GhimeraConfig.model_validate(dict(cfg.model_dump(), judge_budget=1))
    wire = ReviewWire(cfg.models.reviewer)
    graph, ledger, budget, doc = (
        ResearchGraph(cfg.graph, "budget", MemoryGraphSink()),
        Ledger(),
        RunBudget(cfg, time.monotonic),
        document(),
    )
    stage = SemanticStage(
        cfg,
        SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst)),
        reviewer=SelfHostedModel(cfg, cfg.models.reviewer, http=wire),
    )

    async def run():
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
        with pytest.raises(GhimeraRefused):
            await stage.extract("map the organization", doc, identity, graph, budget, ledger)

    asyncio.run(run())
    assert budget.semantic_calls == budget.judge_calls == 1
    assert budget.semantic_review_calls == 0 and not wire.requests
    assert [row.event for row in ledger.snapshot()] == ["semantic"]
    assert (
        ledger.snapshot()[0].refusal and ledger.snapshot()[0].model_call.task == "semantic_extract"
    )


def test_cancelled_independent_call_keeps_both_provenances_and_no_projection(tmp_path):
    cfg = reviewed_config(tmp_path)

    async def run():
        ready = asyncio.Event()

        class BlockingWire(ReviewWire):
            async def post(self, body):
                ready.set()
                await asyncio.Event().wait()

        graph, ledger, budget, doc = (
            ResearchGraph(cfg.graph, "cancel-review", MemoryGraphSink()),
            Ledger(),
            RunBudget(cfg, time.monotonic),
            document(),
        )
        await graph.start("map the organization")
        identity = await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
        before = graph.snapshot()
        stage = SemanticStage(
            cfg,
            SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst)),
            reviewer=SelfHostedModel(
                cfg, cfg.models.reviewer, http=BlockingWire(cfg.models.reviewer)
            ),
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
        assert (
            rows[0].model_call.outcome == "cancelled"
            and rows[0].model_call.task == "semantic_review"
        )
        assert (
            rows[1].model_call.outcome == "success"
            and rows[1].model_call.task == "semantic_extract"
        )
        assert budget.judge_calls == 2
        validate_rows(cfg, rows)

    asyncio.run(run())


def test_projection_requires_earlier_actual_review_not_just_embedded_metadata(tmp_path):
    cfg = reviewed_config(tmp_path)
    _, rows, _ = run_stage(
        cfg, SemanticWire(cfg.models.analyst), ReviewWire(cfg.models.reviewer), (document(),)
    )
    with pytest.raises(ValueError, match="earlier review"):
        validate_rows(cfg, (rows[-1],))
    call = rows[0].model_call.model_copy(update={"service": cfg.models.analyst})
    with pytest.raises(ValueError, match="independent configured"):
        validate_rows(cfg, (rows[0].model_copy(update={"model_call": call}), rows[1]))


def test_reviewed_loop_archive_and_journal_reconcile_and_restore_spend(tmp_path):
    from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
    from ghimera.fetch import FetchLadder
    from ghimera.journal import read_journal
    from ghimera.loop import GoalLoop
    from ghimera.models import Goal, Harvest, Scope
    from tests.test_graph_planning import NativeFixture, with_journal

    cfg = with_journal(reviewed_config(tmp_path), tmp_path)
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
            cfg,
            cfg.models.reviewer,
            http=ReviewWire(cfg.models.reviewer, rejected="m2", coverage="incomplete"),
        ),
    )
    harvest = asyncio.run(
        loop.run(
            Goal(text="map the organization", seeds=("https://example.org/report",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
            run_id="reviewed-loop",
        )
    )
    assert len(harvest.source_documents) == 1
    assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
    assert read_journal(cfg.journal, "reviewed-loop").rows == harvest.ledger
    assert harvest.receipt.judge_calls == 3
    budget = RunBudget(cfg, time.monotonic)
    budget.restore(harvest.receipt, harvest.ledger, search_calls=0, downtime_seconds=0)
    assert budget.semantic_calls == budget.semantic_review_calls == 1 and budget.judge_calls == 3
    altered = harvest.model_dump()
    observed = next(row for row in altered["ledger"] if row["event"] == "semantic")
    observed["semantic_window"]["excluded_mentions"][0]["reason"] = "unknown_role"
    with pytest.raises(ValidationError):
        Harvest.model_validate(altered)


def test_gaps_have_bounded_population_and_can_motivate_a_real_planner_wire(tmp_path):
    from ghimera.research_types import PlanningRequest

    cfg = with_planning(reviewed_config(tmp_path))
    doc = document()
    _, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        ReviewWire(cfg.models.reviewer, coverage="incomplete"),
        (doc,),
    )
    context = build_context(cfg, rows)

    class GapPlanner(ReviewWire):
        async def post(self, body):
            request = json.loads(body)
            self.requests.append(request)
            packet = json.loads(request["messages"][1]["content"])
            output = dict(
                questions=[dict(id="q1", text="What source support is missing?")],
                queries=[
                    dict(
                        text="organization structure",
                        question_ids=["q1"],
                        graph_refs=[packet["graph_context"]["gaps"][0]["id"]],
                    )
                ],
            )
            return ModelHttpResponse(
                200,
                json.dumps(
                    dict(
                        id="gap-fixture",
                        model=self.config.served_model,
                        choices=[
                            dict(
                                index=0,
                                finish_reason="stop",
                                message=dict(role="assistant", content=json.dumps(output)),
                            )
                        ],
                        usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20),
                    )
                ).encode(),
                "application/json",
            )

    wire = GapPlanner(cfg.models.planner)
    model = SelfHostedModel(cfg, cfg.models.planner, http=wire)
    result = asyncio.run(
        model.plan(
            PlanningRequest(
                intent="map the organization",
                questions=(),
                documents=(doc,),
                assessment=None,
                max_questions=4,
                max_queries=2,
                max_query_chars=200,
                graph_context=context,
            )
        )
    )
    assert result.queries[0].graph_refs == (context.gaps[0].id,)
    assert result.model_call.prompt_revision == "ghimera-graph-planning/2"
    assert "not new factual entities" in wire.requests[0]["messages"][0]["content"]

    _, more_rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        ReviewWire(cfg.models.reviewer, coverage="incomplete"),
        (doc, document(url="https://example.org/other")),
    )
    limited = cfg.model_dump()
    limited["research"]["graph_context"]["max_gaps"] = 1
    limited = GhimeraConfig.model_validate(limited)
    view = build_context(limited, more_rows)
    assert len(view.gaps) == view.omitted_gaps == 1
    assert view.gaps[0].source.source_url == "https://example.org/other"


def test_oversized_proposal_refuses_without_spending_an_independent_call(tmp_path):
    cfg = reviewed_config(tmp_path, max_mentions_per_window=1)
    wire = ReviewWire(cfg.models.reviewer)
    with pytest.raises(GhimeraRefused):
        run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))
    assert not wire.requests


def test_nonactive_verified_and_gap_examples_parse_explicit_limits():
    import tomllib
    from pathlib import Path

    from ghimera.graph_planning_types import GraphPlanningConfig
    from ghimera.semantic_types import SemanticConfig

    semantic = SemanticConfig.model_validate(
        tomllib.loads(Path("examples/semantics-verified.toml").read_text())
    )
    planning = GraphPlanningConfig.model_validate(
        tomllib.loads(Path("examples/graph-planning-gaps.toml").read_text())["research"][
            "graph_context"
        ]
    )
    assert (
        semantic.verification.model_role == "reviewer"
        and semantic.verification.max_calls_per_run == 256
    )
    assert planning.max_gaps == 8
