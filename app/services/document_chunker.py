"""Splits a ParsedDocument into chunks sized for LLM context windows."""

import json

from app.models.schemas import Block, BlockType, Chunk, ParsedDocument


def _estimate_tokens(text: str) -> int:
    """Approximate token count using words * 1.3."""
    words = len(text.split())
    return int(words * 1.3)


def _extract_overlap(text: str, max_tokens: int) -> str:
    """Return the trailing fragment of text with up to max_tokens tokens."""
    if max_tokens <= 0:
        return ""
    words = text.split()
    if not words:
        return ""
    total_tokens = _estimate_tokens(text)
    if total_tokens <= max_tokens:
        return text.strip()
    target_words = int(max_tokens / 1.3)
    trailing_words = words[-target_words:]
    return " ".join(trailing_words)


def _should_break_before(
    block: Block,
    doc_format: str,
    current_tokens: int,
    min_chunk_tokens: int,
) -> bool:
    """Return True if a new chunk should start before this block.

    For PDF: break only before H1/H2 (major sections), not H3/table/list.
    Skip break if current chunk would be too small (< min_chunk_tokens).
    """
    if doc_format != "pdf":
        return False
    if current_tokens < min_chunk_tokens:
        return False
    if block.type != BlockType.HEADING:
        return False
    return block.level in (1, 2)


def chunk_document(
    doc: ParsedDocument,
    max_tokens: int = 2000,
    overlap_tokens: int = 200,
    chunk_max_tokens_pdf: int = 1200,
    chunk_min_tokens: int = 400,
) -> list[Chunk]:
    """Split a parsed document into chunks that fit within an LLM context window.

    Blocks are never split mid-way. Each chunk's overlap_prev contains the
    trailing text of the previous chunk for context continuity.

    For PDF: uses chunk_max_tokens_pdf and breaks only before H1/H2.
    Skips structural break if current chunk would be < chunk_min_tokens.

    Args:
        doc: The parsed document with a list of blocks.
        max_tokens: Maximum tokens per chunk for non-PDF formats.
        overlap_tokens: Tokens to carry from the previous chunk into overlap_prev.
        chunk_max_tokens_pdf: Max tokens per chunk for PDF.
        chunk_min_tokens: Min tokens before allowing structural break.

    Returns:
        A list of Chunk objects with index, blocks, text, and overlap_prev.
    """
    if not doc.blocks:
        return []

    effective_max = chunk_max_tokens_pdf if doc.format == "pdf" else max_tokens

    chunks: list[Chunk] = []
    current_blocks: list[Block] = []
    current_text_parts: list[str] = []
    current_tokens = 0
    chunk_index = 0
    overlap_for_next = ""

    for block in doc.blocks:
        if block.type == BlockType.TABLE and block.table_cells:
            block_text = json.dumps(
                {"type": "table", "cells": block.table_cells},
                ensure_ascii=False,
            )
        else:
            block_text = block.text.strip()
        block_tokens = _estimate_tokens(block_text)

        # Structural break: only before H1/H2, and only if chunk is large enough
        if current_blocks and _should_break_before(
            block, doc.format, current_tokens, chunk_min_tokens
        ):
            full_text = "\n\n".join(current_text_parts)
            chunks.append(
                Chunk(
                    index=chunk_index,
                    blocks=current_blocks.copy(),
                    text=full_text,
                    overlap_prev=overlap_for_next,
                )
            )
            overlap_for_next = _extract_overlap(full_text, overlap_tokens)
            chunk_index += 1
            current_blocks.clear()
            current_text_parts.clear()
            current_tokens = 0

        if current_blocks and current_tokens + block_tokens > effective_max:
            full_text = "\n\n".join(current_text_parts)
            chunks.append(
                Chunk(
                    index=chunk_index,
                    blocks=current_blocks.copy(),
                    text=full_text,
                    overlap_prev=overlap_for_next,
                )
            )
            overlap_for_next = _extract_overlap(full_text, overlap_tokens)
            chunk_index += 1
            current_blocks.clear()
            current_text_parts.clear()
            current_tokens = 0

        current_blocks.append(block)
        current_text_parts.append(block_text)
        current_tokens += block_tokens

    if current_blocks:
        full_text = "\n\n".join(current_text_parts)
        chunks.append(
            Chunk(
                index=chunk_index,
                blocks=current_blocks.copy(),
                text=full_text,
                overlap_prev=overlap_for_next,
            )
        )

    return chunks
