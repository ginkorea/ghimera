"""Bind known date assertion state; schemas do not establish model accuracy."""

from copy import deepcopy
from datetime import date

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.model_client import model_schema
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_batching import bound_review_schema
from ghimera.semantic_graph import validate_rows
from ghimera.semantic_types import GroundedSemanticReview, ReviewSelection, SemanticProposal
from tests.test_semantic_batching import BatchWire, batching_config
from tests.test_semantic_graph import SemanticWire, document
from tests.test_semantic_grounding import DatedWire
from tests.test_semantic_verification import run_stage

PROFILE = "proposal_date_checks"


def date_config(tmp_path):
    raw = batching_config(tmp_path).model_dump()
    raw["semantics"]["verification"]["prompt_profile"] = PROFILE
    return GhimeraConfig.model_validate(raw)


def proposal():
    return SemanticProposal.model_validate(
        dict(
            mentions=[
                dict(
                    key=key,
                    role="entity",
                    surface=surface,
                    citation_id="cite:" + "0" * 64,
                    occurrence=0,
                    confidence=0.9,
                )
                for key, surface in (("m1", "甲委員會"), ("m2", "乙委員會"))
            ],
            relations=[
                dict(
                    rule="reports_to",
                    source="m1",
                    target="m2",
                    confidence=0.9,
                    citation_ids=["cite:" + "0" * 64],
                    valid_from=start,
                    valid_to=end,
                )
                for start, end in (
                    (None, None),
                    ("2020-01-01", None),
                    (None, "2021-01-01"),
                    ("2020-01-01", "2021-01-01"),
                )
            ],
        )
    )


def selected(indices):
    return ReviewSelection(
        schema="ghimera.review-selection/1",
        mention_keys=(),
        relation_indices=indices,
        coverage=False,
    )


def bound_schema(original, selection):
    from ghimera.semantic_batching import bind_proposal_dates

    schema = model_schema(GroundedSemanticReview)
    bound_review_schema(schema, selection, 2)
    bind_proposal_dates(schema, original, selection)
    return schema


def assessment(index, asserted):
    check = dict(verdict="supported", reason="Fixture, not a factual verdict.")
    return dict(
        index=index,
        verdict="supported",
        reason="Fixture, not model accuracy.",
        checks=dict(
            entailment=deepcopy(check),
            direction=deepcopy(check),
            validity=dict(asserted=asserted, assessment=deepcopy(check) if asserted else None),
        ),
    )


def response(original, indices):
    return dict(
        schema="ghimera.semantic-review/3",
        proposal_digest=original.content_digest(),
        mentions=[],
        relations=[assessment(index, index != 0) for index in indices],
        coverage="uncertain",
        coverage_reason="A partition does not assess coverage.",
        coverage_findings=[],
    )


def test_explicit_date_profile_runs_complete_review_and_retains_revision(tmp_path):
    cfg = date_config(tmp_path)
    wire = BatchWire(cfg.models.reviewer)
    _, rows, _ = run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))
    assert cfg.semantics.verification.effective_prompt_revision == "ghimera-semantic-verification/6"
    for request, packet in wire.requests:
        assert packet["semantic_recipe"]["verification"]["prompt_profile"] == PROFILE
        assert "assigned role from the original proposal" in request["messages"][0]["content"]
        Draft202012Validator.check_schema(request["response_format"]["json_schema"]["schema"])
    assert len(rows[-1].semantic_window.review.parts) == 4
    assert all(
        row.model_call.prompt_revision == "ghimera-semantic-verification/6" for row in rows[:-1]
    )
    assert validate_rows(cfg, rows) == (rows[-1],)


def test_date_schema_requires_explicit_provider_grammar(tmp_path):
    cfg = date_config(tmp_path)
    raw = cfg.model_dump()
    raw["models"]["reviewer"]["response_format"] = "json_object"
    with pytest.raises(ValidationError, match="json_schema"):
        GhimeraConfig.model_validate(raw)


