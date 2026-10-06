"""Contract-first C0 acceptance; all network/model collaborators are doubles."""

import asyncio
import importlib
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeEncoder, FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder, FetchRoute
from ghimera.loop import GoalLoop
from ghimera.models import Goal, Grade, LinkCandidate, Scope, Verdict
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.scoring import Scorer


def config(**updates: object) -> GhimeraConfig:
    raw = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    raw.update(updates)
    return GhimeraConfig.model_validate(raw)


def scope(**updates: object) -> Scope:
    raw: dict[str, object] = {
        "allowed_hosts": ("example.org",),
        "max_depth": 3,
        "content_types": ("text/html",),
    }
    raw.update(updates)
    return Scope.model_validate(raw)


def run(
    cfg: GhimeraConfig,
    *,
    judge: FakeJudge | None = None,
    route: FakeRoute | None = None,
    scoped: Scope | None = None,
):
    selected_route = route or FakeRoute()
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((selected_route,)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=judge or FakeJudge(),
    )
    goal = Goal(text="ports", seeds=("https://example.org/start",))
    return asyncio.run(loop.run(goal, scoped or scope())), selected_route


def test_versioned_config_fails_closed_and_is_immutable():
    cfg = config()
    assert cfg.schema_version == "chimera.config/1"
    with pytest.raises(ValidationError):
        config(schema="chimera.config/2")
    with pytest.raises(ValidationError):
        config(page_budget=0)
    with pytest.raises(ValidationError):
        config(unknown_operator_value=1)
    with pytest.raises(ValidationError):
        cfg.page_budget = 10


def test_goal_loop_stops_before_page_budget_and_records_every_attempt():
    result, route = run(config(page_budget=3, saturation_window=10))
    assert result.receipt.stop_reason == "budget_exhausted"
    assert result.receipt.fetches == len(route.requests) == 3
    assert sum(row.event == "fetch" for row in result.ledger) == 3
    assert len(result.documents) == 3
    assert result.model_validate_json(result.model_dump_json()) == result


def test_saturation_counts_unique_accepted_documents_not_duplicates():
    result, _ = run(
        config(page_budget=20, saturation_window=3),
        route=FakeRoute(same_content=True),
        scoped=scope(max_depth=10),
    )
    assert result.receipt.stop_reason == "saturated"
    assert result.receipt.fetches == 6
    assert len(result.documents) == 1
    assert any(row.event == "duplicate" for row in result.ledger)


def test_goal_grade_and_judge_budget_are_real_stop_rules():
    result, _ = run(config(grade_interval=2, page_budget=10), judge=FakeJudge(satisfied=True))
    assert result.receipt.stop_reason == "goal_satisfied"
    assert result.receipt.fetches == 2
    assert result.receipt.judge_calls == 3
    exhausted, _ = run(config(judge_budget=1, page_budget=10))
    assert exhausted.receipt.stop_reason == "budget_exhausted"
    assert exhausted.receipt.judge_calls == 1
    assert len(exhausted.documents) == 1


def test_byte_and_time_budget_stop_before_another_attempt():
    result, route = run(config(byte_budget=12, page_budget=20))
    assert result.receipt.bytes_read == 12
    assert len(route.requests) == 1
    ticks = iter((0.0, 2.0, 2.0, 2.0))
    loop = GoalLoop(
        config=config(wall_seconds=1.0),
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        clock=lambda: next(ticks),
    )
    timed = asyncio.run(loop.run(Goal(text="ports", seeds=("https://example.org/start",)), scope()))
    assert timed.receipt.stop_reason == "budget_exhausted"
    assert timed.receipt.fetches == 0


def test_out_of_scope_and_blocked_pages_are_recorded_not_followed():
    result, route = run(
        config(page_budget=5),
        route=FakeRoute(),
        scoped=scope(max_depth=1),
    )
    assert all("example.org" in request.url for request in route.requests)
    assert any(row.refusal == RefusalCode.OUT_OF_SCOPE for row in result.ledger)
    refused, route = run(config(), route=FakeRoute(refusal=RefusalCode.CHALLENGE_NOT_SOLVED))
    assert refused.documents == ()
    assert len(route.requests) == 1
    assert any(row.refusal == RefusalCode.CHALLENGE_NOT_SOLVED for row in refused.ledger)


def test_hold_gets_one_more_model_pass_and_never_a_person():
    result, _ = run(config(page_budget=1), judge=FakeJudge(hold_first=True))
    assert result.receipt.judge_calls == 2
    assert result.documents[0].verdict.decision == "accept"
    assert sum(row.event == "verdict" for row in result.ledger) == 2


def test_instances_do_not_share_visited_state():
    fetcher = FetchLadder((FakeRoute(),))
    loop = GoalLoop(
        config=config(page_budget=1),
        fetcher=fetcher,
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    goal = Goal(text="ports", seeds=("https://example.org/start",))
    first = asyncio.run(loop.run(goal, scope()))
    second = asyncio.run(loop.run(goal, scope()))
    assert first.receipt.fetches == second.receipt.fetches == 1


def test_scope_rejects_credentials_private_hosts_and_url_tricks():
    for url in (
        "file:///etc/passwd",
        "https://user:secret@example.org/a",
        "http://127.0.0.1/a",
        "https://example.org.evil/a",
    ):
        assert not scope().permits(url)
    assert scope().permits("https://example.org/a")
    assert not scope().permits("https://sub.example.org/a")


@pytest.mark.parametrize("base", (FetchRoute, Scorer))
def test_extension_declarations_fail_on_creation_and_templates_are_final(base):
    with pytest.raises(TypeError):
        type("MissingDeclaration", (base,), {})
    with pytest.raises(TypeError):
        type(
            "OverrideTemplate",
            (base,),
            {"name": "bad", "cost": 0, "needs_browser": False, base.TEMPLATE: lambda *args: None},
        )
    assert base.REFERENCE


def test_fake_encoder_has_the_port_shape_and_no_inference_stack():
    assert asyncio.run(FakeEncoder().encode(("ports", "other"))) == ((1.0, 0.0), (0.0, 1.0))
    importlib.import_module("ghimera")
    modules = {name.split(".")[0] for name in sys.modules}
    assert not {"torch", "transformers", "openai", "ollama", "redis"} & modules


def test_model_boundaries_reject_nonfinite_scores_and_blank_evidence():
    with pytest.raises(ValidationError):
        LinkCandidate(url="https://example.org", score=float("nan"))
    with pytest.raises(ValidationError):
        Verdict(decision="accept", kind="report", publisher="publisher", language="en", reason="")
    with pytest.raises(ValidationError):
        Grade(satisfied=True, confidence=2.0, reason="evidence")


def test_named_refusal_teaches_the_operator():
    exc = GhimeraRefused(RefusalCode.NO_CRAWL_EGRESS)
    assert "crawl_egress" in str(exc)
    assert exc.code == RefusalCode.NO_CRAWL_EGRESS
