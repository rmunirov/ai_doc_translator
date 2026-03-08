"""Tests for assemble_document — TXT, HTML, PDF."""

from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz
import pytest

from app.models.schemas import Block, BlockType, Chunk, ParsedDocument
from app.services.document_parser import DocumentParser
from app.services.document_assembler import (
    _get_block_translations,
    assemble_document,
)


async def test_assemble_txt(tmp_path: Path) -> None:
    """Joined output equals '\\n\\n'.join(block_translations)."""
    blocks = [
        Block(type=BlockType.PARAGRAPH, text="A"),
        Block(type=BlockType.PARAGRAPH, text="B"),
        Block(type=BlockType.PARAGRAPH, text="C"),
    ]
    parsed = ParsedDocument(format="txt", blocks=blocks)
    chunk = Chunk(index=0, blocks=blocks, text="A\n\nB\n\nC")
    block_translations = ["Uno", "Dos", "Tres"]
    translated_chunks = ["\n\n".join(block_translations)]
    output_path = str(tmp_path / "out.txt")

    await assemble_document(parsed, [chunk], translated_chunks, output_path)

    content = Path(output_path).read_text(encoding="utf-8")
    assert content == "Uno\n\nDos\n\nTres"


async def test_assemble_html_replaces_text(tmp_path: Path) -> None:
    """Translated text appears in output HTML; h1 tag contains translated heading."""
    html_content = """<!DOCTYPE html>
<html><body><h1>Original</h1><p>Para</p></body></html>"""
    soup_str = html_content
    blocks = [
        Block(type=BlockType.HEADING, text="Original", level=1, raw_html="<h1>Original</h1>"),
        Block(type=BlockType.PARAGRAPH, text="Para", raw_html="<p>Para</p>"),
    ]
    parsed = ParsedDocument(format="html", blocks=blocks, metadata={"soup_str": soup_str})
    chunk = Chunk(index=0, blocks=blocks, text="Original\n\nPara")
    translated_chunks = ["TranslatedHeading\n\nTranslatedPara"]
    output_path = str(tmp_path / "out.html")

    await assemble_document(parsed, [chunk], translated_chunks, output_path)

    content = Path(output_path).read_text(encoding="utf-8")
    assert "TranslatedHeading" in content
    assert "TranslatedPara" in content
    assert "<h1>" in content


async def test_assemble_html_preserves_structure(tmp_path: Path) -> None:
    """Original img tag (if present in soup_str) still in output."""
    html_content = """<html><body><h1>H</h1><img src="x.png"/></body></html>"""
    blocks = [Block(type=BlockType.HEADING, text="H", level=1, raw_html="<h1>H</h1>")]
    parsed = ParsedDocument(format="html", blocks=blocks, metadata={"soup_str": html_content})
    chunk = Chunk(index=0, blocks=blocks, text="H")
    translated_chunks = ["TranslatedH"]
    output_path = str(tmp_path / "out.html")

    await assemble_document(parsed, [chunk], translated_chunks, output_path)

    content = Path(output_path).read_text(encoding="utf-8")
    assert "img" in content or "x.png" in content


async def test_assemble_html_preserves_inline_markup(tmp_path: Path) -> None:
    """Inline nodes and style attributes remain after translation."""
    html_content = (
        "<html><body>"
        '<p style="color:red">Hello <span class="x"><b>world</b></span>!</p>'
        "</body></html>"
    )
    blocks = [
        Block(
            type=BlockType.PARAGRAPH,
            text="Hello world !",
            raw_html='<p style="color:red">Hello <span class="x"><b>world</b></span>!</p>',
        )
    ]
    parsed = ParsedDocument(format="html", blocks=blocks, metadata={"soup_str": html_content})
    chunk = Chunk(index=0, blocks=blocks, text="Hello world !")
    output_path = str(tmp_path / "inline.html")

    await assemble_document(parsed, [chunk], ["Привет красивый мир"], output_path)

    content = Path(output_path).read_text(encoding="utf-8")
    assert "style=\"color:red\"" in content
    assert "class=\"x\"" in content
    assert "<b>" in content
    assert "Привет" in content


