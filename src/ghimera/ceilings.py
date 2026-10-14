"""Optional run ceilings: a declared limit, or explicitly none.

A self-hosted deployment is not a metered API. Every run ceiling named here may
be configured as ``"unbounded"`` (or ``0``/``null``), which parses to ``None``
and means *no limit*. A configured number is still an exact ceiling, enforced
at its existing spend boundary and recorded in the effective recipe.

``wall_seconds`` may be unbounded too; internally its remaining time is then
``UNBOUNDED_WALL_SECONDS`` (one year), so every per-call deadline derived from it
stays a finite number. Existing integer recipes parse and serialize unchanged.
"""

from typing import Annotated

from pydantic import BeforeValidator, Field

UNBOUNDED = "unbounded"
UNBOUNDED_WALL_SECONDS = 365.0 * 24 * 3600
UNBOUNDED_BYTES = 2**62


def _unbounded(value: object) -> object:
    if value is None or (type(value) in {int, float} and value == 0):
        return None
    if isinstance(value, str) and value.strip().lower() == UNBOUNDED:
        return None
    return value


Ceiling = Annotated[Annotated[int, Field(strict=True, gt=0)] | None, BeforeValidator(_unbounded)]
SecondsCeiling = Annotated[
    Annotated[float, Field(gt=0, allow_inf_nan=False)] | None, BeforeValidator(_unbounded)
]


def exceeds(used: float, limit: float | None) -> bool:
    """``used`` is over a declared ceiling; an unbounded ceiling is never exceeded."""
    return limit is not None and used > limit


def reached(used: float, limit: float | None) -> bool:
    """``used`` has met a declared ceiling, so one more unit would exceed it."""
    return limit is not None and used >= limit


def headroom(limit: int | None, used: int) -> int:
    return UNBOUNDED_BYTES if limit is None else limit - used


def describe(limit: float | None) -> str:
    return UNBOUNDED if limit is None else f"{limit:g}"
