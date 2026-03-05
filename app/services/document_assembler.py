"""Assembles translated block texts back into TXT, HTML, or PDF documents."""

import asyncio
import io
import logging
import re
from pathlib import Path
from typing import Any

import aiofiles
import pymupdf as fitz
from bs4 import BeautifulSoup, NavigableString, Tag

from app.models.schemas import Block, BlockType, Chunk, ParsedDocument
from app.services.pdf_layout_translator import (
    TextBlock,
    clear_blocks,
    draw_translated_blocks,
)
from app.services.quality_metrics import (
    layout_preservation_score,
    overflow_resolution_rate,
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
    return result


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
                translated=translated,
            )
        )
    return result


def _assemble_pdf_pymupdf_sync(
    blocks: list[Block],
    block_translations: list[str],
    output_path: str,
    source_path: str | None,
) -> tuple[bool, str | None]:
    """Assemble PDF by replacing text in original bboxes with PyMuPDF.

    Returns:
        Tuple (success, warning_message).
    """
    if not source_path:
        warning = "source_path is required for PyMuPDF PDF assembly"
        logger.warning(warning)
        return False, warning

    source = Path(source_path)
    if not source.exists():
        warning = f"source PDF does not exist: {source_path}"
        logger.warning(warning)
        return False, warning

    try:
        with fitz.open(source_path) as doc:
            text_blocks = _to_pdf_text_blocks(blocks, block_translations)
            if text_blocks:
                original_layout = [block.model_copy() for block in text_blocks]
                clear_blocks(doc, text_blocks)
                failed_count = draw_translated_blocks(doc, text_blocks)
                layout_score = layout_preservation_score(original_layout, text_blocks)
                style_score = style_preservation_rate(blocks, text_blocks)
                overflow_score = overflow_resolution_rate(len(text_blocks), failed_count)
                logger.info(
                    "Assembly metrics layout=%.3f style=%.3f overflow=%.3f",
                    layout_score,
                    style_score,
                    overflow_score,
                )
                if failed_count > 0:
                    warning = (
                        "PyMuPDF bbox draw failed for "
                        f"{failed_count} blocks; falling back to reportlab"
                    )
                    logger.warning(warning)
                    return False, warning
                doc.save(output_path, garbage=4, deflate=True)
                return True, None
            warning = (
                "No drawable translated blocks for PDF bbox assembly; "
                "falling back to reportlab"
            )
            logger.warning(warning)
            return False, warning
    except Exception as exc:
        warning = f"PyMuPDF PDF assembly failed, fallback to reportlab: {exc}"
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
        loop = asyncio.get_running_loop()
        ok, warning = await loop.run_in_executor(
            None,
            _assemble_pdf_pymupdf_sync,
            original.blocks,
            block_translations,
            output_path,
            source_path,
        )
        if warning and assembly_warnings is not None:
            assembly_warnings.append(warning)
        if not ok:
            await loop.run_in_executor(
                None,
                _assemble_pdf_sync,
                original.blocks,
                block_translations,
                output_path,
                original.metadata,
                source_path,
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
