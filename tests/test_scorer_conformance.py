"""Every scorer returns only supplied candidates, with validated finite scores."""

import asyncio
from pathlib import Path

import pytest

from chimera.budget import RunBudget
from chimera.config import ChimeraConfig
from chimera.doubles import KeywordScorer
from chimera.models import Extracted, Goal, LinkCandidate


class ReverseFixture(KeywordScorer):
    name = "reverse_fixture"
    cost = 1

    async def rank(self, goal, document, budget):
        return tuple(reversed(await super().rank(goal, document, budget)))


@pytest.mark.parametrize("scorer_type", (KeywordScorer, ReverseFixture))
def test_scorer_conformance(scorer_type):
    config = ChimeraConfig.from_toml(Path("examples/chimera.toml"))
    budget = RunBudget(config, lambda: 0.0)
    document = Extracted(
        title="fixture",
        text="fixture",
        language="en",
        links=(
            LinkCandidate(url="https://example.org/x", anchor="unrelated"),
            LinkCandidate(url="https://example.org/ports", anchor="ports"),
        ),
    )
    goal = Goal(text="ports", seeds=("https://example.org",))
    result = asyncio.run(scorer_type().score(goal, document, budget))
    assert tuple(item.score for item in result) == (1.0, 0.0)
    assert {item.url for item in result} == {item.url for item in document.links}
    assert budget.judge_calls == 0


def test_a_scorer_cannot_invent_candidates_or_override_template():
    from chimera.refusals import ChimeraRefused

    class Inventing(KeywordScorer):
        async def rank(self, goal, document, budget):
            return (LinkCandidate(url="https://invented.example", score=1.0),)

    config = ChimeraConfig.from_toml(Path("examples/chimera.toml"))
    with pytest.raises(ChimeraRefused, match="adapter_contract"):
        asyncio.run(
            Inventing().score(
                Goal(text="ports", seeds=("https://example.org",)),
                Extracted(title="x", text="x", language="en"),
                RunBudget(config, lambda: 0.0),
            )
        )
    with pytest.raises(TypeError):
        type("BadScorer", (KeywordScorer,), {"score": lambda *args: None})
