"""Tests for PyMuPDF-based PDF layout translator utilities."""

from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz

from app.services.pdf_layout_translator import (
    TextBlock,
    clear_blocks,
    draw_translated_block,
    draw_translated_blocks,
    extract_text_blocks,
    translate_blocks,
    translate_pdf,
)


class _StubTranslator:
    """Simple sync translator for tests."""

    def translate(self, text: str, src: str, tgt: str) -> str:
        return f"{tgt}:{text}"


def _make_simple_pdf(path: Path, text: str = "Hello world") -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12, fontname="helv")
    doc.save(str(path))
    doc.close()


def test_extract_text_blocks_has_style_and_bbox(tmp_path: Path) -> None:
    pdf_path = tmp_path / "input.pdf"
    _make_simple_pdf(pdf_path, "Extract me")

    with fitz.open(str(pdf_path)) as doc:
        blocks = extract_text_blocks(doc)

    assert blocks
    first = blocks[0]
    assert first.page_index == 0
    assert first.text
    assert len(first.bbox) == 4
    assert first.size > 0
    assert isinstance(first.color, tuple)


def test_clear_blocks_removes_text_in_bbox(tmp_path: Path) -> None:
    pdf_path = tmp_path / "clear_input.pdf"
    out_path = tmp_path / "clear_output.pdf"
    _make_simple_pdf(pdf_path, "Clear me")

    with fitz.open(str(pdf_path)) as doc:
        blocks = extract_text_blocks(doc)
        assert blocks
        clear_blocks(doc, blocks)
        doc.save(str(out_path))

    with fitz.open(str(out_path)) as out_doc:
        text = out_doc[0].get_text("text")
        assert "Clear me" not in text


def test_draw_translated_block_reports_overflow() -> None:
    doc = fitz.open()
    page = doc.new_page()
    block = TextBlock(
        page_index=0,
        bbox=(10.0, 10.0, 25.0, 20.0),
        text="A",
        font="helv",
        size=10.0,
        color=(0, 0, 0),
        translated="Very long translated text that cannot fit",
    )

    ok = draw_translated_block(page, block, min_font=6.0)

    assert ok is False
    doc.close()


def test_draw_translated_block_does_not_shrink_font() -> None:
    doc = fitz.open()
    page = doc.new_page()
    block = TextBlock(
        page_index=0,
        bbox=(10.0, 10.0, 60.0, 24.0),
        text="A",
        font="helv",
        size=11.0,
        color=(0, 0, 0),
        translated="Long long long long long long translated text",
    )
    calls: list[float] = []
    original_insert = fitz.Page.insert_textbox

    def _spy_insert(self, rect, text, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(float(kwargs["fontsize"]))
        return original_insert(self, rect, text, **kwargs)

    with patch.object(fitz.Page, "insert_textbox", _spy_insert):
        draw_translated_block(page, block, min_font=6.0)

    assert calls
    assert all(size == 11.0 for size in calls)
    doc.close()


def test_translate_blocks_sets_translated() -> None:
    blocks = [
        TextBlock(
            page_index=0,
            bbox=(1.0, 1.0, 100.0, 20.0),
            text="One",
            font="helv",
            size=12.0,
            color=(0, 0, 0),
        )
    ]

    translated = translate_blocks(blocks, _StubTranslator(), "en", "ru")

    assert translated[0].translated == "ru:One"


def test_translate_pdf_end_to_end(tmp_path: Path) -> None:
    input_pdf = tmp_path / "src.pdf"
    output_pdf = tmp_path / "dst.pdf"
    _make_simple_pdf(input_pdf, "Hello PDF")

    translate_pdf(
        input_path=str(input_pdf),
        output_path=str(output_pdf),
        translator=_StubTranslator(),
        src_lang="en",
        tgt_lang="ru",
    )

    assert output_pdf.exists()
    with fitz.open(str(output_pdf)) as doc:
        text = doc[0].get_text("text")
    # Accept both single-line and wrapped output
    assert "ru:Hello" in text and "PDF" in text


def test_draw_translated_blocks_pushes_down_lower_blocks() -> None:
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    blocks = [
        TextBlock(
            page_index=0,
            bbox=(20.0, 20.0, 140.0, 45.0),
            text="Top",
            size=12.0,
            color=(0, 0, 0),
            translated="Очень длинный верхний абзац " * 8,
            block_type="paragraph",
        ),
        TextBlock(
            page_index=0,
            bbox=(20.0, 50.0, 140.0, 75.0),
            text="Bottom",
            size=12.0,
            color=(0, 0, 0),
            translated="Нижний блок",
            block_type="paragraph",
        ),
    ]
    failed = draw_translated_blocks(doc, blocks)
    assert failed == 0
    first = fitz.Rect(blocks[0].bbox)
    second = fitz.Rect(blocks[1].bbox)
    assert second.y0 >= first.y1
    doc.close()
