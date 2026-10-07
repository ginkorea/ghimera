"""Explicit review dimensions; these fixtures do not establish model accuracy."""

import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.model_http import ModelHttpResponse
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_graph import validate_rows
from ghimera.semantic_types import SemanticReview
from tests.test_semantic_graph import SemanticWire, document
from tests.test_semantic_verification import ReviewWire, reviewed_config, run_stage


def factorized_config(tmp_path):
    return reviewed_config(
        tmp_path,
        verification=dict(
            schema="ghimera.semantic-verification/2", model_role="reviewer", max_calls_per_run=8
        ),
    )


class FactorizedWire(ReviewWire):
    def __init__(self, bound, *, dimension=None, verdict="unsupported", inconsistent=False):
        super().__init__(bound)
        self.dimension, self.verdict, self.inconsistent = dimension, verdict, inconsistent

    async def post(self, body):
        response = await super().post(body)
        wire = json.loads(response.body)
        result = json.loads(wire["choices"][0]["message"]["content"])
        result["schema"] = "ghimera.semantic-review/2"
        for item in result["mentions"]:
            item["checks"] = {
                name: dict(verdict="supported", reason="Fixture dimension, not a real model.")
                for name in ("named_entity", "role")
            }
        for item in result["relations"]:
            item["checks"] = {
                name: dict(verdict="supported", reason="Fixture dimension, not a real model.")
                for name in ("entailment", "direction", "validity")
            }
        if self.dimension:
            target = (
                result["mentions"][1]
                if self.dimension in ("named_entity", "role")
                else result["relations"][0]
            )
            target["checks"][self.dimension]["verdict"] = self.verdict
            if not self.inconsistent:
                target["verdict"] = self.verdict
        wire["choices"][0]["message"]["content"] = json.dumps(result)
        return ModelHttpResponse(200, json.dumps(wire).encode(), "application/json")


def test_factorized_wire_retains_all_dimensions_and_replays(tmp_path):
    cfg = factorized_config(tmp_path)
    wire = FactorizedWire(cfg.models.reviewer)
    _, rows, budget = run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))
    window = rows[-1].semantic_window
    assert window.review.schema_version == "ghimera.semantic-review/2"
    assert window.review.model_call.prompt_revision == "ghimera-semantic-verification/2"
    assert window.review.mentions[0].checks.role.verdict == "supported"
    assert window.review.relations[0].checks.direction.verdict == "supported"
    restored = type(window).model_validate_json(window.model_dump_json(by_alias=True))
    assert restored == window and restored.review.mentions[0].checks is not None
    assert validate_rows(cfg, rows) == (rows[-1],)
    assert budget.semantic_calls == budget.semantic_review_calls == 1
    assert "named_entity" in wire.requests[0][0]["messages"][0]["content"]


@pytest.mark.parametrize(
    "dimension", ["named_entity", "role", "entailment", "direction", "validity"]
)
@pytest.mark.parametrize("verdict", ["unsupported", "ambiguous"])
def test_presence_or_confidence_cannot_override_a_failed_dimension(tmp_path, dimension, verdict):
    cfg = factorized_config(tmp_path)
    graph, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst, confidence=1.0),
        FactorizedWire(cfg.models.reviewer, dimension=dimension, verdict=verdict),
        (document(),),
    )
    window = rows[-1].semantic_window
    if dimension in ("named_entity", "role"):
        assert [item.key for item in window.entities] == ["m1"]
        assert window.excluded_mentions[0].reason == "review_" + verdict
        assert window.excluded_relations[0].reason == "endpoint_quarantined"
    else:
        assert len(window.entities) == 2
        assert window.excluded_relations[0].reason == "review_" + verdict
    assert not any(edge.rule == "reports_to" for edge in graph.snapshot().edges)
    assert len(window.proposal.mentions) == 2 and len(window.proposal.relations) == 1


@pytest.mark.parametrize(
    "dimension", ["named_entity", "role", "entailment", "direction", "validity"]
)
def test_inconsistent_supported_summary_refuses_before_projection(tmp_path, dimension):
    cfg = factorized_config(tmp_path)
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            SemanticWire(cfg.models.analyst),
            FactorizedWire(cfg.models.reviewer, dimension=dimension, inconsistent=True),
            (document(),),
        )


def test_v2_policy_refuses_an_undimensioned_legacy_response(tmp_path):
    cfg = factorized_config(tmp_path)
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg, SemanticWire(cfg.models.analyst), ReviewWire(cfg.models.reviewer), (document(),)
        )


