"""Independent model judgments and deterministic summaries; fixtures are not accuracy."""

import json
from copy import deepcopy
from itertools import product

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.model_client import model_schema
from ghimera.model_http import ModelHttpResponse
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_batching import (
    bind_proposal_dates,
    bound_review_schema,
    derive_independent_review,
)
from ghimera.semantic_graph import validate_rows
from ghimera.semantic_types import (
    GroundedSemanticReview,
    IndependentSemanticReview,
    ReviewSelection,
)
from tests.test_semantic_batching import BatchWire, batching_config
from tests.test_semantic_graph import SemanticWire, document
from tests.test_semantic_grounding import DatedWire, missing_mention
from tests.test_semantic_review_dates import proposal, response, selected
from tests.test_semantic_verification import run_stage

PROFILE = "independent_dimension_checks"


def dimension_config(tmp_path):
    raw = batching_config(tmp_path).model_dump()
    raw["semantics"]["verification"]["prompt_profile"] = PROFILE
    return GhimeraConfig.model_validate(raw)


class DimensionWire(BatchWire):
    """The fixture server emits the new wire, never a client-side answer repair."""

    def __init__(self, bound, **kwargs):
        super().__init__(bound, **kwargs)
        self.responses = []
        self.extra_verdict = False

    async def post(self, body):
        response = await super().post(body)
        if response.status != 200:
            return response
        wire = json.loads(response.body)
        payload = json.loads(wire["choices"][0]["message"]["content"])
        payload["schema"] = "ghimera.semantic-review/5"
        for item in payload["mentions"] + payload["relations"]:
            del item["verdict"]
            if self.extra_verdict:
                item["verdict"] = "supported"
        self.responses.append(payload)
        wire["choices"][0]["message"]["content"] = json.dumps(payload)
        return ModelHttpResponse(200, json.dumps(wire).encode(), "application/json")


def test_independent_wire_preserves_actual_checks_and_completes_original_review(tmp_path):
    cfg = dimension_config(tmp_path)
    wire = DimensionWire(cfg.models.reviewer)
    _, rows, _ = run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))
    window = rows[-1].semantic_window
    assert len(window.review.parts) == len(wire.responses) == 4
    for part, observed in zip(window.review.parts, wire.responses, strict=True):
        assert (
            part.review.dimension_response.model_dump(mode="json", exclude={"model_call"})
            == observed
        )
        assert part.review.model_call.prompt_revision == "ghimera-semantic-verification/7"
        assert part.review.dimension_response.model_call is None
    for request, packet in wire.requests:
        system = request["messages"][0]["content"]
        assert "do not generate an overall verdict" in system
        assert "recompute each mention's overall verdict" not in system
        assert packet["semantic_recipe"]["verification"]["prompt_profile"] == PROFILE
        schema = request["response_format"]["json_schema"]["schema"]
        Draft202012Validator.check_schema(schema)
        assert "verdict" not in schema["$defs"]["IndependentMentionAssessment"]["properties"]
        assert "verdict" not in schema["$defs"]["IndependentRelationAssessment"]["properties"]
    assert validate_rows(cfg, rows) == (rows[-1],)
    assert type(window).model_validate_json(window.model_dump_json()) == window


def test_every_dimension_combination_derives_the_existing_fail_closed_summary():
    original = proposal()
    for checks in product(("supported", "ambiguous", "unsupported"), repeat=3):
        body = response(original, (1,))
        body["schema"] = "ghimera.semantic-review/5"
        relation = body["relations"][0]
        del relation["verdict"]
        for check, verdict in zip(
            (
                relation["checks"]["entailment"],
                relation["checks"]["direction"],
                relation["checks"]["validity"]["assessment"],
            ),
            checks,
            strict=True,
        ):
            check["verdict"] = verdict
        body["mentions"] = [
            dict(
                key="m1",
                reason="Independent fixture judgments.",
                checks=dict(
                    named_entity=dict(verdict=checks[0], reason="Native type fixture."),
                    role=dict(verdict=checks[1], reason="Assigned role fixture."),
                ),
            )
        ]
        observed = IndependentSemanticReview.model_validate(body)
        result = derive_independent_review(observed)
        expected = (
            "unsupported"
            if "unsupported" in checks
            else ("ambiguous" if "ambiguous" in checks else "supported")
        )
        assert result.relations[0].verdict == expected
        expected_mention = (
            "unsupported"
            if "unsupported" in checks[:2]
            else ("ambiguous" if "ambiguous" in checks[:2] else "supported")
        )
        assert result.mentions[0].verdict == expected_mention
        assert result.dimension_response == observed
        assert result.model_call is None  # Pure derivation never fabricates telemetry.


@pytest.mark.parametrize("indices", [(0,), (1,), (2, 0, 3, 1)])
def test_original_dates_indices_and_independent_judgments_are_preserved_in_schema(indices):
    original, selection = proposal(), selected(indices)
    before = original.model_dump_json()
    schema = model_schema(IndependentSemanticReview)
    bound_review_schema(schema, selection, 4, dimensions_only=True)
    bind_proposal_dates(schema, original, selection, dimensions_only=True)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    body = response(original, indices)
    body["schema"] = "ghimera.semantic-review/5"
    for item in body["relations"]:
        del item["verdict"]
        item["checks"]["direction"]["verdict"] = "unsupported"
    validator.validate(body)
    observed = IndependentSemanticReview.model_validate(body)
    assert all(
        item.verdict == "unsupported" for item in derive_independent_review(observed).relations
    )
    for position, index in enumerate(indices):
        wrong = deepcopy(body)
        wrong["relations"][position]["checks"]["validity"] = dict(
            asserted=index == 0,
            assessment=dict(verdict="supported", reason="Wrong input date state.")
            if index == 0
            else None,
        )
        assert not validator.is_valid(wrong)
        wrong = deepcopy(body)
        wrong["relations"][position]["index"] = 88
        assert not validator.is_valid(wrong)
    assert original.model_dump_json() == before


