"""Explicit collection-to-corpus handoff without changing published harvest identities."""

import asyncio
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal, Protocol

from pydantic import Field, model_validator

from ghimera.corpus import EvidenceCorpus
from ghimera.corpus_types import CorpusReceipt
from ghimera.models import Goal, Harvest, Record, Scope
from ghimera.research_types import ResearchRequest, ResearchResult


class CollectionService(Protocol):
    """Existing collector lifecycle; no concrete search or model provider required."""

    async def collect(self, goal: Goal, scope: Scope, *, run_id: str | None = None) -> Harvest: ...

    async def run(
        self,
        request: str | ResearchRequest,
        *,
        run_id: str | None = None,
        suspend_after_rounds: int | None = None,
    ) -> ResearchResult: ...

    async def resume(
        self,
        run_id: str,
        *,
        checkpoint_sha256: str,
        suspend_after_rounds: int | None = None,
    ) -> ResearchResult: ...


class PersistentCollection(Record):
    schema_version: Literal["ghimera.persistent-collection/1"] = Field(alias="schema")
    result: Harvest | ResearchResult
    corpus: CorpusReceipt

    @property
    def harvest(self) -> Harvest:
        return self.result if isinstance(self.result, Harvest) else self.result.harvest

    @model_validator(mode="after")
    def bound(self) -> "PersistentCollection":
        identity = hashlib.sha256(self.harvest.model_dump_json().encode()).hexdigest()
        if self.corpus.harvest_sha256 != identity:
            raise ValueError("corpus acknowledgement must bind this exact collected harvest")
        return self


class CorpusHandoffFailure(Exception):
    """Collection finished; its retained result can be persisted without another crawl."""

    def __init__(self, result: Harvest | ResearchResult) -> None:
        self.result = result
        super().__init__("corpus_handoff_failed: preserve result and inspect the chained failure")


class CorpusHandoffCancelled(asyncio.CancelledError):
    """Cancellation during handoff retains the already completed source work."""

    def __init__(self, result: Harvest | ResearchResult) -> None:
        self.result = result
        super().__init__("corpus_handoff_cancelled: preserve result before retrying persistence")


class PersistentCollector:
    """Borrow configured components; persist before reporting successful completion.

    The caller owns both components and closes the corpus after all operations.
    Collection and corpus model budgets stay separate in their original receipts.
    One operation per facade is an invariant, not an unbounded implicit work queue;
    independent corpus readers may still query its last committed generation.
    """

    def __init__(self, collector: CollectionService, corpus: EvidenceCorpus) -> None:
        corpus.check_ready()
        self._collector, self._corpus = collector, corpus
        self._active = False

    @property
    def corpus(self) -> EvidenceCorpus:
        return self._corpus

    @contextmanager
    def _operation(self) -> Iterator[None]:
        if self._active:
            raise ValueError("persistent collector already owns an active operation")
        self._corpus.check_ready()
        self._active = True
        try:
            yield
        finally:
            self._active = False

    async def _persist(self, result: Harvest | ResearchResult) -> PersistentCollection:
        result = (
            Harvest.model_validate(result.model_dump())
            if isinstance(result, Harvest)
            else ResearchResult.model_validate(result.model_dump())
        )
        harvest = result if isinstance(result, Harvest) else result.harvest
        try:
            receipt = await self.corpus.append(harvest)
            return PersistentCollection(
                schema="ghimera.persistent-collection/1", result=result, corpus=receipt
            )
        except asyncio.CancelledError as exc:
            raise CorpusHandoffCancelled(result) from exc
        except Exception as exc:
            raise CorpusHandoffFailure(result) from exc

    async def persist(self, result: Harvest | ResearchResult) -> PersistentCollection:
        """Retry only persistence of retained source work; never discovery/fetching."""
        with self._operation():
            return await self._persist(result)

    async def collect(
        self, goal: Goal, scope: Scope, *, run_id: str | None = None
    ) -> PersistentCollection:
        with self._operation():
            return await self._persist(await self._collector.collect(goal, scope, run_id=run_id))

    async def run(
        self,
        request: str | ResearchRequest,
        *,
        run_id: str | None = None,
        suspend_after_rounds: int | None = None,
    ) -> PersistentCollection:
        with self._operation():
            result = await self._collector.run(
                request, run_id=run_id, suspend_after_rounds=suspend_after_rounds
            )
            return await self._persist(result)

    async def resume(
        self,
        run_id: str,
        *,
        checkpoint_sha256: str,
        suspend_after_rounds: int | None = None,
    ) -> PersistentCollection:
        with self._operation():
            result = await self._collector.resume(
                run_id,
                checkpoint_sha256=checkpoint_sha256,
                suspend_after_rounds=suspend_after_rounds,
            )
            return await self._persist(result)
