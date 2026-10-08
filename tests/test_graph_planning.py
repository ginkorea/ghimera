"""Graph-aware follow-up planning; controlled responses are not model accuracy."""

import asyncio
import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.graph_planning import build_context, validate_context
from ghimera.graph_planning_types import GraphPlanningConfig
from ghimera.journal import read_journal
from ghimera.loop import GoalLoop
from ghimera.model_client import SelfHostedModel, model_schema
from ghimera.model_http import ModelHttpResponse
from ghimera.models import Extracted
from ghimera.refusals import GhimeraRefused, ModelFailure
from ghimera.research import ResearchLoop
from ghimera.research_types import (
    PlanningRequest,
    Question,
    ResearchPlan,
    ResearchRequest,
    ResearchResult,
    SearchQuery,
)
from tests.test_intent_research import (
    AnalystFixture,
    ReviewerFixture,
    SearchFixture,
    identity,
    policy,
)
from tests.test_semantic_graph import SemanticWire, configured, document, exercise


def planning_config(tmp_path, **limits):
    raw = configured(tmp_path).model_dump()
    context = dict(
        schema="ghimera.graph-planning/1",
        entity_roles=["entity"],
        relation_rules=["reports_to"],
        selection="newest_first",
        max_entities=8,
        max_relations=4,
        max_evidence_chars=4000,
        max_context_chars=18000,
    )
    context.update(limits)
    raw["research"] = policy(
        require_distinct_reviewer=False,
        max_model_input_chars=30000,
        graph_context=context,
    )
    return GhimeraConfig.model_validate(raw)


def observed(cfg, docs=None):
    return exercise(cfg, SemanticWire(cfg.models.analyst), docs or (document(),))


def test_context_is_native_assertions_with_closed_endpoints_and_exact_omissions(tmp_path):
    cfg = planning_config(tmp_path, max_entities=2, max_relations=1)
    docs = (document(), document(url="https://example.org/second"))
    _, rows, _ = observed(cfg, docs)
    context = build_context(cfg, rows)
    assert context.omitted_entities == 2 and context.omitted_relations == 1
    assert len(context.entities) == 2 and len(context.relations) == 1
    assert context.relations[0].claim_status == "model_asserted"
    assert {entity.node.id for entity in context.entities} == {
        context.relations[0].source,
        context.relations[0].target,
    }
    assert {source.source_url for source in context.sources} == {docs[1].url}
    assert context.omitted_evidence_chars > 0
    validate_context(cfg, context, docs)
    assert context == type(context).model_validate_json(context.model_dump_json())


def test_graph_context_caps_do_not_slice_or_fabricate_native_quotes(tmp_path):
    cfg = planning_config(tmp_path, max_evidence_chars=1)
    _, rows, _ = observed(cfg)
    context = build_context(cfg, rows)
    assert context.entities == context.relations == context.sources == ()
    assert context.omitted_entities == 2 and context.omitted_relations == 1
    assert context.omitted_evidence_chars > 1


def test_configured_graph_planning_requires_the_extraction_ontology(tmp_path):
    cfg = planning_config(tmp_path)
    raw = cfg.model_dump()
    raw["research"]["graph_context"]["relation_rules"] = ["invented"]
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)
    raw = cfg.model_dump()
    raw.pop("semantics")
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)


class PlanningWire:
    def __init__(self, bound):
        self.config, self.requests = bound, []

    async def post(self, body):
        request = json.loads(body)
        packet = json.loads(request["messages"][1]["content"])
        self.requests.append(packet)
        entity = packet["graph_context"]["entities"][0]["node"]
        output = ResearchPlan(
            questions=(Question(id="q1", text="Which parent organization?"),),
            queries=(
                SearchQuery(
                    text=entity["label"] + " 上級組織",
                    question_ids=("q1",),
                    graph_refs=(entity["id"],),
                ),
            ),
        ).model_dump(exclude={"model_call"})
        reply = dict(
            id="fixture",
            model="fixture-model",
            usage=dict(prompt_tokens=20, completion_tokens=10, total_tokens=30),
            choices=[
                dict(
                    index=0,
                    finish_reason="stop",
                    message=dict(role="assistant", content=json.dumps(output)),
                )
            ],
        )
        return ModelHttpResponse(200, json.dumps(reply).encode(), "application/json")


def test_concrete_model_port_uses_graph_context_to_emit_bound_followup_queries(tmp_path):
    cfg = planning_config(tmp_path)
    doc = document()
    _, rows, _ = observed(cfg, (doc,))
    context = build_context(cfg, rows)
    request = PlanningRequest(
        intent="map the organization",
        questions=(),
        documents=(doc,),
        assessment=None,
        max_questions=4,
        max_queries=2,
        max_query_chars=200,
        graph_context=context,
    )
    wire = PlanningWire(cfg.models.planner)
    model = SelfHostedModel(cfg, cfg.models.planner, http=wire)
    plan = asyncio.run(model.plan(request))
    assert plan.queries[0].graph_refs == (context.entities[0].node.id,)
    assert context.entities[0].node.label in plan.queries[0].text
    assert plan.model_call.prompt_revision == "ghimera-graph-planning/1"
    assert wire.requests[0]["graph_context"]["population_digest"] == context.population_digest


