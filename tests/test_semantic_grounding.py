"""Source-bound omission witnesses and date assertion state, not model accuracy."""

import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.model_http import ModelHttpResponse
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_graph import validate_rows
from tests.test_semantic_factorization import FactorizedWire
from tests.test_semantic_graph import SemanticWire, document
from tests.test_semantic_verification import reviewed_config, run_stage, with_planning


def grounded_config(tmp_path, **updates):
    verification = dict(
        schema="ghimera.semantic-verification/3",
        model_role="reviewer",
        max_calls_per_run=8,
        max_coverage_findings=2,
    )
    verification.update(updates)
    return reviewed_config(tmp_path, verification=verification)


class GroundedWire(FactorizedWire):
    def __init__(self, bound, *, findings=(), date_asserted=False, **kwargs):
        super().__init__(bound, **kwargs)
        self.findings, self.date_asserted = findings, date_asserted

    async def post(self, body):
        response = await super().post(body)
        wire = json.loads(response.body)
        result = json.loads(wire["choices"][0]["message"]["content"])
        result["schema"] = "ghimera.semantic-review/3"
        result["coverage_findings"] = list(self.findings)
        if self.findings:
            result["coverage"] = "incomplete"
        for item in result["relations"]:
            assessment = item["checks"]["validity"] if self.date_asserted else None
            item["checks"]["validity"] = dict(asserted=self.date_asserted, assessment=assessment)
        wire["choices"][0]["message"]["content"] = json.dumps(result)
        return ModelHttpResponse(200, json.dumps(wire).encode(), "application/json")


class DatedWire(SemanticWire):
    async def post(self, body):
        response = await super().post(body)
        wire = json.loads(response.body)
        result = json.loads(wire["choices"][0]["message"]["content"])
        result["relations"][0]["valid_from"] = "2020-01-01"
        wire["choices"][0]["message"]["content"] = json.dumps(result)
        return ModelHttpResponse(200, json.dumps(wire).encode(), "application/json")


def missing_mention(doc, **updates):
    from ghimera.evidence_context import native_citation
    from ghimera.model_citations import citation_id

    finding = dict(
        kind="mention",
        role="entity",
        surface="丙委員會",
        occurrence=0,
        citation_id=citation_id(native_citation(doc, 0, len(doc.extracted.text))),
        reason="The native window names this additional institution.",
    )
    finding.update(updates)
    return finding


def test_null_dates_are_unasserted_and_unchanged_across_wire_projection_and_replay(tmp_path):
    cfg = grounded_config(tmp_path)
    wire = GroundedWire(cfg.models.reviewer)
    graph, rows, _ = run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))
    window = rows[-1].semantic_window
    assert window.review.schema_version == "ghimera.semantic-review/3"
    assert window.review.model_call.prompt_revision == "ghimera-semantic-verification/3"
    assert window.review.relations[0].checks.validity.asserted is False
    assert window.review.relations[0].checks.validity.assessment is None
    relation = next(edge for edge in graph.snapshot().edges if edge.rule == "reports_to")
    assert relation.valid_from is None and relation.valid_to is None
    restored = type(window).model_validate_json(window.model_dump_json(by_alias=True))
    assert restored == window
    assert validate_rows(cfg, rows) == (rows[-1],)


@pytest.mark.parametrize("extraction,date_asserted", [(DatedWire, False), (SemanticWire, True)])
def test_date_state_must_match_the_original_proposal(tmp_path, extraction, date_asserted):
    cfg = grounded_config(tmp_path)
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            extraction(cfg.models.analyst),
            GroundedWire(cfg.models.reviewer, date_asserted=date_asserted),
            (document(),),
        )


def test_supported_dates_still_require_a_supported_independent_dimension(tmp_path):
    cfg = grounded_config(tmp_path)
    graph, rows, _ = run_stage(
        cfg,
        DatedWire(cfg.models.analyst),
        GroundedWire(cfg.models.reviewer, date_asserted=True, dimension="validity"),
        (document(),),
    )
    assert rows[-1].semantic_window.excluded_relations[0].reason == "review_unsupported"
    assert not any(edge.rule == "reports_to" for edge in graph.snapshot().edges)