@pytest.mark.parametrize("indices", [(0,), (1,), (2,), (3,), (2, 0, 3, 1)])
def test_dates_are_bound_to_original_global_index_without_changing_proposal(indices):
    original = proposal()
    before = original.model_dump_json()
    schema = bound_schema(original, selected(indices))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    good = response(original, indices)
    validator.validate(good)
    for position, index in enumerate(indices):
        wrong = deepcopy(good)
        wrong["relations"][position]["checks"]["validity"] = dict(
            asserted=index == 0,
            assessment=dict(verdict="supported", reason="Wrong assertion state.")
            if index == 0
            else None,
        )
        assert not validator.is_valid(wrong)
        wrong = deepcopy(good)
        wrong["relations"][position]["index"] = 88
        assert not validator.is_valid(wrong)
    assert original.model_dump_json() == before
    assert original.relations[1].valid_from == date(2020, 1, 1)


def test_date_binding_does_not_force_factual_support_or_repair_aggregate():
    original, indices = proposal(), (1,)
    validator = Draft202012Validator(bound_schema(original, selected(indices)))
    body = response(original, indices)
    body["relations"][0]["checks"]["validity"]["assessment"]["verdict"] = "unsupported"
    body["relations"][0]["verdict"] = "unsupported"
    validator.validate(body)
    assert GroundedSemanticReview.model_validate(body).relations[0].verdict == "unsupported"
    body["relations"][0]["verdict"] = "supported"
    with pytest.raises(ValidationError, match="relation summary"):
        GroundedSemanticReview.model_validate(body)


def test_provider_that_ignores_schema_is_still_refused_on_false_dates(tmp_path):
    cfg = date_config(tmp_path)
    wire = BatchWire(cfg.models.reviewer)
    wire.date_asserted = True
    with pytest.raises(GhimeraRefused):
        run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))


def test_original_asserted_date_still_gets_an_independent_assessment(tmp_path):
    cfg = date_config(tmp_path)
    wire = BatchWire(cfg.models.reviewer)
    wire.date_asserted = True
    _, rows, _ = run_stage(cfg, DatedWire(cfg.models.analyst), wire, (document(),))
    relation = rows[-1].semantic_window.review.relations[0]
    assert relation.checks.validity.asserted and relation.checks.validity.assessment is not None
    assert validate_rows(cfg, rows) == (rows[-1],)


def test_old_role_profile_keeps_original_request_schema(tmp_path):
    cfg = batching_config(tmp_path, prompt_profile="assigned_role_checks")
    wire = BatchWire(cfg.models.reviewer)
    run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))
    for request, packet in wire.requests:
        schema = model_schema(GroundedSemanticReview)
        selection = ReviewSelection.model_validate(packet["semantic_review_selection"])
        bound_review_schema(schema, selection, 2)
        assert request["response_format"]["json_schema"]["schema"] == schema
    assert cfg.semantics.verification.effective_prompt_revision == "ghimera-semantic-verification/5"


@pytest.mark.parametrize("defect", ["missing_defs", "invalid_index"])
def test_date_binding_refuses_invalid_boundary_before_generating(defect):
    from ghimera.semantic_batching import bind_proposal_dates

    schema = model_schema(GroundedSemanticReview)
    selection = selected((0,))
    if defect == "missing_defs":
        del schema["$defs"]
    else:
        selection = selected((88,))
    with pytest.raises(GhimeraRefused, match="adapter_contract"):
        bind_proposal_dates(schema, proposal(), selection)


def test_mention_and_coverage_schemas_do_not_gain_irrelevant_date_branches():
    original = proposal()
    for selection in (
        ReviewSelection(
            schema="ghimera.review-selection/1",
            mention_keys=("m1",),
            relation_indices=(),
            coverage=False,
        ),
        ReviewSelection(
            schema="ghimera.review-selection/1", mention_keys=(), relation_indices=(), coverage=True
        ),
    ):
        old = model_schema(GroundedSemanticReview)
        bound_review_schema(old, selection, 2)
        assert bound_schema(original, selection) == old


def test_safe_example_is_nonactive_and_explicit():
    import tomllib
    from pathlib import Path

    from ghimera.semantic_types import SemanticVerificationConfig

    raw = tomllib.loads(Path("examples/review-proposal-dates.toml").read_text())
    policy = SemanticVerificationConfig.model_validate(raw)
    assert policy.prompt_profile == PROFILE
    assert policy.effective_prompt_revision == "ghimera-semantic-verification/6"
