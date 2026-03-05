"""Detect language node — determines source language from document text."""

import logging

import langdetect
from app.agent.state import TranslationState
from app.agent.tools import detect_language_tool

logger = logging.getLogger(__name__)


def _get_sample_text(state: TranslationState) -> str:
    """Extract first ~1000 chars from parsed document blocks."""
    parsed = state.get("parsed_doc")
    if not parsed or not parsed.blocks:
        return ""
    parts: list[str] = []
    total = 0
    for block in parsed.blocks:
        parts.append(block.text)
        total += len(block.text)
        if total >= 1000:
            break
    return " ".join(parts)[:1000]


async def detect_language_node(state: TranslationState) -> dict[str, str]:
    """Detect source language using langdetect on first 1000 chars."""
    job_id = state.get("job_id", "unknown")
    sample = _get_sample_text(state)
    if not sample.strip():
        return {"source_lang": "en"}
    try:
        source_lang = detect_language_tool.invoke({"text": sample})
        logger.info(
            "Detected language",
            extra={"job_id": job_id, "source_lang": source_lang},
        )
        return {"source_lang": source_lang}
    except langdetect.LangDetectException:
        logger.warning(
            "langdetect failed, defaulting to en",
            extra={"job_id": job_id},
        )
        return {"source_lang": "en"}
