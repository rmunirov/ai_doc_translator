"""Parsers that convert PDF, HTML, and TXT files into a structured ParsedDocument."""

import asyncio
import concurrent.futures
import logging
import re
import uuid
from pathlib import Path
from typing import Any

import aiofiles
import pymupdf
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from app.config import get_settings
from app.models.schemas import (
    Block,
    BlockType,
    DocumentLayoutIR,
    LayoutBlock,
    PageLayout,
    ParsedDocument,
)

logger = logging.getLogger(__name__)

_PDF_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="pdf_parser"
)

_HEADING_TAG_LEVELS: dict[str, int] = {
    "h1": 1,
    "h2": 2,
    "h3": 3,
    "h4": 4,
    "h5": 5,
    "h6": 6,
}

_HTML_TAGS_OF_INTEREST = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "li", "table", "caption",
]

# PyMuPDF span flags: bold = 2^4
_FLAG_BOLD = 16

# Footnote detection: text in bottom 15% of page with small font
_FOOTNOTE_Y_THRESHOLD = 0.85
_FOOTNOTE_MAX_FONT_SIZE = 9.0

# Paragraph merge heuristics
_PARAGRAPH_MERGE_GAP_MIN = 4.0
_PARAGRAPH_MERGE_GAP_MAX = 28.0
_PARAGRAPH_MERGE_INDENT_DELTA = 26.0
_PARAGRAPH_MERGE_GAP_MULTIPLIER = 1.1
_PARAGRAPH_MERGE_FONT_TOLERANCE = 1.4
_PARAGRAPH_MERGE_Y_GAP_MIN = -3.0


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _classify_heading(font_size: float, is_bold: bool) -> int:
    """Return heading level (1-3) by font size, or 0 if not a heading."""
    if not is_bold:
        return 0
    if font_size >= 22:
        return 1
    if font_size >= 18:
        return 2
    if font_size >= 16:
        return 3
    return 0


def _table_to_text(cells: list[list[str | None]]) -> str:
    """Flatten table cells to pipe-delimited string."""
    rows: list[str] = []
    for row in cells:
        cells_str = [str(c) if c is not None else "" for c in row]
        rows.append(" | ".join(cells_str))
    return "\n".join(rows)


# Page number/footer marker: "— 1 of 9 —", "-- 3 of 9 --", etc.
_PAGE_MARKER_RE = re.compile(
    r"^\s*[—\-]+\s*\d+\s+of\s+\d+\s*[—\-]*\s*$",
    re.IGNORECASE,
)


def _is_page_marker(text: str) -> bool:
    """Return True if text is a page number marker (e.g. '— 1 of 9 —')."""
    return bool(_PAGE_MARKER_RE.match(text.strip()))


def _ends_sentence(text: str) -> bool:
    """True if text ends with sentence-ending punctuation."""
    return bool(text and text.rstrip()[-1:] in ".!?:")


def _starts_new_paragraph(text: str) -> bool:
    """True if text starts with capital or digit (typical paragraph start)."""
    if not text or not text.strip():
        return False
    first = text.strip()[0]
    return first.isupper() or first.isdigit()


_LIST_BULLET_RE = re.compile(r"^\s*(?:[-*•]|(?:\d+[\).\]])|(?:[A-Za-z][\).\]]))\s+")
_CAPTION_RE = re.compile(r"^\s*(?:figure|fig\.|table|табл\.|рис\.)\s*\d*", re.IGNORECASE)
_FORM_FIELD_RE = re.compile(r"^\s*[^\n:]{1,40}\s*:\s*(?:_+|\.{2,}|\S.*)$")


def _bboxes_overlap(
    bbox1: tuple[float, float, float, float],
    bbox2: tuple[float, float, float, float],
) -> bool:
    """Check whether two (x0, y0, x1, y1) rectangles overlap."""
    x0_1, y0_1, x1_1, y1_1 = bbox1
    x0_2, y0_2, x1_2, y1_2 = bbox2
    if x1_1 <= x0_2 or x0_1 >= x1_2:
        return False
    if y1_1 <= y0_2 or y0_1 >= y1_2:
        return False
    return True


def _span_bbox(span: dict[str, Any]) -> tuple[float, float, float, float]:
    """Extract (x0, y0, x1, y1) from PyMuPDF span bbox."""
    bbox = span.get("bbox", (0, 0, 0, 0))
    return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))


