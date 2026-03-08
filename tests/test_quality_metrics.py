"""Tests for assembly quality metrics."""

from app.models.schemas import Block, BlockType
from app.services.pdf_layout_translator import TextBlock
from app.services.quality_metrics import (
    layout_preservation_score,
    overlap_count,
    overflow_resolution_rate,
    reading_order_violations,
    style_preservation_rate,
)


def test_layout_preservation_score_computes_iou_average() -> None:
    originals = [
        TextBlock(
            page_index=0,
            bbox=(0.0, 0.0, 100.0, 20.0),
            text="A",
            size=12.0,
            color=(0, 0, 0),
        )
    ]
    drawn = [
        TextBlock(
            page_index=0,
            bbox=(0.0, 0.0, 100.0, 20.0),
            text="A",
            size=12.0,
            color=(0, 0, 0),
        )
    ]
    assert layout_preservation_score(originals, drawn) == 1.0


def test_style_preservation_rate_uses_size_bold_and_color() -> None:
    originals = [
        Block(
            type=BlockType.PARAGRAPH,
            text="A",
            font_size=11.0,
            is_bold=True,
            font_color="#000000",
        )
    ]
    drawn = [
        TextBlock(
            page_index=0,
            bbox=(0.0, 0.0, 100.0, 20.0),
            text="A",
            font="helvb",
            size=11.0,
            color=(0, 0, 0),
        )
    ]
    assert style_preservation_rate(originals, drawn) == 1.0


def test_overflow_resolution_rate_handles_partial_success() -> None:
    assert overflow_resolution_rate(10, 2) == 0.8


def test_overlap_count_counts_same_page_column_overlaps() -> None:
    blocks = [
        TextBlock(
            page_index=0,
            bbox=(0.0, 0.0, 10.0, 10.0),
            text="A",
            color=(0, 0, 0),
            column_id=0,
        ),
        TextBlock(
            page_index=0,
            bbox=(5.0, 5.0, 12.0, 12.0),
            text="B",
            color=(0, 0, 0),
            column_id=0,
        ),
    ]
    assert overlap_count(blocks) == 1


def test_reading_order_violations_detect_out_of_order_vertical_flow() -> None:
    blocks = [
        TextBlock(
            page_index=0,
            bbox=(0.0, 40.0, 10.0, 50.0),
            text="Late",
            color=(0, 0, 0),
            column_id=0,
        ),
        TextBlock(
            page_index=0,
            bbox=(0.0, 10.0, 10.0, 20.0),
            text="Early",
            color=(0, 0, 0),
            column_id=0,
        ),
    ]
    assert reading_order_violations(blocks) == 1
