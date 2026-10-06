"""Typed scoring template; expensive C3 implementations must share the run budget."""

import asyncio
from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import ClassVar, final

from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.ledger import Ledger
from ghimera.models import Extracted, Goal, LinkCandidate
from ghimera.refusals import GhimeraRefused, RefusalCode


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
        self, goal: Goal, document: Extracted, budget: RunBudget, ledger: Ledger | None = None
    ) -> tuple[LinkCandidate, ...]:
        budget.check_time()
        async with asyncio.timeout(budget.remaining_seconds):
            ranked = await self.rank(goal, document, budget, ledger or Ledger())
        eligible = {(link.url, link.anchor) for link in document.links}
        if any((link.url, link.anchor) not in eligible for link in ranked):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        try:
            ranked = tuple(LinkCandidate.model_validate(link.model_dump()) for link in ranked)
        except ValidationError:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        return tuple(sorted(ranked, key=lambda link: link.score, reverse=True))

    def validate_config(self, config: GhimeraConfig) -> None:
        if config.scoring is not None:
            raise ValueError(
                "configured semantic scoring cannot be replaced by a keyword-only scorer"
            )

    @abstractmethod
    async def rank(
        self, goal: Goal, document: Extracted, budget: RunBudget, ledger: Ledger
    ) -> tuple[LinkCandidate, ...]: ...