def test_source_bound_omissions_are_retained_not_projected_as_new_entities(tmp_path):
    from ghimera.config import GhimeraConfig
    from ghimera.graph_planning import build_context, validate_context

    cfg = with_planning(grounded_config(tmp_path))
    raw = cfg.model_dump()
    # The wider response schema consumes the same caller-owned input allowance.
    raw["research"]["max_model_input_chars"] = 20000
    cfg = GhimeraConfig.model_validate(raw)
    doc = document("甲委員會隸屬乙委員會。丙委員會發布報告。")
    finding = missing_mention(doc)
    graph, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        GroundedWire(cfg.models.reviewer, findings=(finding,)),
        (doc,),
    )
    review = rows[-1].semantic_window.review
    assert review.coverage == "incomplete"
    assert review.coverage_findings[0].surface == "丙委員會"
    assert not any(node.label == "丙委員會" for node in graph.snapshot().nodes)
    assert validate_rows(cfg, rows) == (rows[-1],)
    context = build_context(cfg, rows)
    assert context.gaps[0].coverage_findings == review.coverage_findings
    validate_context(cfg, context, (doc,))
    witness = context.gaps[0].coverage_findings[0].model_copy(update={"surface": "不存在"})
    changed_gap = context.gaps[0].model_copy(update={"coverage_findings": (witness,)})
    with pytest.raises(GhimeraRefused):
        validate_context(cfg, context.model_copy(update={"gaps": (changed_gap,)}), (doc,))
    narrow = with_planning(grounded_config(tmp_path), max_evidence_chars=1)
    bounded = build_context(narrow, rows)
    assert bounded.gaps == () and bounded.omitted_gaps == 1
    assert bounded.omitted_evidence_chars >= len(finding["surface"])


def test_larger_review_schema_cannot_bypass_a_small_caller_input_allowance(tmp_path):
    cfg = with_planning(grounded_config(tmp_path))
    wire = GroundedWire(cfg.models.reviewer)
    with pytest.raises(GhimeraRefused, match="budget_exhausted"):
        run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))
    assert wire.requests == []


@pytest.mark.parametrize(
    "changes",
    [
        dict(surface="不存在的委員會"),
        dict(occurrence=1),
        dict(citation_id="cite:" + "0" * 64),
        dict(role="unknown"),
        dict(surface="甲委員會"),
    ],
)
def test_omission_witnesses_cannot_invent_spans_roles_citations_or_covered_items(tmp_path, changes):
    cfg = grounded_config(tmp_path)
    doc = document("甲委員會隸屬乙委員會。丙委員會發布報告。")
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            SemanticWire(cfg.models.analyst),
            GroundedWire(cfg.models.reviewer, findings=(missing_mention(doc, **changes),)),
            (doc,),
        )


def test_coverage_witnesses_have_an_explicit_operator_bound(tmp_path):
    with pytest.raises(ValidationError):
        grounded_config(tmp_path, max_coverage_findings=None)
    cfg = grounded_config(tmp_path, max_coverage_findings=1)
    doc = document("甲委員會隸屬乙委員會。丙委員會發布報告。丁委員會發布報告。")
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            SemanticWire(cfg.models.analyst),
            GroundedWire(
                cfg.models.reviewer,
                findings=(missing_mention(doc), missing_mention(doc, surface="丁委員會")),
            ),
            (doc,),
        )


@pytest.mark.parametrize(
    "profile", ["ghimera.semantic-verification/1", "ghimera.semantic-verification/2"]
)
def test_old_profiles_cannot_silently_acquire_a_coverage_bound(tmp_path, profile):
    with pytest.raises(ValidationError):
        grounded_config(tmp_path, schema=profile)


def test_v3_refuses_a_legacy_dimensioned_reply(tmp_path):
    cfg = grounded_config(tmp_path)
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            SemanticWire(cfg.models.analyst),
            FactorizedWire(cfg.models.reviewer),
            (document(),),
        )


def missing_relation(doc, **updates):
    witness = missing_mention(doc)
    for name in ("kind", "role", "reason"):
        witness.pop(name)
    finding = dict(
        kind="relation",
        rule="reports_to",
        source=dict(witness, surface="丙委員會"),
        target=dict(witness, surface="乙委員會"),
        evidence=dict(witness, surface="丙委員會隸屬乙委員會。"),
        reason="The native window asserts this additional relation.",
    )
    finding["target"]["occurrence"] = 1
    finding.update(updates)
    return finding


def test_missing_relation_keeps_native_endpoints_and_evidence_without_projecting(tmp_path):
    cfg = grounded_config(tmp_path)
    doc = document("甲委員會隸屬乙委員會。丙委員會隸屬乙委員會。")
    graph, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        GroundedWire(cfg.models.reviewer, findings=(missing_relation(doc),)),
        (doc,),
    )
    assert len([edge for edge in graph.snapshot().edges if edge.rule == "reports_to"]) == 1
    finding = rows[-1].semantic_window.review.coverage_findings[0]
    assert finding.source.surface == "丙委員會" and finding.target.occurrence == 1
    assert validate_rows(cfg, rows) == (rows[-1],)


