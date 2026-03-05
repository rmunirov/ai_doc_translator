"""Tests for chunk_document — token limit, overlap, block integrity."""

from app.models.schemas import Block, BlockType, Chunk, ParsedDocument
from app.services.document_chunker import chunk_document


def _estimate_tokens(text: str) -> int:
    """Mirror of chunker's token estimation (words * 1.3)."""
    return int(len(text.split()) * 1.3)


def test_chunk_no_block_split() -> None:
    """A block that alone fits in max_tokens is never split across two chunks."""
    blocks = [
        Block(type=BlockType.PARAGRAPH, text="Short paragraph."),
    ]
    doc = ParsedDocument(format="txt", blocks=blocks)
    chunks = chunk_document(doc, max_tokens=2000, overlap_tokens=200)
    assert len(chunks) == 1
    assert len(chunks[0].blocks) == 1
    assert chunks[0].blocks[0].text == "Short paragraph."


def test_chunk_respects_max_tokens() -> None:
    """Each chunk's estimated token count <= max_tokens (except oversized single blocks)."""
    words = ["word"] * 500
    blocks = [
        Block(type=BlockType.PARAGRAPH, text=" ".join(words[:200])),
        Block(type=BlockType.PARAGRAPH, text=" ".join(words[200:400])),
        Block(type=BlockType.PARAGRAPH, text=" ".join(words[400:])),
    ]
    doc = ParsedDocument(format="txt", blocks=blocks)
    max_tokens = 300
    chunks = chunk_document(doc, max_tokens=max_tokens, overlap_tokens=50)
    for chunk in chunks:
        token_est = _estimate_tokens(chunk.text)
        if len(chunk.blocks) == 1 and token_est > max_tokens:
            continue
        assert token_est <= max_tokens, f"Chunk exceeded max_tokens: {token_est}"


def test_chunk_overlap_prev() -> None:
    """Second chunk's overlap_prev equals tail of first chunk's text."""
    blocks = [
        Block(type=BlockType.PARAGRAPH, text="A " * 300),
        Block(type=BlockType.PARAGRAPH, text="B " * 300),
    ]
    doc = ParsedDocument(format="txt", blocks=blocks)
    overlap_tokens = 50
    chunks = chunk_document(doc, max_tokens=200, overlap_tokens=overlap_tokens)
    assert len(chunks) >= 2
    first_text = chunks[0].text
    overlap = chunks[1].overlap_prev
    assert overlap
    assert overlap in first_text
    assert first_text.endswith(overlap) or overlap in first_text[-500:]


def test_chunk_empty_doc() -> None:
    """Empty ParsedDocument.blocks produces empty list."""
    doc = ParsedDocument(format="txt", blocks=[])
    chunks = chunk_document(doc)
    assert chunks == []


def test_chunk_single_oversized_block() -> None:
    """One block larger than max_tokens goes into its own chunk."""
    big_text = "x " * 2000
    blocks = [Block(type=BlockType.PARAGRAPH, text=big_text)]
    doc = ParsedDocument(format="txt", blocks=blocks)
    chunks = chunk_document(doc, max_tokens=500, overlap_tokens=50)
    assert len(chunks) == 1
    assert len(chunks[0].blocks) == 1
    assert chunks[0].text.strip() == big_text.strip()


def test_chunk_index_sequential() -> None:
    """Chunk indices are 0, 1, 2, ... in order."""
    blocks = [
        Block(type=BlockType.PARAGRAPH, text="A " * 300),
        Block(type=BlockType.PARAGRAPH, text="B " * 300),
        Block(type=BlockType.PARAGRAPH, text="C " * 300),
    ]
    doc = ParsedDocument(format="txt", blocks=blocks)
    chunks = chunk_document(doc, max_tokens=200, overlap_tokens=30)
    for i, chunk in enumerate(chunks):
        assert chunk.index == i


def test_chunk_pdf_structural_breaks() -> None:
    """PDF: new chunk starts before H1/H2 when current chunk >= min_tokens."""
    # Intro large enough (>=400 tokens) to trigger break before H1
    intro = " ".join(["word"] * 350)
    blocks = [
        Block(type=BlockType.PARAGRAPH, text=intro),
        Block(type=BlockType.HEADING, text="Section 1", level=1),
        Block(type=BlockType.PARAGRAPH, text="Content under section."),
        Block(type=BlockType.TABLE, text="A | B\n1 | 2"),
        Block(type=BlockType.PARAGRAPH, text="After table."),
    ]
    doc = ParsedDocument(format="pdf", blocks=blocks)
    chunks = chunk_document(
        doc,
        max_tokens=2000,
        overlap_tokens=50,
        chunk_max_tokens_pdf=1200,
        chunk_min_tokens=400,
    )
    # Break before H1 (intro >= 400 tokens), so at least 2 chunks
    assert len(chunks) >= 2
    assert chunks[0].blocks[0].type == BlockType.PARAGRAPH
    assert chunks[1].blocks[0].type == BlockType.HEADING
    # Table stays in same chunk as heading (no break before TABLE)
    assert any(b.type == BlockType.TABLE for b in chunks[1].blocks)
