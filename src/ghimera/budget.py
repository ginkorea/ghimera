"""Reserve before spend; the run owns one budget shared by its injected collaborators."""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ghimera.config import GhimeraConfig
from ghimera.model_reconciliation_types import (
    ModelAttemptAuthorization,
    ModelReconciliationDecision,
)
from ghimera.model_work import validate_model_rows
from ghimera.models import LedgerRow, Receipt
from ghimera.refusals import GhimeraRefused, RefusalCode


class RunBudget:
    def __init__(self, config: GhimeraConfig, clock: Callable[[], float]) -> None:
        self.config = config
        self.clock = clock
        self.started = clock()
        self.fetches = 0
        self.bytes_read = 0
        self.judge_calls = 0
        self.search_calls = 0
        self.challenge_attempts = 0
        self.local_inputs = 0
        self.semantic_calls = 0
        self.semantic_review_calls = 0
        self.identity_proposal_calls = 0
        self.identity_review_calls = 0
        self.local_input_bytes = 0
        self.encoding_calls = 0
        self.encoding_chars = 0
        self.rerank_calls = 0
        self.rerank_pairs = 0
        self.rerank_chars = 0
        self._active_reranks: set[int] = set()
        self._bytes_reserved = 0
        self._bytes_released = asyncio.Event()

    @property
    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.started)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.config.wall_seconds - self.elapsed)

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.config.byte_budget - self.bytes_read)

    @property
    def quiescent(self) -> bool:
        return self._bytes_reserved == 0

    def restore(
        self,
        receipt: Receipt,
        rows: tuple[LedgerRow, ...],
        search_calls: int,
        downtime_seconds: float,
        admission: ModelReconciliationDecision | ModelAttemptAuthorization | None = None,
    ) -> None:
        if receipt.effective_config != self.config or downtime_seconds < 0:
            raise ValueError(
                "restored budget requires its original recipe and nonnegative downtime"
            )
        from ghimera.model_reconciliation import unreconciled_model_sequences, validate_policy

        validate_policy(self.config, rows)
        from ghimera.research_reranking import validate_rerank_rows

        rerank_usage = validate_rerank_rows(self.config, rows)
        admitted_unknown: tuple[int, ...] = ()
        if admission is not None:
            recovery = self.config.research_recovery
            if recovery is None or recovery.model_reconciliation is None:
                raise ValueError("unknown admission requires its original explicit decision policy")
            if isinstance(admission, ModelReconciliationDecision):
                observed = admission.observed
                if (
                    observed.ledger_rows != len(rows)
                    or observed.original_intent_sequence >= len(rows)
                    or rows[observed.original_intent_sequence].model_intent != observed.intent
                    or observed.judge_calls != receipt.judge_calls
                ):
                    raise ValueError(
                        "decision admission changed its original intent or reservation"
                    )
                admitted_unknown = (observed.original_intent_sequence,)
            else:
                from ghimera.model_reconciliation import validate_decisions

                if admission not in validate_decisions(rows) or any(
                    row.model_attempt is not None and row.model_attempt.authorization == admission
                    for row in rows
                ):
                    raise ValueError("attempt admission changed or was already consumed")
                admitted_unknown = (admission.attempt_intent_sequence,)
        if self.config.model_work is not None and set(unreconciled_model_sequences(rows)) - set(
            admitted_unknown
        ):
            raise ValueError("uncertain model reservations require reconciliation, not retry")
        if (
            self.config.model_work is not None
            and validate_model_rows(self.config.model_work, self.config.judge_budget, rows)
            != receipt.judge_calls
        ):
            raise ValueError("restored receipt must preserve original model reservations")
        if (
            receipt.fetches > self.config.page_budget
            or receipt.bytes_read > self.config.byte_budget
            or receipt.judge_calls > self.config.judge_budget
        ):
            raise ValueError("restored spend exceeds the original run budget")
        self.started -= receipt.elapsed_seconds + downtime_seconds
        self.fetches, self.bytes_read = receipt.fetches, receipt.bytes_read
        self.judge_calls = receipt.judge_calls
        self.rerank_calls, self.rerank_pairs, self.rerank_chars = rerank_usage
        self.encoding_calls, self.encoding_chars = receipt.encoding_calls, receipt.encoding_chars
        self.search_calls = search_calls
        self.challenge_attempts = sum(row.event == "challenge" for row in rows)
        self.local_inputs = sum(row.event == "local_input" for row in rows)
        self.local_input_bytes = sum(row.bytes_read for row in rows if row.event == "local_input")
        self.identity_proposal_calls = sum(
            row.model_intent is not None and row.model_intent.phase == "identity_propose"
            for row in rows
        )
        self.identity_review_calls = sum(
            row.model_intent is not None and row.model_intent.phase == "identity_review"
            for row in rows
        )
        if self.config.model_work is not None:
            self.semantic_calls = sum(
                row.model_intent is not None and row.model_intent.phase == "semantic_extract"
                for row in rows
            )
            self.semantic_review_calls = sum(
                row.model_intent is not None and row.model_intent.phase == "semantic_review"
                for row in rows
            )
        else:
            self.semantic_calls = sum(row.event == "semantic" for row in rows)
            self.semantic_review_calls = sum(row.event == "semantic_review" for row in rows)

    def check_time(self) -> None:
        if self.remaining_seconds <= 0:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)

    def reserve_fetch(self) -> None:
        self.check_time()
        if self.fetches >= self.config.page_budget or self.remaining_bytes <= 0:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        self.fetches += 1

    def record_bytes(self, count: int) -> None:
        self.bytes_read += count
        if self.bytes_read > self.config.byte_budget:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    def reserve_bytes(self, maximum: int) -> int:
        """Atomic on the owning asyncio loop; in-flight requests cannot oversubscribe."""
        available = self.remaining_bytes - self._bytes_reserved
        if available <= 0:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        allowance = min(maximum, available)
        self._bytes_reserved += allowance
        return allowance

    def release_bytes(self, allowance: int) -> None:
        self._bytes_reserved -= allowance
        if self._bytes_reserved < 0:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self._bytes_released.set()

    async def wait_bytes(self, maximum: int) -> int:
        """Temporary in-flight reservations are backpressure, not actual spend.

        Checking/clearing/reserving is atomic on this run's asyncio loop. A
        released reservation wakes waiters, which recheck actual available bytes
        before claiming them. No source/model call begins without its allowance.
        """
        while True:
            self.check_time()
            if self.remaining_bytes - self._bytes_reserved > 0:
                return self.reserve_bytes(maximum)
            if self._bytes_reserved == 0 or self.remaining_bytes == 0:
                raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            self._bytes_released.clear()
            async with asyncio.timeout(self.remaining_seconds):
                await self._bytes_released.wait()

    def reserve_judge(self) -> None:
        self.check_time()
        if self.judge_calls >= self.config.judge_budget:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        self.judge_calls += 1

    def check_rerank(self, pairs: int, chars: int) -> None:
        self.check_time()
        policy = self.config.research.reranking if self.config.research is not None else None
        if (
            policy is None
            or self.rerank_calls >= policy.max_calls
            or self.rerank_pairs + pairs > policy.max_pairs
            or self.rerank_chars + chars > policy.max_input_chars
            or self.judge_calls >= self.config.judge_budget
        ):
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)

    def reserve_rerank(self, pairs: int, chars: int) -> None:
        self.check_rerank(pairs, chars)
        self.reserve_judge()
        self.rerank_calls += 1
        self.rerank_pairs += pairs
        self.rerank_chars += chars

    @property
    def active_rerank_sequences(self) -> frozenset[int]:
        """Live contacts only; never restored or treated as retained acknowledgements."""
        return frozenset(self._active_reranks)

    @contextmanager
    def rerank_contact(self, sequence: int) -> Iterator[None]:
        if sequence in self._active_reranks:
            raise ValueError("one original rerank contact cannot be entered twice")
        self._active_reranks.add(sequence)
        try:
            yield
        finally:
            self._active_reranks.remove(sequence)

    def reserve_challenge(self) -> None:
        self.check_time()
        policy = self.config.challenges
        if policy is None or self.challenge_attempts >= policy.max_attempts_per_run:
            raise GhimeraRefused(RefusalCode.CHALLENGE_NOT_SOLVED)
        self.challenge_attempts += 1

    def reserve_search(self) -> None:
        self.check_time()
        policy = self.config.research
        if policy is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if self.search_calls >= policy.query_budget:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        self.search_calls += 1

    def reserve_local_input(self) -> int:
        self.check_time()
        policy = self.config.local_inputs
        if policy is None:
            raise GhimeraRefused(RefusalCode.LOCAL_INPUT_FAILED)
        if self.local_inputs >= policy.max_files_per_run:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        remaining = policy.max_total_bytes - self.local_input_bytes
        if remaining <= 0:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        allowance = self.reserve_bytes(min(policy.max_input_bytes, remaining))
        self.local_inputs += 1
        return allowance

    def reserve_semantic(self) -> None:
        policy = self.config.semantics
        if policy is None or self.semantic_calls >= policy.max_calls_per_run:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        self.reserve_judge()
        self.semantic_calls += 1

    def reserve_identity_proposal(self) -> None:
        policy = self.config.identity_automation
        if policy is None or self.identity_proposal_calls >= policy.max_proposal_calls:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        self.reserve_judge()
        self.identity_proposal_calls += 1

    def reserve_identity_review(self) -> None:
        policy = self.config.identity_automation
        if policy is None or self.identity_review_calls >= policy.max_review_calls:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        self.reserve_judge()
        self.identity_review_calls += 1

    def reserve_encoding(self, input_chars: int) -> None:
        self.check_time()
        policy = self.config.scoring
        if policy is None or input_chars <= 0:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        if (
            self.encoding_calls >= policy.encoding_call_budget
            or self.encoding_chars + input_chars > policy.encoding_char_budget
        ):
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        self.encoding_calls += 1
        self.encoding_chars += input_chars

    def reserve_semantic_review(self) -> None:
        policy = self.config.semantics
        if (
            policy is None
            or policy.verification is None
            or self.semantic_review_calls >= policy.verification.max_calls_per_run
        ):
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        self.reserve_judge()
        self.semantic_review_calls += 1
