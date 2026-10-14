"""Run spend: what a run used, counted before each call; never a limit.

The core has no run ceilings (removed in 0.4.12). ``RunSpend`` counts fetches,
bytes, judge, search, encoder and rerank calls so the receipt, rounds and ledger
show spend. A call is counted before its first await so concurrent tasks and a
restored run agree on sequence. No per-run call count is compared with a limit;
only one call's own input bound (rerank pairs/characters of that call) refuses
that call with ``BOUND_EXCEEDED``. In-flight transfers are tracked only so a checkpoint can require
quiescence.
"""

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
from ghimera.refusals import BoundExceeded, GhimeraRefused, RefusalCode

# A transfer with no route bound of its own is bounded by the route itself.
NO_TRANSFER_BOUND = 2**62


class RunSpend:
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
        self.encoding_lock = asyncio.Lock()
        self.encoding_score_lock = asyncio.Lock()
        self.rerank_calls = 0
        self.rerank_pairs = 0
        self.rerank_chars = 0
        self._active_reranks: set[int] = set()
        self._bytes_reserved = 0

    @property
    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.started)

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
            raise ValueError("restored spend requires its original recipe and nonnegative downtime")
        from ghimera.model_reconciliation import unreconciled_model_sequences, validate_policy

        validate_policy(self.config, rows)
        from ghimera.research_reranking import validate_rerank_rows

        rerank_usage = validate_rerank_rows(self.config, rows)
        from ghimera.run_encoding import encoding_usage

        restored_encoding = encoding_usage(self.config, rows)
        if restored_encoding != (receipt.encoding_calls, receipt.encoding_chars):
            raise ValueError("restored encoding receipt must preserve all original charged intents")
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
            and validate_model_rows(self.config.model_work, rows) != receipt.judge_calls
        ):
            raise ValueError("restored receipt must preserve original model reservations")
        self.started -= receipt.elapsed_seconds + downtime_seconds
        self.fetches, self.bytes_read = receipt.fetches, receipt.bytes_read
        self.judge_calls = receipt.judge_calls
        self.rerank_calls, self.rerank_pairs, self.rerank_chars = rerank_usage
        self.encoding_calls, self.encoding_chars = restored_encoding
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

    def reserve_fetch(self) -> None:
        self.fetches += 1

    def record_bytes(self, count: int) -> None:
        self.bytes_read += count

    def reserve_bytes(self, maximum: int) -> int:
        """Track one in-flight transfer of up to ``maximum`` bytes; never a limit."""
        self._bytes_reserved += maximum
        return maximum

    def release_bytes(self, allowance: int) -> None:
        self._bytes_reserved -= allowance
        if self._bytes_reserved < 0:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    def reserve_judge(self) -> None:
        self.judge_calls += 1

    def check_rerank(self, pairs: int, chars: int) -> None:
        policy = self.config.research.reranking if self.config.research is not None else None
        if policy is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT, "reranking policy is absent")
        # Bounds of this one call; nothing compares the run's totals with a limit.
        if pairs > policy.max_pairs:
            raise BoundExceeded("reranking.max_pairs", pairs, policy.max_pairs)
        if chars > policy.max_input_chars:
            raise BoundExceeded("reranking.max_input_chars", chars, policy.max_input_chars)

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
        policy = self.config.challenges
        if policy is None or self.challenge_attempts >= policy.max_attempts_per_run:
            raise GhimeraRefused(RefusalCode.CHALLENGE_NOT_SOLVED)
        self.challenge_attempts += 1

    def reserve_search(self) -> None:
        if self.config.research is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self.search_calls += 1

    def reserve_local_input(self) -> int:
        policy = self.config.local_inputs
        if policy is None:
            raise GhimeraRefused(RefusalCode.LOCAL_INPUT_FAILED)
        allowance = self.reserve_bytes(policy.max_input_bytes)
        self.local_inputs += 1
        return allowance

    def reserve_semantic(self) -> None:
        policy = self.config.semantics
        if policy is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT, "semantic policy is absent")
        self.reserve_judge()
        self.semantic_calls += 1

    def reserve_identity_proposal(self) -> None:
        policy = self.config.identity_automation
        if policy is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT, "identity automation is absent")
        self.reserve_judge()
        self.identity_proposal_calls += 1

    def reserve_identity_review(self) -> None:
        policy = self.config.identity_automation
        if policy is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT, "identity automation is absent")
        self.reserve_judge()
        self.identity_review_calls += 1

    def reserve_encoding(self, input_chars: int) -> None:
        if self.config.scoring is None or input_chars <= 0:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        self.encoding_calls += 1
        self.encoding_chars += input_chars

    def reserve_semantic_review(self) -> None:
        policy = self.config.semantics
        if policy is None or policy.verification is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT, "semantic review is absent")
        self.reserve_judge()
        self.semantic_review_calls += 1