def test_model_invented_graph_reference_is_refused_with_nonsecret_native_diagnostics(tmp_path):
    cfg = planning_config(tmp_path)
    doc = document()
    _, rows, _ = observed(cfg, (doc,))
    context = build_context(cfg, rows)

    class UnboundPlanningWire(PlanningWire):
        async def post(self, body):
            response = await super().post(body)
            envelope = json.loads(response.body)
            output = json.loads(envelope["choices"][0]["message"]["content"])
            output["queries"][0]["graph_refs"] = ["private-invented-graph-reference"]
            envelope["choices"][0]["message"]["content"] = json.dumps(output)
            return ModelHttpResponse(200, json.dumps(envelope).encode(), "application/json")

    request = PlanningRequest(
        intent="map the organization",
        questions=(),
        documents=(doc,),
        assessment=None,
        max_questions=4,
        max_queries=2,
        max_query_chars=200,
        graph_context=context,
    )
    wire = UnboundPlanningWire(cfg.models.planner)
    with pytest.raises(ModelFailure, match="adapter_contract") as refused:
        asyncio.run(SelfHostedModel(cfg, cfg.models.planner, http=wire).plan(request))
    call = refused.value.model_call
    assert call.output_contract_failure.reason == "unbound_graph_reference"
    assert call.status == 200 and call.outcome == "refused"
    assert call.completion.finish_reason == "stop" and call.completion.model_matches
    assert call.usage.total_tokens == 30 and len(wire.requests) == 1
    assert "private-invented-graph-reference" not in call.model_dump_json()
    assert type(call).model_validate_json(call.model_dump_json()) == call


def test_tampered_native_graph_context_refuses_before_model_io(tmp_path):
    cfg = planning_config(tmp_path)
    doc = document()
    _, rows, _ = observed(cfg)
    context = build_context(cfg, rows)
    entity = context.entities[0]
    bad = context.model_copy(
        update={
            "entities": (
                entity.model_copy(
                    update={"evidence": entity.evidence.model_copy(update={"quote": "invented"})}
                ),
                *context.entities[1:],
            )
        }
    )
    request = PlanningRequest(
        intent="map",
        questions=(),
        documents=(doc,),
        assessment=None,
        max_questions=4,
        max_queries=2,
        max_query_chars=200,
        graph_context=bad,
    )
    wire = PlanningWire(cfg.models.planner)
    with pytest.raises(GhimeraRefused):
        asyncio.run(SelfHostedModel(cfg, cfg.models.planner, http=wire).plan(request))
    assert wire.requests == []


class NativeFixture(FakeExtractor):
    async def extract(self, page):
        return Extracted(
            title="controlled native source", language="zh", text="甲委員會隸屬乙委員會。"
        )


class GraphPlannerFixture:
    model = identity("graph-planner")

    def __init__(self, *, invent_reference=False):
        self.requests, self.invent_reference = [], invent_reference

    async def plan(self, request):
        self.requests.append(request)
        context = request.graph_context
        if context.entities:
            entity = context.entities[0]
            query = SearchQuery(
                text=entity.node.label + " 上級組織",
                question_ids=("q1",),
                graph_refs=("invented" if self.invent_reference else entity.node.id,),
            )
        else:
            query = SearchQuery(text="organization structure", question_ids=("q1",))
        return ResearchPlan(
            questions=request.questions or (Question(id="q1", text="Which parent organization?"),),
            queries=(query,),
        )


def research(tmp_path, *, invent_reference=False, journal=False):
    cfg = planning_config(tmp_path)
    if journal:
        cfg = with_journal(cfg, tmp_path)
    planner, search = GraphPlannerFixture(invent_reference=invent_reference), SearchFixture()
    collector = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=NativeFixture(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        semantic_extractor=SelfHostedModel(
            cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst)
        ),
    )
    loop = ResearchLoop(
        config=cfg,
        collector=collector,
        search=search,
        planner=planner,
        analyst=AnalystFixture(missing=True),
        reviewer=ReviewerFixture(),
    )
    return (
        asyncio.run(
            loop.run(ResearchRequest(intent="map the organization"), run_id="graph-planning")
        ),
        planner,
        search,
    )


def test_real_research_loop_uses_new_graph_findings_in_the_next_discovery_round(tmp_path):
    result, planner, search = research(tmp_path)
    assert result.status == "partial" and result.stop_reason == "rounds_exhausted"
    assert not planner.requests[0].graph_context.entities
    assert len(planner.requests[1].graph_context.entities) == 2
    assert len(planner.requests[1].graph_context.relations) == 1
    assert search.requests[1].query.graph_refs
    assert search.requests[0].query.text != search.requests[1].query.text
    plans = [row for row in result.harvest.ledger if row.event == "plan"]
    assert all(row.planning_graph is not None for row in plans)
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result
    raw = result.model_dump()
    plan = next(
        row for row in raw["harvest"]["ledger"] if row.get("planning_graph", {}).get("entities")
    )
    plan["planning_graph"]["omitted_entities"] += 1
    with pytest.raises(ValidationError, match="replay exactly"):
        ResearchResult.model_validate(raw)