async def test_assemble_pdf_creates_file(tmp_path: Path) -> None:
    """Output .pdf file exists and size > 0."""
    blocks = [
        Block(type=BlockType.PARAGRAPH, text="PDF content."),
    ]
    parsed = ParsedDocument(format="pdf", blocks=blocks)
    chunk = Chunk(index=0, blocks=blocks, text="PDF content.")
    translated_chunks = ["Translated PDF content."]
    output_path = str(tmp_path / "out.pdf")

    await assemble_document(parsed, [chunk], translated_chunks, output_path)

    p = Path(output_path)
    assert p.exists()
    assert p.stat().st_size > 0


async def test_assemble_pdf_uses_pymupdf_bbox_when_source_given(
    tmp_path: Path,
) -> None:
    """When source_path is provided, translated text is drawn into source PDF."""
    source_pdf = tmp_path / "source.pdf"
    output_pdf = tmp_path / "out_bbox.pdf"

    src_doc = fitz.open()
    src_page = src_doc.new_page()
    src_page.insert_text((72, 72), "Hello PDF", fontsize=12, fontname="helv")
    src_doc.save(str(source_pdf))
    src_doc.close()

    parser = DocumentParser()
    parsed_source = await parser.parse(str(source_pdf))
    assert parsed_source.blocks

    block = parsed_source.blocks[0]
    parsed = ParsedDocument(
        format="pdf",
        blocks=[block],
        metadata=parsed_source.metadata,
    )
    chunk = Chunk(index=0, blocks=[block], text=block.text)

    await assemble_document(
        parsed,
        [chunk],
        ["Translated PDF"],
        str(output_pdf),
        source_path=str(source_pdf),
    )

    assert output_pdf.exists()
    with fitz.open(str(output_pdf)) as out_doc:
        text = out_doc[0].get_text("text")
    normalized = " ".join(text.split())
    lowered = normalized.lower()
    assert "translate" in lowered
    assert "pdf" in lowered


async def test_assemble_pdf_applies_font_styles(tmp_path: Path) -> None:
    """Blocks with font_size and font_color produce a valid PDF."""
    blocks = [
        Block(
            type=BlockType.PARAGRAPH,
            text="Styled text.",
            font_size=14.0,
            font_color="#FF0000",
            page_index=0,
        ),
    ]
    parsed = ParsedDocument(format="pdf", blocks=blocks)
    chunk = Chunk(index=0, blocks=blocks, text="Styled text.")
    translated_chunks = ["Translated styled text."]
    output_path = str(tmp_path / "out.pdf")

    await assemble_document(parsed, [chunk], translated_chunks, output_path)

    p = Path(output_path)
    assert p.exists()
    assert p.stat().st_size > 0


