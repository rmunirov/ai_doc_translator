"""LangChain tools for glossary lookup and language detection."""

import uuid

import langdetect
from langchain_core.tools import tool
from sqlalchemy import select

from app.models import Glossary
from app.models.database import AsyncSessionLocal


@tool
async def lookup_glossary(term: str, user_id: str) -> str:
    """Find a term's translation in the user's glossary.

    Args:
        term: Source term to look up.
        user_id: User's UUID.

    Returns:
        Target term if found, else empty string.
    """
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return ""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Glossary).where(
                Glossary.user_id == uid, Glossary.source_term == term
            )
        )
        row = result.scalar_one_or_none()
        return row.target_term if row else ""


@tool
def detect_language_tool(text: str) -> str:
    """Detect the language of the given text.

    Returns ISO 639-1 code (ru, en, de, etc.).
    """
    if not text or not text.strip():
        return "en"
    try:
        return str(langdetect.detect(text))
    except langdetect.LangDetectException:
        return "en"
