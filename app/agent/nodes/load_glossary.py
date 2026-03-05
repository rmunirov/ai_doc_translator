"""Load glossary node — fetches user's glossary from DB."""

import logging
import uuid

from sqlalchemy import select

from app.agent.state import TranslationState
from app.models import Glossary
from app.models.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def load_glossary_node(state: TranslationState) -> dict[str, object]:
    """Load user's glossary as dict[source_term, target_term]."""
    job_id = state.get("job_id", "unknown")
    user_id_str = state.get("user_id", "")
    if not user_id_str:
        return {"glossary": {}}
    try:
        uid = uuid.UUID(user_id_str)
    except ValueError:
        return {"glossary": {}}
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Glossary).where(Glossary.user_id == uid)
        )
        rows = result.scalars().all()
        glossary = {r.source_term: r.target_term for r in rows}
    logger.info(
        "Loaded glossary",
        extra={"job_id": job_id, "terms": len(glossary)},
    )
    return {"glossary": glossary}
