"""Assembles translated block texts back into TXT, HTML, or PDF documents."""

import asyncio
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

import aiofiles
import pymupdf as fitz
from bs4 import BeautifulSoup, NavigableString, Tag

from app.models.schemas import (
    Block,
    BlockType,
    Chunk,
    DocumentLayoutIR,
    LayoutBlock,
    ParsedDocument,
)
from app.services.pdf_layout_translator import (
    TextBlock,
    clear_blocks,
    draw_translated_blocks,
)
from app.services import pdf_layout_translator as pdf_layout_runtime
from app.services.quality_metrics import (
    layout_preservation_score,
    overlap_count,
    overflow_resolution_rate,
    reading_order_violations,
    style_preservation_rate,
)

logger = logging.getLogger(__name__)

# Font paths for Cyrillic/Unicode support (regular, bold) — tried in order.
_UTF8_FONT_PATHS = [
    (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
    (Path("C:/Windows/Fonts/tahoma.ttf"), Path("C:/Windows/Fonts/tahomabd.ttf")),
    (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
    ),
    (
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"),
    ),
    (
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ),
]
_UTF8_FONT_NAME = "UnicodeFont"
_UTF8_FONT_BOLD_NAME = "UnicodeFont-Bold"

# Same ordered tag list as document_parser.py — order determines block mapping.
_HTML_TAGS_OF_INTEREST = [
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "li",
    "table",
    "caption",
]

_HEADING_STYLE_MAP = {1: "Heading1", 2: "Heading2", 3: "Heading3"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_translated_text(text: str) -> str:
    """Strip JSON/structured-output artifacts that may leak from the LLM."""
    if not text:
        return text
    # Remove trailing JSON-like fragments: " }, " } }, etc.
    text = re.sub(r'\s*["\']?\s*}\s*}?\s*$', "", text.strip())
    return text.strip()


def _split_sentences(text: str) -> list[str]:
    """Split text into sentence-like chunks for redistribution fallback."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _distribute_translated_text(chunk: Chunk, translated: str) -> list[str]:
    """Heuristically redistribute one collapsed translation across chunk blocks."""
    n = len(chunk.blocks)
    if n <= 1:
        return [translated.strip()]

    sentences = _split_sentences(translated)
    if not sentences:
        return [translated.strip()] + [""] * (n - 1)

    # Ensure enough segments to distribute.
    if len(sentences) < n:
        words = translated.split()
        if not words:
            return [translated.strip()] + [""] * (n - 1)
        per_block = max(1, len(words) // n)
        segments: list[str] = []
        start = 0
        for _ in range(n - 1):
            end = min(len(words), start + per_block)
            segments.append(" ".join(words[start:end]).strip())
            start = end
        segments.append(" ".join(words[start:]).strip())
        return [s for s in segments]

    source_lengths = [max(1, len(b.text)) for b in chunk.blocks]
    total_source = sum(source_lengths)
    sentence_lengths = [max(1, len(s)) for s in sentences]
    total_translated = sum(sentence_lengths)
    targets = [
        max(1, round(total_translated * src_len / total_source))
        for src_len in source_lengths
    ]

    out: list[str] = []
    idx = 0
    for block_i in range(n):
        if idx >= len(sentences):
            out.append("")
            continue
        acc: list[str] = []
        cur = 0
        target = targets[block_i]
        remaining_blocks = n - block_i
        remaining_sentences = len(sentences) - idx
        # Keep at least one sentence for each remaining block.
        max_take = max(1, remaining_sentences - (remaining_blocks - 1))
        taken = 0
        while idx < len(sentences) and taken < max_take:
            sent = sentences[idx]
            acc.append(sent)
            cur += max(1, len(sent))
            idx += 1
            taken += 1
            if cur >= target and taken >= 1:
                break
        out.append(" ".join(acc).strip())

    if idx < len(sentences):
        tail = " ".join(sentences[idx:]).strip()
        out[-1] = f"{out[-1]} {tail}".strip()
    return out


def _normalize_table_translation(block: Block, translated_text: str) -> str:
    """Normalize table translation to JSON string with cell matrix."""
    text = translated_text.strip()
    if not text:
        return json.dumps(block.table_cells or [], ensure_ascii=False)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            cells = parsed.get("cells")
            if isinstance(cells, list):
                return json.dumps(cells, ensure_ascii=False)
        if isinstance(parsed, list):
            return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        pass

    if "|" in text:
        rows = _parse_pipe_table(text)
        return json.dumps(rows, ensure_ascii=False)
    if block.table_cells:
        return json.dumps(block.table_cells, ensure_ascii=False)
    return json.dumps([[text]], ensure_ascii=False)


def _register_utf8_font() -> str:
    """Register a Unicode/Cyrillic-capable font. Return base font name for use."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return "Helvetica"
    for reg_path, bold_path in _UTF8_FONT_PATHS:
        if reg_path.exists():
            try:
                pdfmetrics.registerFont(TTFont(_UTF8_FONT_NAME, str(reg_path)))
                if bold_path.exists():
                    pdfmetrics.registerFont(
                        TTFont(_UTF8_FONT_BOLD_NAME, str(bold_path))
                    )
                logger.info("Registered UTF-8 font: %s", reg_path)
                return _UTF8_FONT_NAME
            except Exception as exc:
                logger.warning("Failed to register %s: %s", reg_path, exc)
    logger.warning("No UTF-8 font found, Cyrillic may not render in PDF")
    return "Helvetica"


def _get_block_translations(
    chunks: list[Chunk], translated_chunks: list[str]
) -> list[str]:
    """Split per-chunk translations back into per-block translation strings.

    Each chunk was assembled by joining block texts with "\\n\\n". We try to
    reverse that split. When the LLM returns fewer parts than blocks, we assign
    the full translation to the first block to avoid fragmenting text mid-phrase.

    Args:
        chunks: Original Chunk objects with their block lists.
        translated_chunks: One translated string per chunk from the LLM.

    Returns:
        A flat list with one translated string per block.
    """
    result: list[str] = []
    for chunk, translated in zip(chunks, translated_chunks):
        translated = _sanitize_translated_text(translated)
        parts = [p.strip() for p in translated.split("\n\n")]
        while parts and not parts[-1]:
            parts.pop()
        n = len(chunk.blocks)
        if len(parts) == n:
            result.extend(parts)
        elif len(parts) > n:
            result.extend(parts[: n - 1])
            result.append("\n\n".join(parts[n - 1 :]))
        else:
            redistributed = _distribute_translated_text(chunk, translated)
            result.extend(redistributed)

    normalized: list[str] = []
    flat_blocks = [block for chunk in chunks for block in chunk.blocks]
    for idx, text in enumerate(result):
        if idx < len(flat_blocks) and flat_blocks[idx].type == BlockType.TABLE:
            normalized.append(_normalize_table_translation(flat_blocks[idx], text))
        else:
            normalized.append(text)
    return normalized


# ---------------------------------------------------------------------------
# Format-specific assemblers
# ---------------------------------------------------------------------------


def _assemble_txt(block_translations: list[str]) -> str:
    """Join translated block texts with double newlines.

    Args:
        block_translations: One string per block.

    Returns:
        Complete plain-text document as a single string.
    """
    return "\n\n".join(t for t in block_translations if t)


def _assemble_html(soup_str: str, block_translations: list[str]) -> str:
    """Replace text content of matched tags in the soup with translations.

    Walks the DOM in the same tag order as the parser so the i-th tag gets
    the i-th block translation.

    Args:
        soup_str: Full serialized BeautifulSoup from parsed_doc.metadata.
        block_translations: One string per block, in parser traversal order.

    Returns:
        Prettified HTML string with translated text.
    """
    def _extract_text_nodes(tag: Tag) -> list[NavigableString]:
        nodes: list[NavigableString] = []
        for node in tag.descendants:
            if isinstance(node, NavigableString) and str(node).strip():
                nodes.append(node)
        return nodes

    def _split_translation(
        translated_text: str, node_texts: list[str]
    ) -> list[str]:
        if not node_texts:
            return [translated_text]
        if len(node_texts) == 1:
            return [translated_text]
        words = translated_text.split()
        if not words:
            return [""] * len(node_texts)
        source_lengths = [max(1, len(text.strip())) for text in node_texts]
        total_source = sum(source_lengths)
        total_words = len(words)
        raw_targets = [max(1, round(total_words * sl / total_source)) for sl in source_lengths]
        targets = raw_targets[:-1]
        targets.append(max(1, total_words - sum(targets)))

        out: list[str] = []
        start = 0
        for idx, target in enumerate(targets):
            if idx == len(targets) - 1:
                out.append(" ".join(words[start:]).strip())
                break
            end = min(total_words, start + target)
            out.append(" ".join(words[start:end]).strip())
            start = end
        while len(out) < len(node_texts):
            out.append("")
        return out

    soup = BeautifulSoup(soup_str, "html.parser")
    tags = [t for t in soup.find_all(_HTML_TAGS_OF_INTEREST) if isinstance(t, Tag)]

    for tag, translated_text in zip(tags, block_translations):
        text_nodes = _extract_text_nodes(tag)
        node_texts = [str(node) for node in text_nodes]
        parts = _split_translation(translated_text, node_texts)
        if not text_nodes:
            tag.append(translated_text)
            continue
        for node, part in zip(text_nodes, parts):
            node.replace_with(part)

    return soup.prettify()


def _parse_pipe_table(text: str) -> list[list[str]]:
    """Parse a pipe-delimited table text (from the parser) into a 2-D list.

    Args:
        text: Pipe-delimited table, rows separated by newlines.

    Returns:
        2-D list of cell strings.
    """
    parsed_json = None
    try:
        parsed_json = json.loads(text)
    except Exception:
        parsed_json = None
    if isinstance(parsed_json, list):
        out_rows: list[list[str]] = []
        for row in parsed_json:
            if isinstance(row, list):
                out_rows.append([str(cell) for cell in row])
            else:
                out_rows.append([str(row)])
        if out_rows:
            return out_rows

    rows: list[list[str]] = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split("|")]
        rows.append(cells)
    return rows if rows else [[""]]


def _hex_to_rgb(font_color: str | None) -> tuple[int, int, int]:
    """Convert #RRGGBB color into RGB tuple."""
    if not font_color:
        return (0, 0, 0)
    value = font_color.strip().lstrip("#")
    if len(value) != 6:
        return (0, 0, 0)
    try:
        return (
            int(value[0:2], 16),
            int(value[2:4], 16),
            int(value[4:6], 16),
        )
    except ValueError:
        return (0, 0, 0)


def _block_font_name(block: Block) -> str:
    """Map style metadata to a built-in PyMuPDF font."""
    font_name = (block.font_name or "").lower()
    if block.is_bold or "bold" in font_name:
        return "helvb"
    return "helv"


def _to_pdf_text_blocks(
    blocks: list[Block], block_translations: list[str]
) -> list[TextBlock]:
    """Build TextBlock objects for PDF in-place redraw."""
    result: list[TextBlock] = []
    page_x_mids: dict[int, list[float]] = {}
    for block in blocks:
        if block.page_index is None or block.bbox is None:
            continue
        x_mid = (float(block.bbox[0]) + float(block.bbox[2])) / 2
        page_x_mids.setdefault(int(block.page_index), []).append(x_mid)

    page_split_x: dict[int, float] = {}
    for page_index, mids in page_x_mids.items():
        if len(mids) < 6:
            continue
        sorted_mids = sorted(mids)
        midpoint = sorted_mids[len(sorted_mids) // 2]
        page_split_x[page_index] = midpoint

    for idx, block in enumerate(blocks):
        if block.page_index is None or block.bbox is None:
            continue
        if len(block.bbox) != 4:
            continue

        translated = (
            block_translations[idx].strip() if idx < len(block_translations) else ""
        )
        if block.non_translatable:
            translated = block.text
        if block.type == BlockType.TABLE:
            cells = _parse_pipe_table(translated)
            translated = "\n".join(" | ".join(row) for row in cells)
        if not translated:
            # Keep source text when translation cannot be mapped reliably.
            continue

        use_size = float(block.font_size) if block.font_size else 10.0
        use_color = _hex_to_rgb(block.font_color)
        use_font = _block_font_name(block)
        use_line_height = block.line_height
        if block.style_runs:
            runs = [
                run for run in block.style_runs if isinstance(run.get("text"), str)
            ]
            if runs:
                dominant = max(runs, key=lambda run: len(run.get("text", "")))
                dom_size = dominant.get("font_size")
                if isinstance(dom_size, (int, float)):
                    use_size = float(dom_size)
                dom_color = dominant.get("font_color")
                if isinstance(dom_color, str):
                    use_color = _hex_to_rgb(dom_color)
                dom_font = str(dominant.get("font_name", ""))
                if dom_font:
                    use_font = "helvb" if "bold" in dom_font.lower() else "helv"
                if use_line_height is None:
                    use_line_height = use_size * 1.2

        result.append(
            TextBlock(
                page_index=block.page_index,
                bbox=(
                    float(block.bbox[0]),
                    float(block.bbox[1]),
                    float(block.bbox[2]),
                    float(block.bbox[3]),
                ),
                text=block.text,
                font=use_font,
                size=use_size,
                color=use_color,
                line_height=use_line_height,
                column_id=(
                    1
                    if (
                        block.page_index in page_split_x
                        and ((float(block.bbox[0]) + float(block.bbox[2])) / 2)
                        > page_split_x[block.page_index]
                    )
                    else 0
                ),
                block_type=block.type.value,
                translated=translated,
            )
        )
    return result


def _paragraph_fragmentation_rate(blocks: list[Block]) -> float:
    """Estimate paragraph fragmentation: many short paragraph blocks -> higher."""
    paragraph_blocks = [b for b in blocks if b.type == BlockType.PARAGRAPH]
    if not paragraph_blocks:
        return 0.0
    short_count = sum(1 for b in paragraph_blocks if len(b.text.split()) <= 4)
    return short_count / len(paragraph_blocks)


def _to_layout_ir(
    blocks: list[Block], block_translations: list[str]
) -> DocumentLayoutIR:
    """Build stable layout IR from parsed blocks and mapped translations."""
    pages: dict[int, list[LayoutBlock]] = {}
    for idx, block in enumerate(blocks):
        if block.page_index is None or block.bbox is None:
            continue
        translated = block_translations[idx] if idx < len(block_translations) else ""
        page = int(block.page_index)
        pages.setdefault(page, [])
        pages[page].append(
            LayoutBlock(
                block_id=f"blk_{page}_{idx}",
                page=page,
                type=block.type,
                bbox=block.bbox,
                text=translated if not block.non_translatable else block.text,
                style={
                    "font_size": block.font_size,
                    "font_color": block.font_color,
                    "font_name": block.font_name,
                    "is_bold": block.is_bold,
                    "line_height": block.line_height,
                },
                reading_order=len(pages[page]),
                column_id=0,
                non_translatable=block.non_translatable,
                table_cells=block.table_cells,
            )
        )
    ir_pages = []
    for page, page_blocks in sorted(pages.items()):
        ir_pages.append(
            {
                "page": page,
                "width": 0.0,
                "height": 0.0,
                "blocks": page_blocks,
            }
        )
    return DocumentLayoutIR.model_validate({"pages": ir_pages})


def _coalesce_pdf_block_translations(
    blocks: list[Block], block_translations: list[str]
) -> list[str]:
    """Merge tiny adjacent paragraph translations to reduce fragmentation."""
    if len(blocks) != len(block_translations):
        return block_translations
    out = block_translations.copy()
    for idx in range(1, len(blocks)):
        prev_block = blocks[idx - 1]
        block = blocks[idx]
        if (
            prev_block.type != BlockType.PARAGRAPH
            or block.type != BlockType.PARAGRAPH
            or prev_block.page_index != block.page_index
            or prev_block.bbox is None
            or block.bbox is None
        ):
            continue
        y_gap = float(block.bbox[1] - prev_block.bbox[3])
        x_gap = abs(float(block.bbox[0] - prev_block.bbox[0]))
        short_translation = len(out[idx].split()) <= 5
        if -2.0 <= y_gap <= 16.0 and x_gap <= 20.0 and short_translation:
            out[idx - 1] = f"{out[idx - 1]} {out[idx]}".strip()
            out[idx] = ""
    return out


def _assemble_pdf_pymupdf_sync(
    blocks: list[Block],
    block_translations: list[str],
    output_path: str,
    source_path: str | None,
    metadata: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """Assemble PDF using page rebuild with original page geometry.

    Returns:
        Tuple (success, warning_message).
    """
    def _insert_images(doc: fitz.Document, images_meta: list[dict[str, Any]]) -> None:
        for image in images_meta:
            page_index = int(image.get("page_index", 0))
            if page_index < 0 or page_index >= len(doc):
                continue
            bbox = image.get("bbox")
            img_bytes = image.get("image_bytes")
            if (
                not isinstance(bbox, (tuple, list))
                or len(bbox) < 4
                or not isinstance(img_bytes, (bytes, bytearray))
            ):
                continue
            rect = fitz.Rect(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
            try:
                doc[page_index].insert_image(rect, stream=img_bytes, overlay=True)
            except Exception as exc:
                logger.warning("Failed to place image on page %d: %s", page_index, exc)

    source = Path(source_path) if source_path else None
    images_meta = (metadata or {}).get("images", [])

    try:
        rebuilt_doc = fitz.open()
        layout_ir = _to_layout_ir(blocks, block_translations)

        if source and source.exists():
            with fitz.open(str(source)) as source_doc:
                for page_index in range(len(source_doc)):
                    source_page = source_doc[page_index]
                    target_page = rebuilt_doc.new_page(
                        width=source_page.rect.width,
                        height=source_page.rect.height,
                    )
                    target_page.show_pdf_page(
                        target_page.rect,
                        source_doc,
                        page_index,
                    )
        else:
            page_count = int((metadata or {}).get("page_count") or 0)
            max_index = max((block.page_index or 0) for block in blocks) if blocks else 0
            total_pages = max(page_count, max_index + 1, 1)
            default_width = 595.0
            default_height = 842.0
            for page_idx in range(total_pages):
                rebuilt_doc.new_page(width=default_width, height=default_height)
            _insert_images(rebuilt_doc, images_meta)

        text_blocks = _to_pdf_text_blocks(blocks, block_translations)
        if not text_blocks:
            rebuilt_doc.save(output_path, garbage=4, deflate=True)
            rebuilt_doc.close()
            return True, "No drawable translated blocks; source pages copied as-is"

        original_layout = [block.model_copy() for block in text_blocks]
        if source and source.exists():
            clear_blocks(rebuilt_doc, text_blocks)
        failed_count = draw_translated_blocks(rebuilt_doc, text_blocks)
        layout_score = layout_preservation_score(original_layout, text_blocks)
        style_score = style_preservation_rate(blocks, text_blocks)
        overflow_score = overflow_resolution_rate(len(text_blocks), failed_count)
        logger.info(
            "Assembly metrics layout=%.3f style=%.3f overflow=%.3f",
            layout_score,
            style_score,
            overflow_score,
        )
        logger.info(
            "Assembly readability overlap_count=%d order_violations=%d",
            overlap_count(text_blocks),
            reading_order_violations(text_blocks),
        )
        logger.info(
            "Assembly diagnostics blocks_total=%d draw_failed=%d fragmentation=%.3f",
            len(text_blocks),
            failed_count,
            _paragraph_fragmentation_rate(blocks),
        )
        logger.info(
            "Layout IR pages=%d blocks=%d",
            len(layout_ir.pages),
            sum(len(page.blocks) for page in layout_ir.pages),
        )
        draw_stats = pdf_layout_runtime.LAST_DRAW_STATS or {}
        if draw_stats:
            logger.info("Draw diagnostics %s", draw_stats)
        warning = None
        if failed_count > 0:
            warning = (
                "Page rebuild placed with overflow warnings: "
                f"{failed_count} blocks could not be fully drawn"
            )
            logger.warning(warning)

        rebuilt_doc.save(output_path, garbage=4, deflate=True)
        rebuilt_doc.close()
        return True, warning
    except Exception as exc:
        warning = f"PyMuPDF page rebuild failed: {exc}"
        logger.warning(warning)
        return False, warning


def _block_to_flowable(
    block: Block, translated_text: str, font_name: str
) -> Any:
    """Convert a single block into a reportlab flowable.

    Args:
        block: Original block with type/style metadata.
        translated_text: Translated text for this block.
        font_name: ReportLab font name (use Unicode-capable for Cyrillic).

    Returns:
        A reportlab Platypus flowable (Paragraph, Table, or Spacer).
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    base_styles = getSampleStyleSheet()
    font_bold = (
        _UTF8_FONT_BOLD_NAME if font_name != "Helvetica" else "Helvetica-Bold"
    )

    if not translated_text:
        return Spacer(1, 0)

    if block.type == BlockType.TABLE:
        data = _parse_pipe_table(translated_text)
        tbl = Table(data)
        tbl.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTNAME", (0, 0), (-1, 0), font_bold),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("PADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return tbl

    def _para_style(base: str) -> ParagraphStyle:
        s = base_styles[base]
        style = ParagraphStyle(
            name=f"{base}_utf8",
            parent=s,
            fontName=font_name,
            spaceBefore=2,
            spaceAfter=2,
        )
        if block.font_size is not None:
            style.fontSize = block.font_size
        if block.font_color:
            style.textColor = colors.HexColor(block.font_color)
        return style

    if block.type == BlockType.HEADING:
        style_name = _HEADING_STYLE_MAP.get(block.level, "Heading3")
        style = _para_style(style_name)
        text = f"<b>{translated_text}</b>" if block.is_bold else translated_text
        return Paragraph(text, style)

    if block.type == BlockType.LIST_ITEM:
        style = _para_style("Normal")
        return Paragraph(f"• {translated_text}", style)

    if block.type == BlockType.CAPTION:
        style = _para_style("Normal")
        style.fontSize = max(8, style.fontSize - 1)
        return Paragraph(translated_text, style)

    if block.type == BlockType.FOOTNOTE:
        style = _para_style("Normal")
        style.fontSize = min(style.fontSize, 8)
        return Paragraph(translated_text, style)

    if block.type == BlockType.FORM_FIELD:
        style = _para_style("Normal")
        text = translated_text if not block.non_translatable else block.text
        return Paragraph(text, style)

    # PARAGRAPH / CODE / fallback
    style = _para_style("Normal")
    text = f"<b>{translated_text}</b>" if block.is_bold else translated_text
    return Paragraph(text, style)


def _assemble_pdf_sync(
    blocks: list[Block],
    block_translations: list[str],
    output_path: str,
    metadata: dict[str, Any] | None = None,
    source_path: str | None = None,
) -> None:
    """Render translated blocks into a PDF file using reportlab (synchronous).

    Inserts PageBreak when page_index increases to preserve original page
    boundaries. Merges blocks with images from metadata, sorted by
    (page_index, bbox.top).

    Args:
        blocks: Original blocks carrying type/style metadata.
        block_translations: Translated text per block.
        output_path: Filesystem path for the output PDF file.
        metadata: Optional metadata with "images" list (page_index, bbox,
            image_bytes, ext).
        source_path: Optional path to source PDF (for reference).
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Image, PageBreak, SimpleDocTemplate, Spacer

    font_name = _register_utf8_font()
    images_meta = (metadata or {}).get("images", [])

    # Build unified items: (page_index, y_top, "block", block_idx) or
    # (page_index, y_top, "image", img_dict)
    items: list[tuple[int, float, str, Any]] = []
    for i, block in enumerate(blocks):
        page_idx = block.page_index if block.page_index is not None else 0
        y_top = block.bbox[1] if block.bbox else 0.0
        items.append((page_idx, y_top, "block", i))
    for img in images_meta:
        page_idx = img.get("page_index", 0)
        bbox = img.get("bbox", (0, 0, 0, 0))
        y_top = bbox[1] if len(bbox) >= 2 else 0.0
        items.append((page_idx, y_top, "image", img))

    items.sort(key=lambda x: (x[0], x[1]))

    story: list[Any] = []
    prev_page: int | None = None
    for page_idx, _, item_type, payload in items:
        if prev_page is not None and page_idx > prev_page:
            story.append(PageBreak())
        prev_page = page_idx

        if item_type == "block":
            block_idx = payload
            block = blocks[block_idx]
            translated_text = (
                block_translations[block_idx]
                if block_idx < len(block_translations)
                else ""
            )
            flowable = _block_to_flowable(block, translated_text, font_name)
            story.append(flowable)
            story.append(Spacer(1, 2))
        else:
            img_dict = payload
            img_bytes = img_dict.get("image_bytes")
            bbox = img_dict.get("bbox", (0, 0, 100, 100))
            if img_bytes and len(bbox) >= 4:
                w_pt = max(1, bbox[2] - bbox[0])
                h_pt = max(1, bbox[3] - bbox[1])
                try:
                    img_flowable = Image(
                        io.BytesIO(img_bytes),
                        width=w_pt,
                        height=h_pt,
                    )
                    story.append(img_flowable)
                    story.append(Spacer(1, 2))
                except Exception as exc:
                    logger.warning("Failed to insert image in PDF: %s", exc)

    doc = SimpleDocTemplate(output_path, pagesize=A4)
    doc.build(story)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def assemble_document(
    original: ParsedDocument,
    chunks: list[Chunk],
    translated_chunks: list[str],
    output_path: str,
    source_path: str | None = None,
    assembly_warnings: list[str] | None = None,
) -> str:
    """Reassemble translated block texts into a complete output document.

    Dispatches to the appropriate format handler (TXT, HTML, PDF) based on
    ``original.format``. PDF assembly is offloaded to a thread executor
    because reportlab is synchronous.

    Args:
        original: The parsed source document (format + blocks + metadata).
        chunks: Chunk objects produced by the chunker (carry block lists).
        translated_chunks: One translated string per chunk from the LLM.
        output_path: Filesystem path where the output document is written.
        source_path: Optional path to source file (used for PDF assembly).
        assembly_warnings: Optional collector for non-fatal assembly warnings.

    Returns:
        The ``output_path`` after the file has been written.

    Raises:
        ValueError: If ``original.format`` is not supported.
        ValueError: If ``chunks`` and ``translated_chunks`` lengths differ.
    """
    if len(chunks) != len(translated_chunks):
        raise ValueError(
            f"chunks/translated_chunks length mismatch: "
            f"{len(chunks)} vs {len(translated_chunks)}"
        )

    block_translations = _get_block_translations(chunks, translated_chunks)
    fmt = original.format

    if fmt == "txt":
        content = _assemble_txt(block_translations)
        async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
            await f.write(content)

    elif fmt == "html":
        soup_str = str(original.metadata.get("soup_str", ""))
        if not soup_str:
            logger.warning("No soup_str in metadata, falling back to plain join")
            content = _assemble_txt(block_translations)
            async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
                await f.write(content)
        else:
            html_content = _assemble_html(soup_str, block_translations)
            async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
                await f.write(html_content)

    elif fmt == "pdf":
        block_translations = _coalesce_pdf_block_translations(
            original.blocks,
            block_translations,
        )
        loop = asyncio.get_running_loop()
        ok, warning = await loop.run_in_executor(
            None,
            _assemble_pdf_pymupdf_sync,
            original.blocks,
            block_translations,
            output_path,
            source_path,
            original.metadata,
        )
        if warning and assembly_warnings is not None:
            assembly_warnings.append(warning)
        if not ok:
            raise RuntimeError(
                "PDF page rebuild failed; reportlab flow fallback is disabled"
            )

    else:
        raise ValueError(f"Unsupported document format: {fmt}")

    logger.info(
        "Assembled %s document at %s (%d blocks)",
        fmt,
        output_path,
        len(original.blocks),
    )
    return output_path
