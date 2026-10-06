"""Configurable geometric reading order; preserve blocks, never rewrite prose."""

import math
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReadingOrderPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.reading-order/1"] = Field(alias="schema")
    method: Literal["vendor", "xy_cut"]
    column_direction: Literal["left_to_right", "right_to_left"]
    min_column_gap_ratio: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
    min_row_gap_ratio: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
    max_recursion_depth: Annotated[int, Field(strict=True, gt=0)]


@dataclass(frozen=True)
class LayoutBlock:
    index: int
    left: float
    top: float
    right: float
    bottom: float


def _gap(blocks: tuple[LayoutBlock, ...], *, horizontal: bool, minimum: float) -> float | None:
    intervals = sorted(
        (block.left, block.right) if horizontal else (block.top, block.bottom) for block in blocks
    )
    end = intervals[0][1]
    best_size, split = minimum, None
    for start, finish in intervals[1:]:
        if start - end >= best_size:
            best_size, split = start - end, (start + end) / 2
        end = max(end, finish)
    return split


def column_order(
    blocks: tuple[LayoutBlock, ...],
    policy: ReadingOrderPolicy,
    *,
    page_width: float,
    page_height: float,
) -> tuple[int, ...]:
    """Recursive XY cut over observed boxes, with explicit normalized gap limits.

    A clear vertical gutter orders whole columns before individual lines. Full-
    width headings/tables remove that gutter: a horizontal cut separates their
    bands first. Overlapping/ambiguous bands use stable geometric order. This
    is not a learned semantic reading-order confidence score.
    """
    if (
        not math.isfinite(page_width)
        or not math.isfinite(page_height)
        or page_width <= 0
        or page_height <= 0
        or len({b.index for b in blocks}) != len(blocks)
    ):
        raise ValueError("reading order needs positive page geometry and unique block identities")
    if any(
        not all(math.isfinite(v) for v in (b.left, b.top, b.right, b.bottom))
        or b.left > b.right
        or b.top > b.bottom
        for b in blocks
    ):
        raise ValueError("reading order needs finite, correctly oriented source boxes")
    if policy.method == "vendor":
        return tuple(b.index for b in blocks)
    reverse = policy.column_direction == "right_to_left"

    def fallback(values: tuple[LayoutBlock, ...]) -> tuple[int, ...]:
        return tuple(
            b.index
            for b in sorted(values, key=lambda b: (b.top, -b.left if reverse else b.left, b.index))
        )

    def cut(values: tuple[LayoutBlock, ...], depth: int) -> tuple[int, ...]:
        if len(values) < 2 or depth >= policy.max_recursion_depth:
            return fallback(values)
        split = _gap(values, horizontal=True, minimum=policy.min_column_gap_ratio * page_width)
        if split is not None:
            left = tuple(b for b in values if b.right < split)
            right = tuple(b for b in values if b.left > split)
            if len(left) + len(right) == len(values) and left and right:
                first, second = (right, left) if reverse else (left, right)
                return cut(first, depth + 1) + cut(second, depth + 1)
        split = _gap(values, horizontal=False, minimum=policy.min_row_gap_ratio * page_height)
        if split is not None:
            top = tuple(b for b in values if b.bottom < split)
            bottom = tuple(b for b in values if b.top > split)
            if len(top) + len(bottom) == len(values) and top and bottom:
                return cut(top, depth + 1) + cut(bottom, depth + 1)
        return fallback(values)

    return cut(blocks, 0)
