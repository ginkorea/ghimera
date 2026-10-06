"""Column order regression drawn from the actual raster-PDF failure."""

import pytest

from chimera.document_order import LayoutBlock, ReadingOrderPolicy, column_order


def policy(**updates):
    return ReadingOrderPolicy.model_validate(
        dict(
            {
                "schema": "chimera.reading-order/1",
                "method": "xy_cut",
                "column_direction": "left_to_right",
                "min_column_gap_ratio": 0.015,
                "min_row_gap_ratio": 0.015,
                "max_recursion_depth": 32,
            },
            **updates,
        )
    )


def page():
    heading = LayoutBlock(0, 10, 10, 95, 18)
    # Interleaved line observations, just like the actual failed OCR output.
    lines = tuple(
        LayoutBlock(i * 2 + c + 1, x, 30 + i * 10, x + 30, 34 + i * 10)
        for i in range(3)
        for c, x in enumerate((10, 60))
    )
    table = LayoutBlock(7, 10, 75, 95, 95)
    return (heading, *lines, table)


def test_columns_are_read_whole_and_heading_table_bands_stay_in_place():
    assert column_order(page(), policy(), page_width=100, page_height=100) == (
        0,
        1,
        3,
        5,
        2,
        4,
        6,
        7,
    )


def test_right_to_left_columns_are_an_explicit_choice():
    assert column_order(
        page(), policy(column_direction="right_to_left"), page_width=100, page_height=100
    ) == (0, 2, 4, 6, 1, 3, 5, 7)


def test_vendor_order_is_unchanged_and_depth_limit_is_bounded():
    blocks = page()
    assert column_order(blocks, policy(method="vendor"), page_width=100, page_height=100) == tuple(
        b.index for b in blocks
    )
    assert sorted(
        column_order(blocks, policy(max_recursion_depth=1), page_width=100, page_height=100)
    ) == list(range(8))
    assert column_order((), policy(), page_width=100, page_height=100) == ()


def test_ambiguous_overlapping_boxes_are_never_dropped_or_duplicated():
    blocks = (LayoutBlock(0, 10, 10, 80, 30), LayoutBlock(1, 20, 20, 90, 40))
    assert column_order(blocks, policy(), page_width=100, page_height=100) == (0, 1)
    with pytest.raises(ValueError):
        column_order(blocks + (blocks[0],), policy(), page_width=100, page_height=100)
    with pytest.raises(ValueError):
        column_order(
            (LayoutBlock(0, 0, 0, float("nan"), 1),), policy(), page_width=100, page_height=100
        )
