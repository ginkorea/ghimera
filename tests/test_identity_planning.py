"""Identity/dispute planning is an unresolved research view, not entity truth."""

import asyncio
import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.graph_planning import build_context, validate_context
from ghimera.graph_planning_types import GraphPlanningConfig
from ghimera.journal import read_journal
from ghimera.loop import GoalLoop
from ghimera.model_client import SelfHostedModel
from ghimera.model_http import ModelHttpResponse
from ghimera.refusals import GhimeraRefused
from ghimera.research import ResearchLoop
from ghimera.research_types import (
    PlanningRequest,
    Question,
    ResearchPlan,
    ResearchRequest,
    ResearchResult,
    SearchHit,
    SearchQuery,
    SearchResponse,
)
from tests.test_graph_planning import NativeFixture, observed, planning_config, with_journal
from tests.test_intent_research import AnalystFixture, ReviewerFixture, SearchFixture, identity
from tests.test_semantic_graph import SemanticWire, document, exercise


def identity_config(tmp_path, **limits):
    raw = planning_config(tmp_path).model_dump(by_alias=True)
    raw["research"]["graph_context"].update(
        schema="ghimera.graph-planning/3",
        max_gaps=2,
        identity={
            "schema": "ghimera.identity-planning/1",
            "candidate_strategy": "exact_surface_and_role",
            "alias_rules": [],
            "exclusive_relations": ["reports_to"],
            "unknown_time_policy": "report_possible",
            "max_groups": 8,
            "max_members_per_group": 8,
            "max_disputes": 8,
            "max_pair_checks": 16,
        },
    )
    raw["research"]["graph_context"]["identity"].update(limits)
    return GhimeraConfig.model_validate(raw)


def test_same_name_is_a_research_candidate_not_a_merged_identity(tmp_path):
    cfg = identity_config(tmp_path)
    docs = (document(), document(url="https://example.org/second"))
    snapshot, rows, _ = observed(cfg, docs)
    context = build_context(cfg, rows)
    view = context.identity
    assert view.schema_version == "ghimera.identity-view/1"
    assert len(view.groups) == 2 and all(g.status == "unresolved" for g in view.groups)
    assert all(len(g.node_ids) == 2 and g.bases == ("exact_surface_and_role",) for g in view.groups)
    assert len([n for n in snapshot.nodes if n.role == "entity"]) == 4
    assert set().union(*(set(g.node_ids) for g in view.groups)) == {
        e.node.id for e in context.entities
    }
    assert not view.disputes
    assert {g.id for g in view.groups} <= context.references
    validate_context(cfg, context, docs)
    assert context == type(context).model_validate_json(context.model_dump_json())


def test_identity_policy_is_explicit_and_cannot_leak_into_legacy_profiles(tmp_path):
    cfg = identity_config(tmp_path)
    for bad in ("ghimera.graph-planning/1", "ghimera.graph-planning/2"):
        raw = cfg.model_dump(by_alias=True)
        raw["research"]["graph_context"]["schema"] = bad
        with pytest.raises(ValidationError):
            GhimeraConfig.model_validate(raw)
    raw = cfg.model_dump(by_alias=True)
    raw["research"]["graph_context"].pop("identity")
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)
    raw = cfg.model_dump(by_alias=True)
    raw["research"]["graph_context"]["identity"]["alias_rules"] = ["invented"]
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)


class DatedWire(SemanticWire):
    def __init__(self, bound, dates):
        super().__init__(bound)
        self.dates = iter(dates)

    async def post(self, body):
        response = await super().post(body)
        envelope = json.loads(response.body)
        output = json.loads(envelope["choices"][0]["message"]["content"])
        packet = json.loads(json.loads(body)["messages"][1]["content"])
        quote = packet["evidence"]["windows"][0]["citation"]["quote"]
        if "丙委員會" in quote:
            output["mentions"][1]["surface"] = "丙委員會"
        start, end = next(self.dates)
        output["relations"][0].update(valid_from=start, valid_to=end)
        envelope["choices"][0]["message"]["content"] = json.dumps(output)
        return ModelHttpResponse(200, json.dumps(envelope).encode(), "application/json")


