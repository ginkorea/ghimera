"""Every scorer returns only supplied candidates, with validated finite scores."""

import asyncio
from pathlib import Path

import pytest

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.doubles import KeywordScorer
from ghimera.embedding import SelfHostedEncoder
from ghimera.models import Extracted, Goal, LinkCandidate
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_scoring import EmbeddingScorer
from tests.test_embedding_scoring import endpoint as endpoint
from tests.test_embedding_scoring import policy, references, run_config, service


class ReverseFixture(KeywordScorer):
    name = "reverse_fixture"
    cost = 1

    async def rank(self, goal, document, budget, ledger):
        return tuple(reversed(await super().rank(goal, document, budget, ledger)))


@pytest.mark.parametrize("scorer_type", (KeywordScorer, ReverseFixture, EmbeddingScorer))
def test_scorer_conformance(scorer_type, endpoint):
    config = GhimeraConfig.from_toml(Path("examples/chimera.toml"))
    if scorer_type is EmbeddingScorer:
        cfg = service(endpoint[0])
        refs = references(cfg)
        scoring = policy(cfg, refs)
        config = run_config(scoring)
        scorer = EmbeddingScorer(scoring, SelfHostedEncoder(cfg), refs)
    else:
        scorer = scorer_type()
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
    result = asyncio.run(scorer.score(goal, document, budget))
    assert tuple(item.score for item in result) == (1.0, 0.0)
    assert {item.url for item in result} == {item.url for item in document.links}
    assert budget.judge_calls == 0


def test_a_scorer_cannot_invent_candidates_or_override_template():
    class Inventing(KeywordScorer):
        async def rank(self, goal, document, budget, ledger):
            return (LinkCandidate(url="https://invented.example", score=1.0),)

    config = GhimeraConfig.from_toml(Path("examples/chimera.toml"))
    with pytest.raises(GhimeraRefused, match="adapter_contract"):
        asyncio.run(
            Inventing().score(
                Goal(text="ports", seeds=("https://example.org",)),
                Extracted(title="x", text="x", language="en"),
                RunBudget(config, lambda: 0.0),
            )
        )
    with pytest.raises(TypeError):
        type("BadScorer", (KeywordScorer,), {"score": lambda *args: None})
