"""Translate chunk node — delegates to TranslationAgent."""

import asyncio
import logging
from typing import Any

import httpx

from app.agent.state import TranslationState
from app.agents.translation_agent import TranslationAgent, TranslateInput

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = (5, 15, 45)  # seconds between retries


async def translate_chunk_node(state: TranslationState) -> dict[str, Any]:
    """Translate the current chunk using TranslationAgent."""
    job_id = state.get("job_id", "unknown")
    llm = state.get("llm")
    chunks = state.get("chunks", [])
    current_idx = state.get("current_chunk_idx", 0)
    glossary = state.get("glossary", {})
    context_summary = state.get("context_summary", "")
    source_lang = state.get("source_lang", "en")
    target_lang = state.get("target_lang", "")
    translated_chunks = list(state.get("translated_chunks", []))
    if not llm:
        raise ValueError("llm is required for translate_chunk_node")
    if current_idx >= len(chunks):
        return {"translated_chunks": translated_chunks}
    chunk = chunks[current_idx]
    chunk_text = chunk.text
    agent = TranslationAgent(llm=llm)
    input_data = TranslateInput(
        text=chunk_text,
        source_lang=source_lang,
        target_lang=target_lang,
        glossary=glossary,
        context_summary=context_summary,
    )
    for attempt in range(_MAX_RETRIES):
        try:
            result = await agent.arun(input_data)
            translated_chunks.append(result.translated_text)
            logger.info(
                "Translated chunk",
                extra={"job_id": job_id, "chunk_idx": current_idx},
            )
            return {"translated_chunks": translated_chunks}
        except httpx.ReadTimeout as exc:
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "ReadTimeout on chunk %d (attempt %d/%d), retrying in %ds",
                    current_idx,
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.exception(
                    "translate_chunk_node failed after %d retries: %s",
                    _MAX_RETRIES,
                    exc,
                )
                raise
        except Exception as exc:
            logger.exception(
                "translate_chunk_node failed: %s (job_id=%s, chunk_idx=%d)",
                exc,
                job_id,
                current_idx,
            )
            raise