@pytest.mark.parametrize(
    "dates,expected",
    [
        ((("2025-01-01", "2025-12-31"), ("2025-06-01", "2026-01-01")), "known_overlap"),
        (((None, None), (None, None)), "unknown_time"),
        ((("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31")), None),
    ],
)
def test_competing_targets_keep_both_assertions_and_unknown_time_is_not_filled(
    tmp_path, dates, expected
):
    cfg = identity_config(tmp_path)
    docs = (document(), document("甲委員會隸屬丙委員會。", url="https://example.org/second"))
    _, rows, _ = exercise(cfg, DatedWire(cfg.models.analyst, dates), docs)
    context = build_context(cfg, rows)
    assert len(context.relations) == 2
    if expected:
        assert len(context.identity.disputes) == 1
        dispute = context.identity.disputes[0]
        assert dispute.basis == expected and dispute.status == "unresolved"
        assert {dispute.left_relation_id, dispute.right_relation_id} == {
            e.id for e in context.relations
        }
        assert dispute.id in context.references
    else:
        assert not context.identity.disputes
    validate_context(cfg, context, docs)


def test_group_bounds_do_not_present_partial_membership_as_the_whole_group(tmp_path):
    cfg = identity_config(tmp_path, max_members_per_group=2)
    docs = tuple(document(url=f"https://example.org/{n}") for n in range(3))
    _, rows, _ = observed(cfg, docs)
    context = build_context(cfg, rows)
    assert context.identity.groups == ()
    assert context.identity.omitted_groups == 2 and context.identity.omitted_members == 6


def test_forged_identity_group_refuses_before_model_io(tmp_path):
    cfg = identity_config(tmp_path)
    docs = (document(), document(url="https://example.org/second"))
    _, rows, _ = observed(cfg, docs)
    context = build_context(cfg, rows)
    group = context.identity.groups[0]
    forged = context.model_copy(
        update={
            "identity": context.identity.model_copy(
                update={
                    "groups": (
                        group.model_copy(update={"node_ids": (*group.node_ids, "invented")}),
                    )
                }
            )
        }
    )
    with pytest.raises(GhimeraRefused):
        validate_context(cfg, forged, docs)


