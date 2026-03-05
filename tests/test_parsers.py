"""Tests for DocumentParser — TXT, HTML, PDF."""

from pathlib import Path

import pytest

from app.models.schemas import BlockType
from app.services.document_parser import DocumentParser, _is_page_marker


async def test_parse_txt_returns_paragraphs(txt_file: Path) -> None:
    """TXT split by double newlines produces N PARAGRAPH blocks."""
    parser = DocumentParser()
    result = await parser.parse(str(txt_file))
    assert result.format == "txt"
    assert len(result.blocks) == 3
    for block in result.blocks:
        assert block.type == BlockType.PARAGRAPH
    assert result.blocks[0].text == "First paragraph."
    assert result.blocks[1].text == "Second paragraph."
    assert result.blocks[2].text == "Third paragraph."


async def test_parse_txt_skips_empty_fragments(tmp_path: Path) -> None:
    """Trailing whitespace produces no extra blocks."""
    path = tmp_path / "trailing.txt"
    path.write_text("Only one.\n\n   \n\n", encoding="utf-8")
    parser = DocumentParser()
    result = await parser.parse(str(path))
    assert len(result.blocks) == 1
    assert result.blocks[0].text == "Only one."


async def test_parse_html_heading_levels(html_file: Path) -> None:
    """h1, h2, p, li, table produce correct BlockType and level."""
    parser = DocumentParser()
    result = await parser.parse(str(html_file))
    assert result.format == "html"
    blocks = result.blocks
    assert len(blocks) >= 5
    assert blocks[0].type == BlockType.HEADING
    assert blocks[0].level == 1
    assert blocks[0].text == "Main Heading"
    assert blocks[1].type == BlockType.HEADING
    assert blocks[1].level == 2
    assert blocks[1].text == "Sub Heading"
    assert blocks[2].type == BlockType.PARAGRAPH
    assert blocks[2].text == "A paragraph of text."
    li_blocks = [b for b in blocks if b.type == BlockType.LIST_ITEM]
    assert len(li_blocks) >= 2
    table_blocks = [b for b in blocks if b.type == BlockType.TABLE]
    assert len(table_blocks) >= 1


async def test_parse_html_stores_soup_str(html_file: Path) -> None:
    """metadata['soup_str'] is non-empty and parseable."""
    parser = DocumentParser()
    result = await parser.parse(str(html_file))
    soup_str = result.metadata.get("soup_str", "")
    assert soup_str
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(soup_str, "html.parser")
    h1 = soup.find("h1")
    assert h1 is not None
    assert h1.get_text(strip=True) == "Main Heading"


async def test_parse_html_skips_empty_tags(tmp_path: Path) -> None:
    """Empty <p></p> is not included."""
    path = tmp_path / "empty.html"
    path.write_text(
        "<html><body><p>Has text</p><p></p><p>Also has text</p></body></html>",
        encoding="utf-8",
    )
    parser = DocumentParser()
    result = await parser.parse(str(path))
    assert len(result.blocks) == 2
    assert result.blocks[0].text == "Has text"
    assert result.blocks[1].text == "Also has text"


async def test_parse_pdf_returns_blocks(pdf_file: Path) -> None:
    """A minimal PDF produces at least one block (PARAGRAPH or HEADING)."""
    parser = DocumentParser()
    result = await parser.parse(str(pdf_file))
    assert result.format == "pdf"
    assert len(result.blocks) >= 1
    assert any(
        b.type in (BlockType.PARAGRAPH, BlockType.HEADING) for b in result.blocks
    )
    for block in result.blocks:
        assert block.page_index == 0


def test_is_page_marker() -> None:
    """Page number markers are detected correctly."""
    assert _is_page_marker("— 1 of 9 —") is True
    assert _is_page_marker("-- 3 of 9 --") is True
    assert _is_page_marker(" - 5 of 10 - ") is True
    assert _is_page_marker("Page 1") is False
    assert _is_page_marker("Normal text") is False


async def test_parse_pdf_extracts_images(tmp_path: Path) -> None:
    """PDF with an image produces metadata['images'] with image data."""
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Image, SimpleDocTemplate

    # Minimal 1x1 PNG
    png_bytes = bytes(
        [
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
            0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
            0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
            0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,
            0x54, 0x08, 0xD7, 0x63, 0xF8, 0xFF, 0xFF, 0x3F,
            0x00, 0x05, 0xFE, 0x02, 0xFE, 0xDC, 0xCC, 0x59,
            0xE7, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,
            0x44, 0xAE, 0x42, 0x60, 0x82,
        ]
    )
    path = tmp_path / "with_image.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    doc.build([Image(io.BytesIO(png_bytes), width=50, height=50)])

    parser = DocumentParser()
    result = await parser.parse(str(path))
    assert result.format == "pdf"
    images = result.metadata.get("images", [])
    assert len(images) >= 1
    assert "image_bytes" in images[0]
    assert "bbox" in images[0]
    assert "page_index" in images[0]


async def test_parse_unknown_extension_raises(tmp_path: Path) -> None:
    """Unsupported extension raises ValueError."""
    path = tmp_path / "doc.docx"
    path.write_text("content", encoding="utf-8")
    parser = DocumentParser()
    with pytest.raises(ValueError, match="Unsupported file extension"):
        await parser.parse(str(path))