async def test_assemble_pdf_with_images(tmp_path: Path) -> None:
    """PDF with metadata images produces a file with images inserted."""
    # Minimal 1x1 PNG
    png_bytes = bytes(
        [
            0x89,
            0x50,
            0x4E,
            0x47,
            0x0D,
            0x0A,
            0x1A,
            0x0A,
            0x00,
            0x00,
            0x00,
            0x0D,
            0x49,
            0x48,
            0x44,
            0x52,
            0x00,
            0x00,
            0x00,
            0x01,
            0x00,
            0x00,
            0x00,
            0x01,
            0x08,
            0x02,
            0x00,
            0x00,
            0x00,
            0x90,
            0x77,
            0x53,
            0xDE,
            0x00,
            0x00,
            0x00,
            0x0C,
            0x49,
            0x44,
            0x41,
            0x54,
            0x08,
            0xD7,
            0x63,
            0xF8,
            0xFF,
            0xFF,
            0x3F,
            0x00,
            0x05,
            0xFE,
            0x02,
            0xFE,
            0xDC,
            0xCC,
            0x59,
            0xE7,
            0x00,
            0x00,
            0x00,
            0x00,
            0x49,
            0x45,
            0x4E,
            0x44,
            0xAE,
            0x42,
            0x60,
            0x82,
        ]
    )
    blocks = [
        Block(
            type=BlockType.PARAGRAPH,
            text="Before image.",
            page_index=0,
            bbox=(0, 0, 100, 20),
        ),
    ]
    metadata = {
        "page_count": 1,
        "images": [
            {
                "page_index": 0,
                "bbox": (50, 20, 150, 120),
                "image_bytes": png_bytes,
                "ext": "png",
            },
        ],
    }
    parsed = ParsedDocument(format="pdf", blocks=blocks, metadata=metadata)
    chunk = Chunk(index=0, blocks=blocks, text="Before image.")
    translated_chunks = ["Translated before image."]
    output_path = str(tmp_path / "out.pdf")

    await assemble_document(parsed, [chunk], translated_chunks, output_path)

    p = Path(output_path)
    assert p.exists()
    assert p.stat().st_size > 0


async def test_assemble_pdf_falls_back_when_no_drawable_bbox(
    tmp_path: Path,
) -> None:
    """When no bboxes exist, rebuild should keep source page as-is."""
    source_pdf = tmp_path / "source_no_bbox.pdf"
    output_pdf = tmp_path / "out_fallback.pdf"

    src_doc = fitz.open()
    src_page = src_doc.new_page()
    src_page.insert_text((72, 72), "Original text", fontsize=12, fontname="helv")
    src_doc.save(str(source_pdf))
    src_doc.close()

    # No bbox/page_index -> PyMuPDF in-place redraw has nothing to draw.
    blocks = [Block(type=BlockType.PARAGRAPH, text="Original text")]
    parsed = ParsedDocument(format="pdf", blocks=blocks)
    chunk = Chunk(index=0, blocks=blocks, text="Original text")

    await assemble_document(
        parsed,
        [chunk],
        ["Translated fallback text"],
        str(output_pdf),
        source_path=str(source_pdf),
    )

    assert output_pdf.exists()
    with fitz.open(str(output_pdf)) as out_doc:
        text = out_doc[0].get_text("text")
    assert "Original text" in text


async def test_assemble_pdf_falls_back_when_bbox_draw_fails(
    tmp_path: Path,
) -> None:
    """BBox draw failure should keep rebuild output with warning path."""
    source_pdf = tmp_path / "source_draw_fail.pdf"
    output_pdf = tmp_path / "out_draw_fail.pdf"

    src_doc = fitz.open()
    src_page = src_doc.new_page()
    src_page.insert_text((72, 72), "Hello PDF", fontsize=12, fontname="helv")
    src_doc.save(str(source_pdf))
    src_doc.close()

    parser = DocumentParser()
    parsed_source = await parser.parse(str(source_pdf))
    block = parsed_source.blocks[0]
    parsed = ParsedDocument(
        format="pdf",
        blocks=[block],
        metadata=parsed_source.metadata,
    )
    chunk = Chunk(index=0, blocks=[block], text=block.text)

    with patch(
        "app.services.document_assembler.draw_translated_blocks",
        return_value=1,
    ):
        await assemble_document(
            parsed,
            [chunk],
            ["Translated via fallback"],
            str(output_pdf),
            source_path=str(source_pdf),
        )

    assert output_pdf.exists()


