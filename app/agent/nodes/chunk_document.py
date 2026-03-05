"""Chunk document node — splits parsed doc into chunks for LLM."""

import logging
from typing import Any

from app.agent.state import TranslationState
from app.config import get_settings
from app.services.document_chunker import chunk_document

logger = logging.getLogger(__name__)


async def chunk_document_node(state: TranslationState) -> dict[str, Any]:
    """Split parsed document into chunks and set current_chunk_idx to 0."""
    job_id = state.get("job_id", "unknown")
    parsed_doc = state.get("parsed_doc")
    if not parsed_doc:
        raise ValueError("parsed_doc is required")
    settings = get_settings()
    chunks = chunk_document(
        parsed_doc,
        chunk_max_tokens_pdf=settings.chunk_max_tokens_pdf,
        chunk_min_tokens=settings.chunk_min_tokens,
    )
    logger.info(
        "Chunked document",
        extra={"job_id": job_id, "chunk_count": len(chunks)},
    )
    return {
        "chunks": chunks,
        "current_chunk_idx": 0,
        "translated_chunks": [],
        "context_summary": "",
    }