class IdentityPlanWire:
    def __init__(self, bound):
        self.config, self.requests = bound, []

    async def post(self, body):
        request = json.loads(body)
        self.requests.append(request)
        packet = json.loads(request["messages"][1]["content"])
        group = packet["graph_context"]["identity"]["groups"][0]
        output = ResearchPlan(
            questions=(Question(id="q1", text="Are these the same organization?"),),
            queries=(
                SearchQuery(
                    text="organization official aliases",
                    question_ids=("q1",),
                    graph_refs=(group["id"],),
                ),
            ),
        ).model_dump(exclude={"model_call"})
        response = dict(
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
        return ModelHttpResponse(200, json.dumps(response).encode(), "application/json")


def test_concrete_planner_can_research_identity_without_claiming_it_resolved(tmp_path):
    cfg = identity_config(tmp_path)
    docs = (document(), document(url="https://example.org/second"))
    _, rows, _ = observed(cfg, docs)
    context = build_context(cfg, rows)
    wire = IdentityPlanWire(cfg.models.planner)
    request = PlanningRequest(
        intent="map organization",
        questions=(),
        documents=docs,
        assessment=None,
        max_questions=2,
        max_queries=2,
        max_query_chars=200,
        graph_context=context,
    )
    plan = asyncio.run(SelfHostedModel(cfg, cfg.models.planner, http=wire).plan(request))
    assert plan.queries[0].graph_refs == (context.identity.groups[0].id,)
    assert plan.model_call.prompt_revision == "ghimera-graph-planning/3"
    assert "unresolved" in wire.requests[0]["messages"][0]["content"]


def alias_config(tmp_path, *, cross_role=False):
    raw = identity_config(tmp_path).model_dump(mode="json", by_alias=True)
    roles = ["entity"]
    if cross_role:
        roles.append("person")
        raw["graph"]["roles"].append(dict(name="person", kind="person"))
        mention = next(r for r in raw["graph"]["relations"] if r["name"] == "mentions")
        mention["target_roles"].append("person")
    raw["graph"]["relations"].append(
        dict(
            name="alias_of",
            predicate="alias_of",
            source_roles=roles,
            target_roles=roles,
            semantic=True,
        )
    )
    raw["semantics"]["entity_roles"] = roles
    raw["semantics"]["relation_rules"].append("alias_of")
    context = raw["research"]["graph_context"]
    context["entity_roles"] = roles
    context["relation_rules"].append("alias_of")
    context["identity"]["alias_rules"] = ["alias_of"]
    return GhimeraConfig.model_validate(raw)


class AliasWire(SemanticWire):
    def __init__(self, bound, *, cross_role=False):
        super().__init__(bound)
        self.cross_role = cross_role

    async def post(self, body):
        response = await super().post(body)
        envelope = json.loads(response.body)
        output = json.loads(envelope["choices"][0]["message"]["content"])
        output["relations"][0]["rule"] = "alias_of"
        if self.cross_role:
            output["mentions"][1]["role"] = "person"
        envelope["choices"][0]["message"]["content"] = json.dumps(output)
        return ModelHttpResponse(200, json.dumps(envelope).encode(), "application/json")


def test_alias_group_keeps_its_original_claims_and_never_rekeys_mentions(tmp_path):
    cfg = alias_config(tmp_path)
    doc = document("甲委員會又稱乙委員會。")
    snapshot, rows, _ = exercise(cfg, AliasWire(cfg.models.analyst), (doc,))
    context = build_context(cfg, rows)
    group = context.identity.groups[0]
    assert group.bases == ("model_asserted_alias",) and group.status == "unresolved"
    assert group.alias_relation_ids == (context.relations[0].id,)
    assert set(group.node_ids) == {node.id for node in snapshot.nodes if node.role == "entity"}
    assert context.relations[0].claim_status == "model_asserted"
    validate_context(cfg, context, (doc,))


def test_alias_assertion_cannot_equate_different_entity_roles(tmp_path):
    cfg = alias_config(tmp_path, cross_role=True)
    doc = document()
    _, rows, _ = exercise(cfg, AliasWire(cfg.models.analyst, cross_role=True), (doc,))
    context = build_context(cfg, rows)
    assert len(context.entities) == 2 and len(context.relations) == 1
    assert not context.identity.groups and context.identity.cross_role_aliases == 1
    validate_context(cfg, context, (doc,))


def test_pair_and_dispute_caps_keep_omissions_visible(tmp_path):
    docs = (
        document(),
        document(url="https://example.org/two"),
        document("甲委員會隸屬丙委員會。", url="https://example.org/three"),
    )
    for cap, expected in ((dict(max_pair_checks=1), (1, 2)), (dict(max_disputes=1), (3, 0))):
        cfg = identity_config(tmp_path, **cap)
        _, rows, _ = exercise(cfg, DatedWire(cfg.models.analyst, [(None, None)] * 3), docs)
        context = build_context(cfg, rows)
        assert (context.identity.examined_pairs, context.identity.omitted_pairs) == expected
        if "max_disputes" in cap:
            assert len(context.identity.disputes) == context.identity.omitted_disputes == 1
        assert len(context.relations) == 3
        validate_context(cfg, context, docs)


def test_unknown_time_skip_never_invents_an_interval(tmp_path):
    cfg = identity_config(tmp_path, unknown_time_policy="skip")
    docs = (document(), document("甲委員會隸屬丙委員會。", url="https://example.org/two"))
    _, rows, _ = exercise(cfg, DatedWire(cfg.models.analyst, [(None, None)] * 2), docs)
    context = build_context(cfg, rows)
    assert not context.identity.disputes and context.identity.examined_pairs == 1
    assert all(edge.valid_from is None and edge.valid_to is None for edge in context.relations)


@pytest.mark.parametrize("dates", [("2025-12-01", "2025-01-01"), ("20250101", None)])
def test_invalid_dates_refuse_instead_of_making_a_temporal_claim(tmp_path, dates):
    cfg = identity_config(tmp_path)
    # Deliberately corrupt an acknowledged graph edge, not a successful extraction.
    _, rows, _ = observed(cfg)
    row = rows[0]
    window = row.semantic_window
    original = next(edge for edge in window.edges if edge.rule == "reports_to")
    edge = original.model_copy(update=dict(valid_from=dates[0], valid_to=dates[1]))
    forged = row.model_copy(
        update={
            "semantic_window": window.model_copy(
                update={
                    "edges": tuple(
                        edge if item.id == original.id else item for item in window.edges
                    )
                }
            )
        }
    )
    with pytest.raises(GhimeraRefused):
        build_context(cfg, (forged,))


class IdentityPlanner:
    model = identity("identity-planner")

    def __init__(self):
        self.requests = []

    async def plan(self, request):
        self.requests.append(request)
        groups = request.graph_context.identity.groups
        return ResearchPlan(
            questions=request.questions or (Question(id="q1", text="Which organization?"),),
            queries=(
                SearchQuery(
                    text="source aliases" if groups else "organization structure",
                    question_ids=("q1",),
                    graph_refs=(groups[0].id,) if groups else (),
                ),
            ),
        )


class TwoSources(SearchFixture):
    async def request(self, request):
        self.requests.append(request)
        return SearchResponse(
            raw=b'{"fixture":"two sources"}',
            hits=tuple(
                SearchHit(
                    url=f"https://example.org/{n}", title="Controlled source", snippet="discovery"
                )
                for n in ("one", "two")
            ),
        )


@pytest.mark.parametrize("selection", ["newest_first", "identity_first"])
def test_identity_question_drives_followup_and_replays_from_the_journal(tmp_path, selection):
    raw_cfg = identity_config(tmp_path).model_dump(mode="json", by_alias=True)
    raw_cfg["research"]["graph_context"]["selection"] = selection
    cfg = with_journal(GhimeraConfig.model_validate(raw_cfg), tmp_path)
    planner, search = IdentityPlanner(), TwoSources()
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
    result = asyncio.run(
        loop.run(ResearchRequest(intent="map organization"), run_id="identity-plan")
    )
    assert result.status == "partial" and result.stop_reason == "rounds_exhausted"
    assert not planner.requests[0].graph_context.identity.groups
    assert planner.requests[1].graph_context.identity.groups
    assert search.requests[1].query.graph_refs[0].startswith("identity:")
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result
    report = read_journal(cfg.journal, "identity-plan")
    assert report.rows == result.harvest.ledger and report.summary is not None
    raw = result.model_dump()
    plan = next(
        r
        for r in raw["harvest"]["ledger"]
        if r["event"] == "plan" and r["planning_graph"]["identity"]["groups"]
    )
    plan["planning_graph"]["identity"]["examined_pairs"] += 1
    with pytest.raises(ValidationError, match="replay exactly"):
        ResearchResult.model_validate(raw)


def test_legacy_graph_recipe_canonical_bytes_do_not_gain_identity_fields():
    for filename in ("graph-planning.toml", "graph-planning-gaps.toml"):
        raw = tomllib.loads((Path("examples") / filename).read_text())["research"]["graph_context"]
        policy = GraphPlanningConfig.model_validate(raw)
        assert policy.model_dump(mode="json", by_alias=True) == raw
        assert policy.identity is None


def test_non_active_identity_example_requires_explicit_ontology_and_bounds():
    raw = tomllib.loads(Path("examples/graph-planning-identity.toml").read_text())
    policy = GraphPlanningConfig.model_validate(raw["research"]["graph_context"])
    assert policy.view_schema == "ghimera.planning-graph/3"
    assert policy.identity.alias_rules == ("alias_of",)
    assert policy.identity.exclusive_relations == ()
    assert policy.identity.max_pair_checks == 64
