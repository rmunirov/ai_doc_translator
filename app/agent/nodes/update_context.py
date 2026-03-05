"""Update context node — delegates to ContextSummaryAgent."""

import asyncio
import logging
from typing import Any

import httpx

from app.agent.state import TranslationState
from app.agents.context_agent import ContextInput, ContextSummaryAgent
from app.config import get_settings

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = (5, 15, 45)


def _estimate_tokens(text: str) -> int:
    """Approximate token count (match document_chunker)."""
    return int(len(text.split()) * 1.3)


async def update_context_node(state: TranslationState) -> dict[str, Any]:
    """Update context_summary from last translated chunk using ContextSummaryAgent.

    Skips LLM call if chunk is below context_update_min_tokens (saves tokens).
    """
    job_id = state.get("job_id", "unknown")
    llm = state.get("llm")
    translated_chunks = state.get("translated_chunks", [])
    if not llm:
        raise ValueError("llm is required for update_context_node")
    if not translated_chunks:
        return {"context_summary": ""}
    last_text = translated_chunks[-1]
    settings = get_settings()
    timeout_sec = max(1, settings.context_update_timeout_sec)
    max_chars = max(200, settings.context_update_max_chars)
    if _estimate_tokens(last_text) < settings.context_update_min_tokens:
        logger.debug(
            "Skipping context update (chunk below %d tokens)",
            settings.context_update_min_tokens,
        )
        return {"context_summary": ""}
    if len(last_text) > max_chars:
        logger.warning(
            "Trimming context input from %d to %d chars",
            len(last_text),
            max_chars,
        )
        last_text = last_text[:max_chars]
    agent = ContextSummaryAgent(llm=llm)
    input_data = ContextInput(translated_text=last_text)
    for attempt in range(_MAX_RETRIES):
        try:
            result = await asyncio.wait_for(
                agent.arun(input_data),
                timeout=timeout_sec,
            )
            logger.info(
                "Updated context",
                extra={"job_id": job_id},
            )
            return {"context_summary": result.summary}
        except (httpx.ReadTimeout, TimeoutError) as exc:
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "Timeout in update_context (attempt %d/%d), retrying in %ds",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    "update_context_node failed after %d retries, using empty context: %s",
                    _MAX_RETRIES,
                    exc,
                )
                return {"context_summary": ""}
        except Exception as exc:
            logger.warning(
                "update_context_node failed, using empty context: %s (job_id=%s)",
                exc,
                job_id,
            )
            return {"context_summary": ""}
    return {"context_summary": ""}
