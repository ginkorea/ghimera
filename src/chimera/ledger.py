"""Run-owned append-only ledger; immutable snapshots rather than exposed lists."""

from typing import Protocol

from chimera.models import Harvest, LedgerRow


class LedgerSink(Protocol):
    def append(self, row: LedgerRow) -> None: ...

    def finish(self, harvest: Harvest) -> None: ...

    def close(self) -> None: ...


class Ledger:
    def __init__(self, *, sink: LedgerSink | None = None) -> None:
        self._rows: list[LedgerRow] = []
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

    def snapshot(self) -> tuple[LedgerRow, ...]:
        return tuple(self._rows)