def test_a_model_generated_summary_is_refused_instead_of_removed(tmp_path):
    cfg = dimension_config(tmp_path)
    wire = DimensionWire(cfg.models.reviewer)
    wire.extra_verdict = True
    with pytest.raises(GhimeraRefused):
        run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))
    assert len(wire.requests) == 1


@pytest.mark.parametrize("field", ["reason", "coverage_reason", "checks"])
def test_native_review_cannot_change_an_independent_observation(tmp_path, field):
    cfg = dimension_config(tmp_path)
    _, rows, _ = run_stage(
        cfg, SemanticWire(cfg.models.analyst), DimensionWire(cfg.models.reviewer), (document(),)
    )
    part = rows[-1].semantic_window.review.parts[0].review
    raw = part.model_dump(by_alias=True)
    if field == "coverage_reason":
        raw["dimension_response"][field] = "Changed model statement."
    elif field == "checks":
        raw["dimension_response"]["mentions"][0][field]["role"]["reason"] = "Changed judgment."
    else:
        raw["dimension_response"]["mentions"][0][field] = "Changed model reason."
    with pytest.raises(ValidationError, match="preserve every independent"):
        GroundedSemanticReview.model_validate(raw)


def test_selected_profile_cannot_lose_its_raw_payload_or_be_replayed_as_an_old_profile(tmp_path):
    cfg = dimension_config(tmp_path)
    _, rows, _ = run_stage(
        cfg, SemanticWire(cfg.models.analyst), DimensionWire(cfg.models.reviewer), (document(),)
    )
    raw = rows[-1].semantic_window.model_dump(by_alias=True)
    raw["review"]["parts"][0]["review"].pop("dimension_response")
    with pytest.raises(ValueError, match="exact earlier review"):
        changed = rows[-1].model_copy(
            update={"semantic_window": type(rows[-1].semantic_window).model_validate(raw)}
        )
        validate_rows(cfg, rows[:-1] + (changed,))
    without_payload = rows[0].semantic_review.model_copy(update={"dimension_response": None})
    erased = rows[0].model_copy(update={"semantic_review": without_payload})
    with pytest.raises(ValueError, match="independent configured service"):
        validate_rows(cfg, (erased,) + rows[1:])
    old = cfg.model_dump()
    old["semantics"]["verification"]["prompt_profile"] = "proposal_date_checks"
    with pytest.raises(ValueError, match="independent configured service"):
        validate_rows(GhimeraConfig.model_validate(old), rows)


@pytest.mark.parametrize("extraction,asserted", [(SemanticWire, True), (DatedWire, False)])
def test_schema_ignoring_provider_is_refused_for_false_original_date_state(
    tmp_path, extraction, asserted
):
    cfg = dimension_config(tmp_path)
    wire = DimensionWire(cfg.models.reviewer)
    wire.date_asserted = asserted
    with pytest.raises(GhimeraRefused):
        run_stage(cfg, extraction(cfg.models.analyst), wire, (document(),))


def test_coverage_findings_stay_research_leads_and_late_failures_do_not_retry(tmp_path):
    cfg = dimension_config(tmp_path)
    doc = document("甲委員會隸屬乙委員會。丙委員會發布報告。")
    graph, rows, _ = run_stage(
        cfg,
        SemanticWire(cfg.models.analyst),
        DimensionWire(cfg.models.reviewer, findings=(missing_mention(doc),)),
        (doc,),
    )
    review = rows[-1].semantic_window.review
    assert review.coverage == "incomplete"
    assert review.parts[-1].review.dimension_response.coverage_findings == review.coverage_findings
    assert not any(node.label == "丙委員會" for node in graph.snapshot().nodes)
    assert validate_rows(cfg, rows) == (rows[-1],)
    failed = DimensionWire(cfg.models.reviewer, fail_at=4)
    with pytest.raises(GhimeraRefused):
        run_stage(cfg, SemanticWire(cfg.models.analyst), failed, (doc,))
    assert len(failed.requests) == 4


def test_independent_profile_requires_explicit_grammar_and_nonactive_example(tmp_path):
    import tomllib
    from pathlib import Path

    from ghimera.semantic_types import SemanticVerificationConfig

    cfg = dimension_config(tmp_path)
    raw = cfg.model_dump()
    raw["models"]["reviewer"]["response_format"] = "json_object"
    with pytest.raises(ValidationError, match="json_schema"):
        GhimeraConfig.model_validate(raw)
    policy = SemanticVerificationConfig.model_validate(
        tomllib.loads(Path("examples/review-independent-dimensions.toml").read_text())
    )
    assert policy.prompt_profile == PROFILE
    assert policy.effective_prompt_revision == "ghimera-semantic-verification/7"
    selection = ReviewSelection(
        schema="ghimera.review-selection/1",
        mention_keys=("m1",),
        relation_indices=(),
        coverage=False,
    )
    schema = model_schema(GroundedSemanticReview)
    assert "dimension_response" not in schema["properties"]
    assert "IndependentSemanticReview" not in schema["$defs"]
    bound_review_schema(schema, selection, 4)  # Old wire is still a valid independent boundary.
