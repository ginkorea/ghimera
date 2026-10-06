"""Run-owned append-only ledger; immutable snapshots rather than exposed lists."""

from chimera.models import LedgerRow


class Ledger:
    def __init__(self) -> None:
        self._rows: list[LedgerRow] = []

    def append(self, row: LedgerRow) -> None:
        if row.sequence != len(self._rows):
            raise ValueError("ledger sequence must match append position")
        self._rows.append(row)

    @property
    def next_sequence(self) -> int:
        return len(self._rows)

    def snapshot(self) -> tuple[LedgerRow, ...]:
        return tuple(self._rows)