def test_legacy_policy_does_not_silently_acquire_v2(tmp_path):
    cfg = reviewed_config(tmp_path)
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            SemanticWire(cfg.models.analyst),
            FactorizedWire(cfg.models.reviewer),
            (document(),),
        )
    assert (
        hashlib.sha256(
            json.dumps(SemanticReview.model_json_schema(), sort_keys=True).encode()
        ).hexdigest()
        == "c726888e6e1e8823edefafa41fc62d9fa3ed855feef6c61908ce6f630cb8e1bf"
    )
    _, rows, _ = run_stage(
        cfg, SemanticWire(cfg.models.analyst), ReviewWire(cfg.models.reviewer), (document(),)
    )
    assert "checks" not in rows[-1].semantic_window.review.model_dump_json()
    assert (
        '"schema"'
        not in rows[-1]
        .semantic_window.review.model_dump_json(by_alias=True)
        .split('"model_call"')[0]
    )


def test_factorized_summary_cannot_be_altered_in_serialized_window(tmp_path):
    cfg = factorized_config(tmp_path)
    _, rows, _ = run_stage(
        cfg, SemanticWire(cfg.models.analyst), FactorizedWire(cfg.models.reviewer), (document(),)
    )
    raw = rows[-1].semantic_window.model_dump(by_alias=True)
    raw["review"]["mentions"][0]["checks"]["role"]["verdict"] = "unsupported"
    with pytest.raises(ValidationError):
        type(rows[-1].semantic_window).model_validate(raw)


def test_factorized_example_is_explicit_and_non_active():
    from ghimera.semantic_types import SemanticConfig

    raw = tomllib.loads(Path("examples/semantics-factorized.toml").read_text())
    policy = SemanticConfig.model_validate(raw)
    assert policy.verification.schema_version == "ghimera.semantic-verification/2"
    assert policy.verification.effective_prompt_revision == "ghimera-semantic-verification/2"


@pytest.mark.parametrize("missing", ["checks", "named_entity", "role"])
def test_missing_dimension_cannot_be_read_as_legacy(tmp_path, missing):
    cfg = factorized_config(tmp_path)
    _, rows, _ = run_stage(
        cfg, SemanticWire(cfg.models.analyst), FactorizedWire(cfg.models.reviewer), (document(),)
    )
    raw = rows[0].model_dump(by_alias=True)
    assessment = raw["semantic_review"]["mentions"][0]
    if missing == "checks":
        assessment.pop("checks")
    else:
        assessment["checks"].pop(missing)
    with pytest.raises(ValidationError):
        type(rows[0]).model_validate(raw)


def test_unsupported_dimension_dominates_uncertainty_and_blank_reasons_refuse(tmp_path):
    cfg = factorized_config(tmp_path)
    _, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        FactorizedWire(cfg.models.reviewer, dimension="role"),
        (document(),),
    )
    raw = rows[0].model_dump(by_alias=True)
    assessment = raw["semantic_review"]["mentions"][1]
    assessment["checks"]["named_entity"]["verdict"] = "ambiguous"
    restored = type(rows[0]).model_validate(raw)
    assert restored.semantic_review.mentions[1].verdict == "unsupported"
    assessment["checks"]["named_entity"]["reason"] = "  "
    with pytest.raises(ValidationError):
        type(rows[0]).model_validate(raw)


def test_reader_binds_dimensioned_response_even_when_call_revision_is_unchanged(tmp_path):
    cfg = factorized_config(tmp_path)
    _, rows, _ = run_stage(
        cfg, SemanticWire(cfg.models.analyst), FactorizedWire(cfg.models.reviewer), (document(),)
    )
    raw = rows[0].semantic_review.model_dump(by_alias=True)
    raw.pop("schema")
    for item in (*raw["mentions"], *raw["relations"]):
        item.pop("checks")
    downgraded = SemanticReview.model_validate(raw)
    row = rows[0].model_copy(update={"semantic_review": downgraded})
    with pytest.raises(ValueError, match="independent configured"):
        validate_rows(cfg, (row, rows[1]))


def test_dimensioned_loop_archive_and_journal_keep_checks_and_call_accounting(tmp_path):
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

    cfg = with_journal(factorized_config(tmp_path), tmp_path)
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
            http=FactorizedWire(cfg.models.reviewer, dimension="direction"),
        ),
    )
    harvest = asyncio.run(
        loop.run(
            Goal(text="map the organization", seeds=("https://example.org/report",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
            run_id="dimensioned-loop",
        )
    )
    restored = Harvest.model_validate_json(harvest.model_dump_json(by_alias=True))
    assert restored == harvest
    assert read_journal(cfg.journal, "dimensioned-loop").rows == harvest.ledger
    review = next(row.semantic_review for row in restored.ledger if row.event == "semantic_review")
    assert review.relations[0].checks.direction.verdict == "unsupported"
    assert not any(edge.rule == "reports_to" for edge in restored.graph.edges)
    budget = RunBudget(cfg, time.monotonic)
    budget.restore(harvest.receipt, harvest.ledger, search_calls=0, downtime_seconds=0)
    assert budget.semantic_calls == budget.semantic_review_calls == 1
    assert budget.judge_calls == 3
