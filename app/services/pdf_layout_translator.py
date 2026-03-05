"""PyMuPDF-based text replacement in-place preserving PDF layout."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Protocol

import pymupdf as fitz
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

_DEFAULT_FONT = "helv"
_UNICODE_FONT_PATHS = [
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/tahoma.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
]


class Translator(Protocol):
    """Translation contract for PDF text blocks."""

    def translate(self, text: str, src: str, tgt: str) -> str:
        """Translate one text fragment.

        Args:
            text: Source text.
            src: Source language code.
            tgt: Target language code.

        Returns:
            Translated text.
        """


class TextBlock(BaseModel):
    """Text fragment with coordinates and visual style."""

    page_index: int
    bbox: tuple[float, float, float, float]
    text: str
    font: str = _DEFAULT_FONT
    size: float = 10.0
    color: tuple[int, int, int] = (0, 0, 0)
    line_height: float | None = None
    translated: str | None = None

    model_config = {"frozen": False}

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("TextBlock.text must not be empty")
        return cleaned

    @field_validator("bbox")
    @classmethod
    def _validate_bbox(
        cls, value: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = value
        if x1 <= x0 or y1 <= y0:
            raise ValueError("TextBlock.bbox must have positive area")
        return value

    @property
    def rect(self) -> fitz.Rect:
        """Return bbox as a PyMuPDF Rect."""
        return fitz.Rect(self.bbox)


def _color_int_to_rgb(color_value: int) -> tuple[int, int, int]:
    """Convert PyMuPDF color integer to RGB tuple."""
    red = (color_value >> 16) & 0xFF
    green = (color_value >> 8) & 0xFF
    blue = color_value & 0xFF
    return (red, green, blue)


def _rgb_to_fitz(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """Convert 0..255 RGB to PyMuPDF color tuple 0..1."""
    return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)


def _is_ascii_text(text: str) -> bool:
    """Return True if text contains ASCII-only characters."""
    return all(ord(ch) < 128 for ch in text)


def _font_candidates(
    preferred_font: str, translated_text: str
) -> list[tuple[str, str | None]]:
    """Build ordered font candidates for insert_textbox."""
    candidates: list[tuple[str, str | None]] = []

    if not _is_ascii_text(translated_text):
        for idx, font_path in enumerate(_UNICODE_FONT_PATHS):
            if font_path.exists():
                candidates.append((f"unicode_{idx}", str(font_path)))

    candidates.append((preferred_font, None))
    if preferred_font != _DEFAULT_FONT:
        candidates.append((_DEFAULT_FONT, None))

    return candidates


def _rects_overlap(a: fitz.Rect, b: fitz.Rect) -> bool:
    """Return True if two rectangles overlap."""
    if a.x1 <= b.x0 or a.x0 >= b.x1:
        return False
    if a.y1 <= b.y0 or a.y0 >= b.y1:
        return False
    return True


def _candidate_rects(
    rect: fitz.Rect, page_rect: fitz.Rect, max_passes: int = 5
) -> list[fitz.Rect]:
    """Build candidate rectangles for local expansion."""
    width = max(1.0, rect.width)
    height = max(1.0, rect.height)
    candidates: list[fitz.Rect] = [fitz.Rect(rect)]
    for step in range(1, max_passes + 1):
        extra_h = height * 0.35 * step
        extra_w = width * 0.2 * step
        down = fitz.Rect(rect.x0, rect.y0, rect.x1, min(page_rect.y1, rect.y1 + extra_h))
        right = fitz.Rect(rect.x0, rect.y0, min(page_rect.x1, rect.x1 + extra_w), rect.y1)
        down_right = fitz.Rect(
            rect.x0,
            rect.y0,
            min(page_rect.x1, rect.x1 + extra_w),
            min(page_rect.y1, rect.y1 + extra_h),
        )
        candidates.extend([down, right, down_right])
    unique: list[fitz.Rect] = []
    for item in candidates:
        if item.width <= 0 or item.height <= 0:
            continue
        if any(existing == item for existing in unique):
            continue
        unique.append(item)
    return unique


def extract_text_blocks(doc: fitz.Document) -> list[TextBlock]:
    """Extract span-level text blocks with style data from a PDF.

    Args:
        doc: Open PyMuPDF document.

    Returns:
        List of span-level text blocks.
    """
    blocks: list[TextBlock] = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        page_dict = page.get_text("dict", sort=True)
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = str(span.get("text", "")).strip()
                    if not text:
                        continue
                    bbox_raw = span.get("bbox")
                    if not bbox_raw or len(bbox_raw) != 4:
                        continue
                    bbox = (
                        float(bbox_raw[0]),
                        float(bbox_raw[1]),
                        float(bbox_raw[2]),
                        float(bbox_raw[3]),
                    )
                    color_value = int(span.get("color", 0))
                    block_item = TextBlock(
                        page_index=page_index,
                        bbox=bbox,
                        text=text,
                        font=str(span.get("font", _DEFAULT_FONT)),
                        size=float(span.get("size", 10.0)),
                        color=_color_int_to_rgb(color_value),
                    )
                    blocks.append(block_item)
    return blocks


def translate_blocks(
    blocks: list[TextBlock],
    translator: Translator,
    src_lang: str,
    tgt_lang: str,
) -> list[TextBlock]:
    """Translate text for each block.

    Args:
        blocks: Source blocks.
        translator: Translator implementation.
        src_lang: Source language code.
        tgt_lang: Target language code.

    Returns:
        New list of blocks with ``translated`` field populated.
    """
    translated_blocks: list[TextBlock] = []
    for block in blocks:
        translated_text = translator.translate(block.text, src_lang, tgt_lang)
        translated_blocks.append(
            block.model_copy(update={"translated": translated_text})
        )
    return translated_blocks


def clear_blocks(doc: fitz.Document, blocks: list[TextBlock]) -> None:
    """Clear old text with redactions in each text block area.

    Args:
        doc: Open PyMuPDF document.
        blocks: Blocks to clear.
    """
    page_rects: dict[int, list[fitz.Rect]] = defaultdict(list)
    for block in blocks:
        page_rects[block.page_index].append(block.rect)

    for page_index, rects in page_rects.items():
        page = doc[page_index]
        for rect in rects:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()


def draw_translated_block(
    page: fitz.Page,
    block: TextBlock,
    min_font: float = 6.0,
    font_delta: float = 0.0,
) -> bool:
    """Draw translated text into block bbox using expansion-first strategy.

    Args:
        page: Target PDF page.
        block: Target text block.
        min_font: Kept for backward compatibility; ignored by layout strategy.
        font_delta: Optional size adjustment added to original font size.

    Returns:
        True if text was fully inserted into the block; otherwise False.
    """
    text = (block.translated or "").strip()
    if not text:
        return True

    fontsize = max(1.0, block.size + font_delta)
    color = _rgb_to_fitz(block.color)
    font_name = block.font or _DEFAULT_FONT
    candidates = _font_candidates(font_name, text)
    lineheight = None
    if block.line_height and fontsize > 0:
        lineheight = max(0.8, block.line_height / fontsize)

    for rect in _candidate_rects(block.rect, page.rect):
        page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
        for candidate_name, candidate_file in candidates:
            try:
                remaining = page.insert_textbox(
                    rect,
                    text,
                    fontsize=fontsize,
                    fontname=candidate_name,
                    fontfile=candidate_file,
                    color=color,
                    align=fitz.TEXT_ALIGN_LEFT,
                    overlay=True,
                    lineheight=lineheight,
                )
                if remaining >= 0:
                    block.bbox = (rect.x0, rect.y0, rect.x1, rect.y1)
                    return True
            except Exception as exc:
                logger.warning(
                    "Failed font candidate '%s' for page %d: %s",
                    candidate_name,
                    block.page_index,
                    exc,
                )

    logger.warning(
        "Translated text does not fit block bbox page=%d bbox=%s",
        block.page_index,
        block.bbox,
    )
    return False


def draw_translated_blocks(doc: fitz.Document, blocks: list[TextBlock]) -> int:
    """Draw translated text for all blocks in the document.

    Args:
        doc: Open PyMuPDF document.
        blocks: Blocks containing translated text.

    Returns:
        Number of blocks that failed to draw.
    """
    failed_count = 0
    page_blocks: dict[int, list[TextBlock]] = defaultdict(list)
    for block in blocks:
        page_blocks[block.page_index].append(block)

    for page_index, page_group in page_blocks.items():
        page = doc[page_index]
        occupied: list[fitz.Rect] = []
        sorted_blocks = sorted(
            page_group,
            key=lambda item: (item.bbox[1], item.bbox[0]),
        )
        for block in sorted_blocks:
            original_rect = block.rect
            placed = False
            for candidate_rect in _candidate_rects(original_rect, page.rect):
                moved_rect = fitz.Rect(candidate_rect)
                max_shifts = 8
                for _ in range(max_shifts):
                    collisions = [
                        existing
                        for existing in occupied
                        if _rects_overlap(moved_rect, existing)
                    ]
                    if not collisions:
                        break
                    max_bottom = max(item.y1 for item in collisions)
                    delta = max_bottom - moved_rect.y0 + 2.0
                    moved_rect = fitz.Rect(
                        moved_rect.x0,
                        moved_rect.y0 + delta,
                        moved_rect.x1,
                        moved_rect.y1 + delta,
                    )
                    if moved_rect.y1 > page.rect.y1:
                        break
                if moved_rect.y1 > page.rect.y1:
                    continue

                tmp_block = block.model_copy(
                    update={
                        "bbox": (
                            moved_rect.x0,
                            moved_rect.y0,
                            moved_rect.x1,
                            moved_rect.y1,
                        )
                    }
                )
                if draw_translated_block(page, tmp_block):
                    block.bbox = tmp_block.bbox
                    occupied.append(fitz.Rect(block.bbox))
                    placed = True
                    break

            if not placed:
                failed_count += 1
    if failed_count:
        logger.warning("Failed to draw %d translated blocks", failed_count)
    return failed_count


def translate_pdf(
    input_path: str,
    output_path: str,
    translator: Translator,
    src_lang: str,
    tgt_lang: str,
) -> None:
    """Translate a PDF in-place by replacing text inside source bboxes.

    Flow:
      1) Open source PDF.
      2) Extract text blocks with style and bbox.
      3) Translate block text through ``translator``.
      4) Clear old text via redactions.
      5) Draw translated text in original bboxes.
      6) Save result PDF.

    Args:
        input_path: Path to source PDF.
        output_path: Path for translated output PDF.
        translator: Translation provider implementation.
        src_lang: Source language code.
        tgt_lang: Target language code.
    """
    with fitz.open(input_path) as doc:
        blocks = extract_text_blocks(doc)
        translated_blocks = translate_blocks(blocks, translator, src_lang, tgt_lang)
        clear_blocks(doc, translated_blocks)
        failed_count = draw_translated_blocks(doc, translated_blocks)
        if failed_count:
            logger.warning(
                "translate_pdf completed with %d undrawn blocks",
                failed_count,
            )
        doc.save(output_path, garbage=4, deflate=True)
