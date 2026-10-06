"""Reserve before spend; the run owns one budget shared by its injected collaborators."""

from collections.abc import Callable

from ghimera.config import GhimeraConfig
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
        self.local_input_bytes = 0
        self.encoding_calls = 0
        self.encoding_chars = 0
        self._bytes_reserved = 0

    @property
    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.started)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.config.wall_seconds - self.elapsed)

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.config.byte_budget - self.bytes_read)

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

    def reserve_judge(self) -> None:
        self.check_time()
        if self.judge_calls >= self.config.judge_budget:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        self.judge_calls += 1

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