@pytest.mark.parametrize(
    "defect", ["wrong_occurrence", "absent_quote", "unknown_rule", "already_proposed"]
)
def test_missing_relation_requires_this_exact_occurrence_and_declared_omission(tmp_path, defect):
    cfg = grounded_config(tmp_path)
    doc = document("甲委員會隸屬乙委員會。丙委員會隸屬乙委員會。")
    finding = missing_relation(doc)
    if defect == "wrong_occurrence":
        finding["target"]["occurrence"] = 0
    elif defect == "absent_quote":
        finding["evidence"]["surface"] = "丙委員會發布聲明。"
    elif defect == "unknown_rule":
        finding["rule"] = "unknown"
    else:
        finding["source"]["surface"] = "甲委員會"
        finding["target"]["occurrence"] = 0
        finding["evidence"]["surface"] = "甲委員會隸屬乙委員會。"
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            SemanticWire(cfg.models.analyst),
            GroundedWire(cfg.models.reviewer, findings=(finding,)),
            (doc,),
        )


def test_same_missing_occurrence_with_different_reason_is_still_duplicate(tmp_path):
    cfg = grounded_config(tmp_path)
    doc = document("甲委員會隸屬乙委員會。丙委員會發布報告。")
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            SemanticWire(cfg.models.analyst),
            GroundedWire(
                cfg.models.reviewer,
                findings=(missing_mention(doc), missing_mention(doc, reason="Different wording.")),
            ),
            (doc,),
        )


@pytest.mark.parametrize("coverage", ["adequate", "uncertain"])
def test_coverage_cannot_hide_concrete_omissions(tmp_path, coverage):
    from ghimera.semantic_types import GroundedSemanticReview

    cfg = grounded_config(tmp_path)
    doc = document("甲委員會隸屬乙委員會。丙委員會發布報告。")
    _, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        GroundedWire(cfg.models.reviewer, findings=(missing_mention(doc),)),
        (doc,),
    )
    raw = rows[0].semantic_review.model_dump(by_alias=True)
    raw["coverage"] = coverage
    with pytest.raises(ValidationError):
        GroundedSemanticReview.model_validate(raw)


def test_incomplete_without_a_witness_is_not_a_grounded_coverage_claim(tmp_path):
    from ghimera.semantic_types import GroundedSemanticReview

    cfg = grounded_config(tmp_path)
    _, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        GroundedWire(cfg.models.reviewer),
        (document(),),
    )
    raw = rows[0].semantic_review.model_dump(by_alias=True)
    raw["coverage"] = "incomplete"
    with pytest.raises(ValidationError):
        GroundedSemanticReview.model_validate(raw)


def test_grounded_loop_archive_journal_and_budget_restoration(tmp_path):
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

    cfg = with_journal(grounded_config(tmp_path), tmp_path)
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
            cfg, cfg.models.reviewer, http=GroundedWire(cfg.models.reviewer)
        ),
    )
    harvest = asyncio.run(
        loop.run(
            Goal(text="map the organization", seeds=("https://example.org/report",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
            run_id="grounded-loop",
        )
    )
    restored = Harvest.model_validate_json(harvest.model_dump_json(by_alias=True))
    assert restored == harvest
    assert read_journal(cfg.journal, "grounded-loop").rows == harvest.ledger
    review = next(row.semantic_review for row in restored.ledger if row.event == "semantic_review")
    assert review.relations[0].checks.validity.assessment is None
    budget = RunBudget(cfg, time.monotonic)
    budget.restore(harvest.receipt, harvest.ledger, search_calls=0, downtime_seconds=0)
    assert budget.semantic_calls == budget.semantic_review_calls == 1


def test_grounded_example_is_explicit_and_old_policy_serialization_is_unchanged(tmp_path):
    from ghimera.semantic_types import SemanticConfig

    policy = SemanticConfig.model_validate(
        tomllib.loads(Path("examples/semantics-grounded.toml").read_text())
    )
    assert policy.verification.schema_version == "ghimera.semantic-verification/3"
    assert policy.verification.max_coverage_findings == 8
    old = reviewed_config(tmp_path).semantics.verification.model_dump(by_alias=True)
    assert old == dict(
        schema="ghimera.semantic-verification/1", model_role="reviewer", max_calls_per_run=8
    )
