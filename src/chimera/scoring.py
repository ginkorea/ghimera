"""Typed scoring template; expensive C3 implementations must share the run budget."""

import asyncio
from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import ClassVar, final

from chimera.budget import RunBudget
from chimera.models import Extracted, Goal, LinkCandidate
from chimera.refusals import ChimeraRefused, RefusalCode


class Scorer(ABC):
    name: ClassVar[str]
    cost: ClassVar[int]
    TEMPLATE: ClassVar[str] = "score"
    REFERENCE = MappingProxyType({"declaration": "KeywordScorer", "template": "KeywordScorer"})

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if "score" in cls.__dict__:
            raise TypeError("Scorer.score is final; implement rank")
        if (
            not isinstance(getattr(cls, "name", None), str)
            or not cls.name.strip()
            or type(getattr(cls, "cost", None)) is not int
            or cls.cost < 0
        ):
            raise TypeError("Scorer declares nonblank name and nonnegative cost")

    @final
    async def score(
        self, goal: Goal, document: Extracted, budget: RunBudget
    ) -> tuple[LinkCandidate, ...]:
        budget.check_time()
        async with asyncio.timeout(budget.remaining_seconds):
            ranked = await self.rank(goal, document, budget)
        eligible = {link.url for link in document.links}
        if any(link.url not in eligible for link in ranked):
            raise ChimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        return tuple(sorted(ranked, key=lambda link: link.score, reverse=True))

    @abstractmethod
    async def rank(
        self, goal: Goal, document: Extracted, budget: RunBudget
    ) -> tuple[LinkCandidate, ...]: ...
