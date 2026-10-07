"""Opt-in assigned-role prompt; fixtures establish contracts, not model accuracy."""

import hashlib

import pytest
from pydantic import ValidationError

from ghimera.semantic_types import SemanticVerificationConfig
from tests.test_semantic_batching import BatchWire, batching_config
from tests.test_semantic_graph import SemanticWire, document
from tests.test_semantic_verification import run_stage


def test_unselected_batch_profile_keeps_its_exact_prompt_and_serialization(tmp_path):
    config = batching_config(tmp_path)
    wire = BatchWire(config.models.reviewer)
    run_stage(config, SemanticWire(config.models.analyst), wire, (document(),))
    system = wire.requests[0][0]["messages"][0]["content"]
    assert hashlib.sha256(system.encode()).hexdigest() == (
        "0e57cbaa9f19c8a6a0f1ef2044c65caf9ec6e9748bbd3f803bef4214e85f24a9"
    )
    assert "prompt_profile" not in config.semantics.verification.model_dump(by_alias=True)
    assert config.semantics.verification.effective_prompt_revision == (
        "ghimera-semantic-verification/4"
    )


def test_assigned_role_profile_is_explicit_versioned_and_retained_at_every_call(tmp_path):
    config = batching_config(tmp_path, prompt_profile="assigned_role_checks")
    wire = BatchWire(config.models.reviewer)
    _, rows, _ = run_stage(config, SemanticWire(config.models.analyst), wire, (document(),))
    assert config.semantics.verification.model_dump(by_alias=True)["prompt_profile"] == (
        "assigned_role_checks"
    )
    assert config.semantics.verification.effective_prompt_revision == (
        "ghimera-semantic-verification/5"
    )
    for request, packet in wire.requests:
        system = request["messages"][0]["content"]
        assert "assigned role from the original proposal" in system
        assert "do not test every mention as an office or a person" in system
        assert "An explicit existential sentence is not required" in system
        assert "unsupported if any applicable dimension is unsupported" in system
        assert packet["semantic_recipe"]["verification"]["prompt_profile"] == (
            "assigned_role_checks"
        )
    assert all(
        row.model_call.prompt_revision == "ghimera-semantic-verification/5" for row in rows[:-1]
    )
    window = rows[-1].semantic_window
    assert type(window).model_validate_json(window.model_dump_json()) == window


@pytest.mark.parametrize("version", [1, 2, 3])
def test_earlier_verification_profiles_cannot_silently_change_questions(version):
    raw = dict(
        schema=f"ghimera.semantic-verification/{version}",
        model_role="reviewer",
        max_calls_per_run=8,
        prompt_profile="assigned_role_checks",
    )
    if version == 3:
        raw["max_coverage_findings"] = 2
    with pytest.raises(ValidationError):
        SemanticVerificationConfig.model_validate(raw)


def test_unknown_review_profile_refuses_before_calls(tmp_path):
    with pytest.raises(ValidationError):
        batching_config(tmp_path, prompt_profile="guess_everything")


def test_nonactive_example_selects_the_exact_profile_and_explicit_bounds():
    import tomllib
    from pathlib import Path

    policy = SemanticVerificationConfig.model_validate(
        tomllib.loads(Path("examples/review-assigned-role.toml").read_text())
    )
    assert policy.prompt_profile == "assigned_role_checks"
    assert policy.effective_prompt_revision == "ghimera-semantic-verification/5"
    assert policy.max_mentions_per_call == 4 and policy.max_relations_per_call == 2
    assert policy.max_coverage_findings == 4
