"""Parsers that convert PDF, HTML, and TXT files into a structured ParsedDocument."""

import asyncio
import io
import logging
import re
from pathlib import Path
from typing import Any

import aiofiles
import pymupdf
from bs4 import BeautifulSoup, Tag

from app.models.schemas import Block, BlockType, ParsedDocument

logger = logging.getLogger(__name__)

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
    "p", "li", "table",
]

# PyMuPDF span flags: bold = 2^4
_FLAG_BOLD = 16


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _classify_heading(font_size: float) -> int:
    """Return heading level (1-3) by font size, or 0 if not a heading."""
    if font_size >= 18:
        return 1
    if font_size >= 14:
        return 2
    if font_size >= 12:
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
        for tag in soup.find_all(_HTML_TAGS_OF_INTEREST):
            if not isinstance(tag, Tag):
                continue

            text = tag.get_text(strip=True)
            if not text:
                continue

            tag_name = tag.name.lower()
            raw_html = str(tag)

            if tag_name in _HEADING_TAG_LEVELS:
                blocks.append(
                    Block(
                        type=BlockType.HEADING,
                        text=text,
                        level=_HEADING_TAG_LEVELS[tag_name],
                        raw_html=raw_html,
                    )
                )
            elif tag_name == "p":
                blocks.append(
                    Block(type=BlockType.PARAGRAPH, text=text, raw_html=raw_html)
                )
            elif tag_name == "li":
                blocks.append(
                    Block(type=BlockType.LIST_ITEM, text=text, raw_html=raw_html)
                )
            elif tag_name == "table":
                blocks.append(
                    Block(type=BlockType.TABLE, text=text, raw_html=raw_html)
                )

        metadata: dict[str, Any] = {"soup_str": str(soup)}
        return blocks, metadata

    # ------------------------------------------------------------------
    # PDF (PyMuPDF)
    # ------------------------------------------------------------------

    async def _parse_pdf(
        self, path: str
    ) -> tuple[list[Block], dict[str, Any]]:
        """Extract text, tables, and images from each page of a PDF file."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._parse_pdf_sync, path)

    def _parse_pdf_sync(
        self, path: str
    ) -> tuple[list[Block], dict[str, Any]]:
        """Synchronous core of the PDF parser using PyMuPDF."""
        blocks: list[Block] = []
        images_meta: list[dict[str, Any]] = []

        with pymupdf.open(path) as doc:
            page_count = len(doc)

            for page_idx in range(page_count):
                page = doc[page_idx]

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
                                )
                            )

                # 2. Text from get_text("dict")
                blocks_dict = page.get_text("dict", sort=True)
                para_buffer: list[str] = []
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
                                page_index=page_idx,
                            )
                        )
                        para_buffer.clear()
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
                            color = span.get("color", 0)
                            font_color = (
                                _color_to_hex(color)
                                if isinstance(color, int) and color != 0
                                else None
                            )

                            heading_level = _classify_heading(font_size)
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
                                    }
                                else:
                                    prev_bbox = para_meta.get("bbox")
                                    if prev_bbox:
                                        para_meta["bbox"] = _merge_bbox(
                                            prev_bbox,
                                            span_bbox,
                                        )
                                para_buffer.append(span_text)
                flush_paragraph()

                # 3. Images
                for img_item in page.get_images(full=True):
                    xref = img_item[0]
                    try:
                        img_data = doc.extract_image(xref)
                    except Exception as exc:
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

        metadata: dict[str, Any] = {
            "page_count": page_count,
            "images": images_meta,
            "source_path": path,
        }
        return blocks, metadata