def test_invented_graph_reference_stops_before_followup_search(tmp_path):
    result, planner, search = research(tmp_path, invent_reference=True)
    assert result.status == "failed"
    assert len(planner.requests) == 2 and len(search.requests) == 1
    assert result.answer is None


def test_serialized_context_is_bounded_even_when_entity_count_allows_more(tmp_path):
    cfg = planning_config(tmp_path, max_context_chars=700)
    _, rows, _ = observed(cfg)
    context = build_context(cfg, rows)
    assert len(context.model_dump_json()) <= 700
    assert context.entities == context.relations == ()
    assert context.omitted_entities == 2 and context.omitted_relations == 1


def test_unconfigured_recipe_preserves_existing_query_serialization():
    query = SearchQuery(text="organization", question_ids=("q1",))
    assert "graph_refs" not in query.model_dump()
    assert "graph_refs" not in model_schema(ResearchPlan)["$defs"]["SearchQuery"]["properties"]
    assert (
        "graph_refs"
        in model_schema(ResearchPlan, graph_planning=True)["$defs"]["SearchQuery"]["properties"]
    )


def test_wrong_model_role_cannot_send_graph_context(tmp_path):
    raw = planning_config(tmp_path).model_dump()
    raw["models"]["planner"]["revision"] = "different-planner"
    cfg = GhimeraConfig.model_validate(raw)
    wire = PlanningWire(cfg.models.analyst)
    request = PlanningRequest(
        intent="map",
        questions=(),
        documents=(),
        assessment=None,
        max_questions=4,
        max_queries=2,
        max_query_chars=200,
        graph_context=build_context(cfg, ()),
    )
    with pytest.raises(GhimeraRefused, match="research_contract"):
        asyncio.run(SelfHostedModel(cfg, cfg.models.analyst, http=wire).plan(request))
    assert wire.requests == []


def with_journal(cfg, tmp_path):
    raw = cfg.model_dump()
    raw["journal"] = dict(
        schema="chimera.run-journal-config/1",
        directory=str(tmp_path / "journal"),
        max_record_bytes=1_000_000,
        max_journal_bytes=8_000_000,
        max_summary_bytes=1_000_000,
        max_records=1000,
    )
    return GhimeraConfig.model_validate(raw)


def test_planning_views_replay_from_the_durable_journal_not_future_findings(tmp_path):
    result, _, _ = research(tmp_path, journal=True)
    cfg = result.harvest.receipt.effective_config
    report = read_journal(cfg.journal, "graph-planning")
    assert report.rows == result.harvest.ledger and report.summary is not None
    raw = result.model_dump()
    plans = [row for row in raw["harvest"]["ledger"] if row["event"] == "plan"]
    plans[0]["planning_graph"] = plans[1]["planning_graph"]
    with pytest.raises(ValidationError, match="replay exactly"):
        ResearchResult.model_validate(raw)


def test_cancelled_actual_planner_call_keeps_the_unsealed_graph_view(tmp_path):
    cfg = with_journal(planning_config(tmp_path), tmp_path)

    async def run():
        ready = asyncio.Event()

        class BlockingWire(PlanningWire):
            async def post(self, body):
                self.requests.append(json.loads(body))
                ready.set()
                await asyncio.Event().wait()

        wire = BlockingWire(cfg.models.planner)
        collector = GoalLoop(
            config=cfg,
            fetcher=FetchLadder((FakeRoute(),)),
            extractor=NativeFixture(),
            scorer=KeywordScorer(),
            judge=FakeJudge(),
            semantic_extractor=SelfHostedModel(
                cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst)
            ),
        )
        loop = ResearchLoop(
            config=cfg,
            collector=collector,
            search=SearchFixture(),
            planner=SelfHostedModel(cfg, cfg.models.planner, http=wire),
            analyst=AnalystFixture(missing=True),
            reviewer=ReviewerFixture(),
        )
        task = asyncio.create_task(
            loop.run(ResearchRequest(intent="map"), run_id="planning-cancelled")
        )
        async with asyncio.timeout(3):
            await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return wire

    wire = asyncio.run(run())
    report = read_journal(cfg.journal, "planning-cancelled")
    assert report.summary is None and len(wire.requests) == 1
    row = next(row for row in report.rows if row.event == "plan")
    assert row.refusal and row.model_call.outcome == "cancelled"
    assert row.model_call.prompt_revision == "ghimera-graph-planning/1"
    assert row.planning_graph == build_context(cfg, ())


def test_non_active_graph_planning_example_parses_its_typed_policy():
    data = tomllib.loads(Path("examples/graph-planning.toml").read_text())
    config = GraphPlanningConfig.model_validate(data["research"]["graph_context"])
    assert config.selection == "newest_first" and config.max_entities == 24