async def test_assemble_pdf_keeps_non_translatable_form_fields(
    tmp_path: Path,
) -> None:
    """FORM_FIELD with non_translatable should keep source text."""
    source_pdf = tmp_path / "source_form.pdf"
    output_pdf = tmp_path / "out_form.pdf"

    src_doc = fitz.open()
    src_page = src_doc.new_page()
    src_page.insert_text((72, 72), "Name: ______", fontsize=12, fontname="helv")
    src_doc.save(str(source_pdf))
    src_doc.close()

    block = Block(
        type=BlockType.FORM_FIELD,
        text="Name: ______",
        bbox=(60.0, 60.0, 240.0, 90.0),
        page_index=0,
        font_size=12.0,
        non_translatable=True,
    )
    parsed = ParsedDocument(
        format="pdf",
        blocks=[block],
        metadata={"source_path": str(source_pdf)},
    )
    chunk = Chunk(index=0, blocks=[block], text=block.text)

    await assemble_document(
        parsed,
        [chunk],
        ["Имя: ______"],
        str(output_pdf),
        source_path=str(source_pdf),
    )

    with fitz.open(str(output_pdf)) as out_doc:
        text = out_doc[0].get_text("text")
    assert "Name:" in text


async def test_assemble_chunk_mismatch_fallback(tmp_path: Path) -> None:
    """When len(chunks) != len(translated_chunks), ValueError is raised."""
    parsed = ParsedDocument(format="txt", blocks=[Block(type=BlockType.PARAGRAPH, text="X")])
    chunks = [
        Chunk(index=0, blocks=[Block(type=BlockType.PARAGRAPH, text="X")], text="X"),
        Chunk(index=1, blocks=[Block(type=BlockType.PARAGRAPH, text="Y")], text="Y"),
    ]
    translated_chunks = ["Only one"]
    output_path = str(tmp_path / "out.txt")

    with pytest.raises(ValueError, match="length mismatch"):
        await assemble_document(parsed, chunks, translated_chunks, output_path)


async def test_assemble_pdf_can_rebuild_without_source(tmp_path: Path) -> None:
    """PDF rebuild should still work when source_path is missing."""
    blocks = [Block(type=BlockType.PARAGRAPH, text="Hello", page_index=0, bbox=(0, 0, 20, 20))]
    parsed = ParsedDocument(format="pdf", blocks=blocks)
    chunk = Chunk(index=0, blocks=blocks, text="Hello")
    output_path = str(tmp_path / "missing_source.pdf")

    await assemble_document(parsed, [chunk], ["Привет"], output_path, source_path=None)
    assert Path(output_path).exists()


def test_get_block_translations_exact_match() -> None:
    """Split by '\\n\\n' produces correct per-block list."""
    chunks = [
        Chunk(index=0, blocks=[Block(type=BlockType.PARAGRAPH, text="A")], text="A"),
        Chunk(index=1, blocks=[Block(type=BlockType.PARAGRAPH, text="B")], text="B"),
    ]
    translated_chunks = ["Uno", "Dos"]
    result = _get_block_translations(chunks, translated_chunks)
    assert result == ["Uno", "Dos"]


def test_get_block_translations_fallback() -> None:
    """LLM-collapsed text is redistributed across blocks heuristically."""
    chunks = [
        Chunk(
            index=0,
            blocks=[
                Block(type=BlockType.PARAGRAPH, text="A"),
                Block(type=BlockType.PARAGRAPH, text="B"),
            ],
            text="A\n\nB",
        ),
    ]
    translated_chunks = ["Single collapsed translation"]
    result = _get_block_translations(chunks, translated_chunks)
    assert len(result) == 2
    assert all(part.strip() for part in result)
    assert " ".join(result).replace("  ", " ").strip() == "Single collapsed translation"


def test_get_block_translations_strips_json_artifacts() -> None:
    """Trailing JSON artifacts from LLM structured output are removed."""
    chunks = [
        Chunk(index=0, blocks=[Block(type=BlockType.PARAGRAPH, text="X")], text="X"),
    ]
    translated_chunks = ['Перевод текста" } }']
    result = _get_block_translations(chunks, translated_chunks)
    assert result == ["Перевод текста"]
