"""Run-owned append-only ledger; immutable snapshots rather than exposed lists."""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ghimera.config import GhimeraConfig
from ghimera.models import Harvest, LedgerRow

if TYPE_CHECKING:
    from ghimera.journal_types import JournalHeader


class LedgerSink(Protocol):
    def append(self, row: LedgerRow) -> None: ...

    def finish(self, harvest: Harvest) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class DurableLedgerSink(LedgerSink, Protocol):
    """Append acknowledges durable storage, bound to the complete original recipe.

    Implementations promising this capability must not substitute memory-only
    acknowledgement. Native DirectoryLedgerSink fsyncs the journal before return.
    """

    @property
    def effective_config(self) -> GhimeraConfig: ...


@runtime_checkable
class ReplayLedgerSink(DurableLedgerSink, Protocol):
    """Replay requires the writer's exact committed prefix, not borrowed caller rows."""

    @property
    def committed_rows(self) -> tuple[LedgerRow, ...]: ...


@runtime_checkable
class RunLedgerSink(ReplayLedgerSink, Protocol):
    @property
    def header(self) -> "JournalHeader": ...

    def check_capacity(self, record_bytes: tuple[int, ...]) -> None: ...


class Ledger:
    def __init__(
        self, *, sink: LedgerSink | None = None, restored_rows: tuple[LedgerRow, ...] = ()
    ) -> None:
        if tuple(row.sequence for row in restored_rows) != tuple(range(len(restored_rows))):
            raise ValueError("restored ledger must be contiguous")
        self._rows: list[LedgerRow] = list(restored_rows)
        self._sink = sink

    def append(self, row: LedgerRow) -> None:
        if row.sequence != len(self._rows):
            raise ValueError("ledger sequence must match append position")
        if self._sink is not None:
            self._sink.append(row)
        self._rows.append(row)

    def finish(self, harvest: Harvest) -> None:
        if harvest.ledger != self.snapshot():
            raise ValueError("completion must preserve the exact observed ledger")
        if self._sink is not None:
            self._sink.finish(harvest)

    def close(self) -> None:
        if self._sink is not None:
            self._sink.close()

    @property
    def next_sequence(self) -> int:
        return len(self._rows)

    def has_durable_binding(self, config: GhimeraConfig) -> bool:
        """An arbitrary sink or differently configured journal is not admission."""
        return isinstance(self._sink, DurableLedgerSink) and self._sink.effective_config == config

    def snapshot(self) -> tuple[LedgerRow, ...]:
        return tuple(self._rows)

    def has_replay_binding(self, config: GhimeraConfig) -> bool:
        return (
            isinstance(self._sink, ReplayLedgerSink)
            and self._sink.effective_config == config
            and self._sink.committed_rows == self.snapshot()
        )

    def run_header(self, config: GhimeraConfig) -> "JournalHeader":
        if not isinstance(self._sink, RunLedgerSink) or not self.has_replay_binding(config):
            raise ValueError("run encoding requires its real native journal header and prefix")
        header = self._sink.header
        if header.config != config:
            raise ValueError("native run header changed its effective recipe")
        return header

    def check_capacity(self, config: GhimeraConfig, record_bytes: tuple[int, ...]) -> None:
        self.run_header(config)
        if not isinstance(self._sink, RunLedgerSink):
            raise ValueError("run encoding requires native bounded journal capacity")
        self._sink.check_capacity(record_bytes)
