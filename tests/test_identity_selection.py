"""An explicit selection policy reserves evidence for unanswered graph questions."""

import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.graph_planning import build_context, validate_context
from ghimera.graph_planning_types import GraphPlanningConfig
from ghimera.model_http import ModelHttpResponse
from tests.test_identity_planning import AliasWire, DatedWire, alias_config, identity_config
from tests.test_semantic_graph import SemanticWire, document, exercise
from tests.test_semantic_verification import ReviewWire, reviewed_config, run_stage


def question_config(cfg, **limits):
    raw = cfg.model_dump(mode="json", by_alias=True)
    raw["research"]["graph_context"].update(selection="identity_first", **limits)
    return GhimeraConfig.model_validate(raw)


class NamedWire(SemanticWire):
    async def post(self, body):
        response = await super().post(body)
        packet = json.loads(json.loads(body)["messages"][1]["content"])
        quote = packet["evidence"]["windows"][0]["citation"]["quote"]
        first, second = quote.rstrip("。").split("隸屬")
        envelope = json.loads(response.body)
        output = json.loads(envelope["choices"][0]["message"]["content"])
        output["mentions"][0]["surface"] = first
        output["mentions"][1]["surface"] = second
        envelope["choices"][0]["message"]["content"] = json.dumps(output)
        return ModelHttpResponse(200, json.dumps(envelope).encode(), "application/json")


def test_identity_first_retains_a_whole_question_before_newest_unrelated_claim(tmp_path):
    cfg = question_config(identity_config(tmp_path), max_entities=2, max_context_chars=5000)
    docs = (
        document(),
        document(url="https://example.org/two"),
        document("丙委員會隸屬丁委員會。", url="https://example.org/newest"),
    )
    _, rows, _ = exercise(cfg, NamedWire(cfg.models.analyst), docs)
    context = build_context(cfg, rows)
    assert len(context.identity.groups) == 1
    group = context.identity.groups[0]
    assert len(group.node_ids) == 2 and group.status == "unresolved"
    assert set(group.node_ids) == {entity.node.id for entity in context.entities}
    assert {source.source_url for source in context.sources} == {doc.url for doc in docs[:2]}
    assert context.omitted_entities == 4 and context.omitted_relations == 3
    validate_context(cfg, context, docs)
    old = cfg.model_dump(mode="json", by_alias=True)
    old["research"]["graph_context"]["selection"] = "newest_first"
    legacy = build_context(GhimeraConfig.model_validate(old), rows)
    assert not legacy.identity.groups
    assert {entity.node.label for entity in legacy.entities} == {"丙委員會", "丁委員會"}


def test_identity_first_is_explicit_and_cannot_change_old_profile_selection(tmp_path):
    raw = identity_config(tmp_path).model_dump(mode="json", by_alias=True)
    raw["research"]["graph_context"].update(
        selection="identity_first", schema="ghimera.graph-planning/2"
    )
    raw["research"]["graph_context"].pop("identity")
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)


def test_fallback_does_not_split_a_known_candidate_that_exceeds_the_node_limit(tmp_path):
    cfg = question_config(identity_config(tmp_path), max_entities=2)
    docs = tuple(document(url=f"https://example.org/repeat-{n}") for n in range(3)) + (
        document("丙委員會隸屬丁委員會。", url="https://example.org/last"),
    )
    _, rows, _ = exercise(cfg, NamedWire(cfg.models.analyst), docs)
    context = build_context(cfg, rows)
    assert not context.identity.groups
    assert {entity.node.label for entity in context.entities} == {"丙委員會", "丁委員會"}
    assert len(context.entities) == 2 and context.omitted_entities == 6
    validate_context(cfg, context, docs)


def test_alias_question_keeps_the_claim_that_connects_its_original_mentions(tmp_path):
    cfg = question_config(alias_config(tmp_path), max_entities=2, max_relations=1)
    docs = (document("甲委員會又稱乙委員會。"),)
    _, rows, _ = exercise(cfg, AliasWire(cfg.models.analyst), docs)
    context = build_context(cfg, rows)
    group = context.identity.groups[0]
    assert group.alias_relation_ids == (context.relations[0].id,)
    assert context.relations[0].rule == "alias_of"
    assert set(group.node_ids) == {entity.node.id for entity in context.entities}
    validate_context(cfg, context, docs)


def test_competing_claim_question_retains_both_claims_and_every_endpoint(tmp_path):
    cfg = question_config(identity_config(tmp_path), max_entities=4)
    docs = (document(), document("甲委員會隸屬丙委員會。", url="https://example.org/two"))
    _, rows, _ = exercise(cfg, DatedWire(cfg.models.analyst, [(None, None)] * 2), docs)
    context = build_context(cfg, rows)
    assert len(context.identity.disputes) == 1 and len(context.relations) == 2
    assert {edge.source for edge in context.relations} <= {e.node.id for e in context.entities}
    assert {edge.target for edge in context.relations} <= {e.node.id for e in context.entities}
    assert all(edge.valid_from is None and edge.valid_to is None for edge in context.relations)
    validate_context(cfg, context, docs)


def test_identity_first_respects_quote_and_serialized_context_caps(tmp_path):
    cfg = question_config(identity_config(tmp_path), max_evidence_chars=1)
    docs = (document(), document(url="https://example.org/two"))
    _, rows, _ = exercise(cfg, NamedWire(cfg.models.analyst), docs)
    context = build_context(cfg, rows)
    assert not context.entities and not context.identity.groups
    assert context.omitted_entities == 4
    assert len(context.model_dump_json()) <= cfg.research.graph_context.max_context_chars
    validate_context(cfg, context, docs)


def test_gap_admission_keeps_the_already_selected_identity_evidence(tmp_path):
    raw = reviewed_config(tmp_path).model_dump(mode="json", by_alias=True)
    raw["research"] = question_config(identity_config(tmp_path), max_entities=2).model_dump(
        mode="json", by_alias=True
    )["research"]
    cfg = GhimeraConfig.model_validate(raw)
    docs = (document(), document(url="https://example.org/two"))
    _, rows, _ = run_stage(
        cfg,
        NamedWire(cfg.models.analyst),
        ReviewWire(cfg.models.reviewer, coverage="incomplete"),
        docs,
    )
    context = build_context(cfg, rows)
    assert len(context.identity.groups) == 1 and len(context.entities) == 2
    assert len(context.gaps) == 2 and context.omitted_gaps == 0
    assert all(gap.coverage == "incomplete" for gap in context.gaps)
    assert set(context.identity.groups[0].node_ids) == {e.node.id for e in context.entities}
    validate_context(cfg, context, docs)


def test_identity_first_example_is_non_active_and_policy_digest_distinct():
    raw = tomllib.loads(Path("examples/graph-planning-identity-first.toml").read_text())
    policy = GraphPlanningConfig.model_validate(raw["research"]["graph_context"])
    assert policy.selection == "identity_first"
    assert policy.model_dump(mode="json", by_alias=True) == raw["research"]["graph_context"]
    old = policy.model_copy(update={"selection": "newest_first"})
    assert policy.content_digest() != old.content_digest()