def _merge_bbox(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Return union bbox for two rectangles."""
    return (
        min(a[0], b[0]),
        min(a[1], b[1]),
        max(a[2], b[2]),
        max(a[3], b[3]),
    )


def _color_to_hex(color: int) -> str:
    """Convert PyMuPDF color int to hex #RRGGBB."""
    return f"#{color & 0xFFFFFF:06x}"


def _extract_tag_text_nodes(tag: Tag) -> list[str]:
    """Extract non-empty text nodes from a tag subtree."""
    nodes: list[str] = []
    for node in tag.descendants:
        if isinstance(node, NavigableString):
            text = str(node)
            if text.strip():
                nodes.append(text)
    return nodes


def _line_gap(block: Block) -> float:
    """Approximate visual line gap for a block."""
    if block.bbox is None:
        return 0.0
    return float(block.bbox[3] - block.bbox[1])


def _merge_page_paragraph_fragments(page_blocks: list[Block]) -> list[Block]:
    """Merge fragmented paragraph blocks into fuller paragraph units."""
    if not page_blocks:
        return page_blocks
    mids = [
        (float(block.bbox[0]) + float(block.bbox[2])) / 2
        for block in page_blocks
        if block.bbox is not None
    ]
    split_x = sorted(mids)[len(mids) // 2] if len(mids) >= 8 else None

    def _column_id(block: Block) -> int:
        if block.bbox is None or split_x is None:
            return 0
        x_mid = (float(block.bbox[0]) + float(block.bbox[2])) / 2
        return 1 if x_mid > split_x else 0

    merged: list[Block] = []
    for block in page_blocks:
        if (
            merged
            and block.type == BlockType.PARAGRAPH
            and merged[-1].type == BlockType.PARAGRAPH
            and block.bbox is not None
            and merged[-1].bbox is not None
        ):
            prev = merged[-1]
            prev_bbox = merged[-1].bbox
            if prev_bbox is None:
                merged.append(block)
                continue
            x_delta = abs(block.bbox[0] - prev_bbox[0])
            y_gap = block.bbox[1] - prev_bbox[3]
            gap_limit = max(
                _PARAGRAPH_MERGE_GAP_MIN,
                min(
                    _PARAGRAPH_MERGE_GAP_MAX,
                    max(_line_gap(block), _line_gap(prev))
                    * _PARAGRAPH_MERGE_GAP_MULTIPLIER,
                ),
            )
            similar_font = (
                prev.font_size is None
                or block.font_size is None
                or abs(prev.font_size - block.font_size)
                <= _PARAGRAPH_MERGE_FONT_TOLERANCE
            )
            same_column = _column_id(block) == _column_id(prev)
            continuation = not _ends_sentence(prev.text)
            same_indent = x_delta <= _PARAGRAPH_MERGE_INDENT_DELTA
            if (
                same_column
                and similar_font
                and _PARAGRAPH_MERGE_Y_GAP_MIN <= y_gap <= gap_limit
                and (same_indent or continuation)
            ):
                merged_text = f"{prev.text} {block.text}".strip()
                merged[-1] = prev.model_copy(
                    update={
                        "text": merged_text,
                        "bbox": _merge_bbox(prev_bbox, block.bbox),
                        "style_runs": (prev.style_runs or []) + (block.style_runs or []),
                    }
                )
                continue
        merged.append(block)
    return merged


# ---------------------------------------------------------------------------
# DocumentParser
# ---------------------------------------------------------------------------


class DocumentParser:
    """Parses uploaded documents into a list of typed Block objects."""

    async def parse(self, path: str) -> ParsedDocument:
        """Determine file format by extension and delegate to the proper parser.

        Args:
            path: Filesystem path to the uploaded document.

        Returns:
            A ParsedDocument with structured blocks and metadata.

        Raises:
            ValueError: If the file extension is not supported.
            FileNotFoundError: If the file does not exist.
        """
        p = Path(path)
        ext = p.suffix.lower()

        if ext not in {".pdf", ".html", ".htm", ".txt"}:
            raise ValueError(f"Unsupported file extension: {ext}")

        if not p.exists():
            raise FileNotFoundError(f"File not found: {path}")

        try:
            size = p.stat().st_size
        except OSError as exc:
            raise FileNotFoundError(f"Cannot access file: {path}") from exc

        max_size = get_settings().max_file_size_mb * 1024 * 1024
        if size > max_size:
            raise ValueError(
                f"File too large: {size} bytes (max {max_size} bytes)"
            )

        if ext == ".pdf":
            blocks, metadata = await self._parse_pdf(path)
            return ParsedDocument(format="pdf", blocks=blocks, metadata=metadata)

        if ext in {".html", ".htm"}:
            blocks, metadata = await self._parse_html(path)
            return ParsedDocument(format="html", blocks=blocks, metadata=metadata)

        blocks = await self._parse_txt(path)
        return ParsedDocument(format="txt", blocks=blocks)

    # ------------------------------------------------------------------
    # TXT
    # ------------------------------------------------------------------

    async def _parse_txt(self, path: str) -> list[Block]:
        """Split a plain-text file by double newlines into PARAGRAPH blocks."""
        async with aiofiles.open(path, encoding="utf-8") as f:
            content = await f.read()

        blocks: list[Block] = []
        for fragment in content.split("\n\n"):
            text = fragment.strip()
            if text:
                blocks.append(Block(type=BlockType.PARAGRAPH, text=text))

        return blocks

    # ------------------------------------------------------------------
    # HTML
    # ------------------------------------------------------------------

    async def _parse_html(
        self, path: str
    ) -> tuple[list[Block], dict[str, Any]]:
        """Walk the DOM tree and convert semantic tags into Block objects."""
        async with aiofiles.open(path, encoding="utf-8") as f:
            content = await f.read()

        soup = BeautifulSoup(content, "html.parser")

        blocks: list[Block] = []
        html_text_nodes: list[list[str]] = []
        for tag in soup.find_all(_HTML_TAGS_OF_INTEREST):
            if not isinstance(tag, Tag):
                continue

            text = tag.get_text(" ", strip=True)
            if not text:
                continue

            tag_name = tag.name.lower()
            raw_html = str(tag)
            text_nodes = _extract_tag_text_nodes(tag)

            if tag_name in _HEADING_TAG_LEVELS:
                blocks.append(
                    Block(
                        type=BlockType.HEADING,
                        text=text,
                        level=_HEADING_TAG_LEVELS[tag_name],
                        raw_html=raw_html,
                    )
                )
                html_text_nodes.append(text_nodes)
            elif tag_name == "p":
                blocks.append(
                    Block(type=BlockType.PARAGRAPH, text=text, raw_html=raw_html)
                )
                html_text_nodes.append(text_nodes)
            elif tag_name == "li":
                blocks.append(
                    Block(type=BlockType.LIST_ITEM, text=text, raw_html=raw_html)
                )
                html_text_nodes.append(text_nodes)
            elif tag_name == "table":
                blocks.append(
                    Block(type=BlockType.TABLE, text=text, raw_html=raw_html)
                )
                html_text_nodes.append(text_nodes)
            elif tag_name == "caption":
                blocks.append(
                    Block(type=BlockType.CAPTION, text=text, raw_html=raw_html)
                )
                html_text_nodes.append(text_nodes)

        metadata: dict[str, Any] = {
            "soup_str": str(soup),
            "html_text_nodes": html_text_nodes,
        }
        return blocks, metadata

    # ------------------------------------------------------------------
    # PDF (PyMuPDF)
    # ------------------------------------------------------------------

    async def _parse_pdf(
        self, path: str
    ) -> tuple[list[Block], dict[str, Any]]:
        """Extract text, tables, and images from each page of a PDF file."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _PDF_EXECUTOR, self._parse_pdf_sync, path
        )

    def _parse_pdf_sync(
        self, path: str
    ) -> tuple[list[Block], dict[str, Any]]:
        """Synchronous core of the PDF parser using PyMuPDF."""
        blocks: list[Block] = []
        images_meta: list[dict[str, Any]] = []
        page_sizes: dict[int, tuple[float, float]] = {}

        with pymupdf.open(path) as doc:
            page_count = len(doc)

            for page_idx in range(page_count):
                page = doc[page_idx]
                page_sizes[page_idx] = (float(page.rect.width), float(page.rect.height))
                page_blocks_start = len(blocks)

                # 1. Tables
                table_bboxes: list[tuple[float, float, float, float]] = []
                finder = page.find_tables()
                for table in finder.tables:
                    tbl_bbox = (
                        table.bbox.x0,
                        table.bbox.y0,
                        table.bbox.x1,
                        table.bbox.y1,
                    )
                    table_bboxes.append(tbl_bbox)
                    cells = table.extract()
                    if cells:
                        text = _table_to_text(cells)
                        if text.strip():
                            blocks.append(
                                Block(
                                    type=BlockType.TABLE,
                                    text=text,
                                    bbox=tbl_bbox,
                                    page_index=page_idx,
                                    table_cells=[
                                        [
                                            str(cell) if cell is not None else ""
                                            for cell in row
                                        ]
                                        for row in cells
                                    ],
                                    non_translatable=False,
                                )
                            )

                # 2. Text from get_text("dict")
                blocks_dict = page.get_text("dict", sort=True)
                para_buffer: list[str] = []
                para_runs: list[dict[str, Any]] = []
                para_meta: dict[str, Any] = {}

                def flush_paragraph() -> None:
                    if para_buffer:
                        text = " ".join(para_buffer)
                        blocks.append(
                            Block(
                                type=BlockType.PARAGRAPH,
                                text=text,
                                bbox=para_meta.get("bbox"),
                                font_size=para_meta.get("font_size", 10.0),
                                is_bold=para_meta.get("is_bold", False),
                                font_color=para_meta.get("font_color"),
                                font_name=para_meta.get("font_name"),
                                line_height=para_meta.get("line_height"),
                                style_runs=para_runs.copy() if para_runs else None,
                                page_index=page_idx,
                            )
                        )
                        para_buffer.clear()
                        para_runs.clear()
                        para_meta.clear()

                for block in blocks_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            span_text = span.get("text", "").strip()
                            if not span_text:
                                continue
                            span_bbox = _span_bbox(span)
                            if any(
                                _bboxes_overlap(span_bbox, tb)
                                for tb in table_bboxes
                            ):
                                continue
                            if _is_page_marker(span_text):
                                continue

                            font_size = float(span.get("size", 10))
                            flags = int(span.get("flags", 0))
                            bold = bool(flags & _FLAG_BOLD)
                            font_name = str(span.get("font", "helv"))
                            color = span.get("color", 0)
                            font_color = (
                                _color_to_hex(color)
                                if isinstance(color, int) and color != 0
                                else None
                            )
                            y0 = float(span_bbox[1])
                            page_height = float(page.rect.height)

                            if _LIST_BULLET_RE.match(span_text):
                                flush_paragraph()
                                blocks.append(
                                    Block(
                                        type=BlockType.LIST_ITEM,
                                        text=span_text,
                                        bbox=span_bbox,
                                        font_size=font_size,
                                        is_bold=bold,
                                        font_color=font_color,
                                        font_name=font_name,
                                        line_height=font_size * 1.2,
                                        style_runs=[
                                            {
                                                "text": span_text,
                                                "font_name": font_name,
                                                "font_size": font_size,
                                                "is_bold": bold,
                                                "font_color": font_color,
                                                "bbox": span_bbox,
                                            }
                                        ],
                                        page_index=page_idx,
                                    )
                                )
                                continue

                            if _CAPTION_RE.match(span_text):
                                flush_paragraph()
                                blocks.append(
                                    Block(
                                        type=BlockType.CAPTION,
                                        text=span_text,
                                        bbox=span_bbox,
                                        font_size=font_size,
                                        is_bold=bold,
                                        font_color=font_color,
                                        font_name=font_name,
                                        line_height=font_size * 1.2,
                                        page_index=page_idx,
                                    )
                                )
                                continue

                            if _FORM_FIELD_RE.match(span_text):
                                flush_paragraph()
                                blocks.append(
                                    Block(
                                        type=BlockType.FORM_FIELD,
                                        text=span_text,
                                        bbox=span_bbox,
                                        font_size=font_size,
                                        is_bold=bold,
                                        font_color=font_color,
                                        font_name=font_name,
                                        line_height=font_size * 1.2,
                                        page_index=page_idx,
                                        non_translatable=True,
                                    )
                                )
                                continue

                            if (
                                y0 > page_height * _FOOTNOTE_Y_THRESHOLD
                                and font_size <= _FOOTNOTE_MAX_FONT_SIZE
                            ):
                                flush_paragraph()
                                blocks.append(
                                    Block(
                                        type=BlockType.FOOTNOTE,
                                        text=span_text,
                                        bbox=span_bbox,
                                        font_size=font_size,
                                        is_bold=bold,
                                        font_color=font_color,
                                        font_name=font_name,
                                        line_height=font_size * 1.2,
                                        page_index=page_idx,
                                    )
                                )
                                continue

                            heading_level = _classify_heading(font_size, bold)
                            if heading_level > 0:
                                flush_paragraph()
                                blocks.append(
                                    Block(
                                        type=BlockType.HEADING,
                                        text=span_text,
                                        level=heading_level,
                                        bbox=span_bbox,
                                        font_size=font_size,
                                        is_bold=bold,
                                        font_color=font_color,
                                        font_name=font_name,
                                        line_height=font_size * 1.2,
                                        page_index=page_idx,
                                    )
                                )
                            else:
                                prev_ends = (
                                    _ends_sentence(para_buffer[-1])
                                    if para_buffer
                                    else False
                                )
                                starts_new = _starts_new_paragraph(span_text)
                                if para_buffer and prev_ends and starts_new:
                                    flush_paragraph()
                                if not para_meta:
                                    para_meta = {
                                        "bbox": span_bbox,
                                        "font_size": font_size,
                                        "is_bold": bold,
                                        "font_color": font_color,
                                        "font_name": font_name,
                                        "line_height": font_size * 1.2,
                                    }
                                else:
                                    prev_bbox = para_meta.get("bbox")
                                    if prev_bbox:
                                        para_meta["bbox"] = _merge_bbox(
                                            prev_bbox,
                                            span_bbox,
                                        )
                                para_runs.append(
                                    {
                                        "text": span_text,
                                        "font_name": font_name,
                                        "font_size": font_size,
                                        "is_bold": bold,
                                        "font_color": font_color,
                                        "bbox": span_bbox,
                                    }
                                )
                                para_buffer.append(span_text)
                flush_paragraph()

                # 3. Images
                for img_item in page.get_images(full=True):
                    xref = img_item[0]
                    try:
                        img_data = doc.extract_image(xref)
                    except (
                        pymupdf.FileDataError,
                        ValueError,
                        RuntimeError,
                    ) as exc:
                        logger.warning(
                            "Failed to extract image xref=%s: %s",
                            xref,
                            exc,
                        )
                        continue
                    img_bytes = img_data.get("image")
                    img_ext = img_data.get("ext", "png")
                    if not img_bytes:
                        continue
                    rects = page.get_image_rects(xref)
                    for rect in rects:
                        if rect.is_infinite or rect.is_empty:
                            continue
                        bbox = (rect.x0, rect.y0, rect.x1, rect.y1)
                        images_meta.append(
                            {
                                "page_index": page_idx,
                                "bbox": bbox,
                                "image_bytes": img_bytes,
                                "ext": img_ext,
                            }
                        )

                page_slice = blocks[page_blocks_start:]
                merged_page = _merge_page_paragraph_fragments(page_slice)
                blocks[page_blocks_start:] = merged_page

        pages_ir: list[PageLayout] = []
        for page_idx in range(page_count):
            page_blocks = [b for b in blocks if b.page_index == page_idx and b.bbox is not None]
            sorted_blocks = sorted(
                page_blocks,
                key=lambda item: (
                    item.bbox[1] if item.bbox is not None else 0.0,
                    item.bbox[0] if item.bbox is not None else 0.0,
                ),
            )
            layout_blocks: list[LayoutBlock] = []
            for order, block in enumerate(sorted_blocks):
                if block.bbox is None:
                    continue
                layout_blocks.append(
                    LayoutBlock(
                        block_id=f"blk_{page_idx}_{order}_{uuid.uuid4().hex[:8]}",
                        page=page_idx,
                        type=block.type,
                        bbox=block.bbox,
                        text=block.text,
                        style={
                            "font_size": block.font_size,
                            "font_color": block.font_color,
                            "font_name": block.font_name,
                            "is_bold": block.is_bold,
                            "line_height": block.line_height,
                        },
                        reading_order=order,
                        column_id=0,
                        non_translatable=block.non_translatable,
                        table_cells=block.table_cells,
                    )
                )
            width, height = page_sizes.get(page_idx, (595.0, 842.0))
            pages_ir.append(
                PageLayout(page=page_idx, width=width, height=height, blocks=layout_blocks)
            )

        metadata: dict[str, Any] = {
            "page_count": page_count,
            "images": images_meta,
            "source_path": path,
            "layout_ir": DocumentLayoutIR(pages=pages_ir).model_dump(mode="json"),
        }
        return blocks, metadata
