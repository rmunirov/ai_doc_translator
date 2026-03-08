"""Quality metrics for layout-preserving assembly."""

from __future__ import annotations

from app.models.schemas import Block
from app.services.pdf_layout_translator import TextBlock


def _bbox_iou(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    """Compute IoU for two bboxes."""
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - inter
    if union <= 0:
        return 0.0
    return inter / union


def layout_preservation_score(
    original_blocks: list[TextBlock], drawn_blocks: list[TextBlock]
) -> float:
    """Return average bbox IoU for aligned block pairs."""
    if not original_blocks or not drawn_blocks:
        return 0.0
    scores: list[float] = []
    for source, drawn in zip(original_blocks, drawn_blocks):
        scores.append(_bbox_iou(source.bbox, drawn.bbox))
    return sum(scores) / len(scores) if scores else 0.0


def style_preservation_rate(original_blocks: list[Block], drawn_blocks: list[TextBlock]) -> float:
    """Return fraction of blocks preserving size / bold / color intent."""
    if not original_blocks or not drawn_blocks:
        return 0.0
    compared = 0
    matched = 0
    for source, drawn in zip(original_blocks, drawn_blocks):
        if source.font_size is None:
            continue
        compared += 1
        size_close = abs(float(source.font_size) - float(drawn.size)) < 0.01
        bold_expected = source.is_bold
        bold_actual = "b" in drawn.font.lower()
        color_match = True
        if source.font_color:
            source_color = source.font_color.lower()
            drawn_hex = "#{:02x}{:02x}{:02x}".format(*drawn.color)
            color_match = source_color == drawn_hex
        if size_close and bold_expected == bold_actual and color_match:
            matched += 1
    if compared == 0:
        return 1.0
    return matched / compared


def overflow_resolution_rate(total_blocks: int, failed_blocks: int) -> float:
    """Return the share of blocks that were placed successfully."""
    if total_blocks <= 0:
        return 1.0
    return max(0.0, (total_blocks - failed_blocks) / total_blocks)


def overlap_count(blocks: list[TextBlock]) -> int:
    """Count overlapping block pairs on the same page and column."""
    count = 0
    for idx, first in enumerate(blocks):
        for second in blocks[idx + 1 :]:
            if first.page_index != second.page_index:
                continue
            if first.column_id != second.column_id:
                continue
            if _bbox_iou(first.bbox, second.bbox) > 0:
                count += 1
    return count


def reading_order_violations(blocks: list[TextBlock]) -> int:
    """Count cases where a later block appears above the previous one."""
    grouped: dict[tuple[int, int], list[TextBlock]] = {}
    for block in blocks:
        grouped.setdefault((block.page_index, block.column_id), []).append(block)
    violations = 0
    for _, items in grouped.items():
        prev_top: float | None = None
        for block in items:
            current_top = float(block.bbox[1])
            if prev_top is not None and current_top + 1.0 < prev_top:
                violations += 1
            prev_top = current_top
    return violations
