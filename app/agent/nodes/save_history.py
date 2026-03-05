"""Save history node — updates job and inserts translation_history."""

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import update

from app.agent.state import TranslationState
from app.models import TranslationHistory, TranslationJob
from app.models.database import AsyncSessionLocal
from app.models.job import JobStatus

logger = logging.getLogger(__name__)


async def save_history_node(state: TranslationState) -> dict[str, Any]:
    """Update job output_path/status and insert translation_history record."""
    job_id_str = state.get("job_id", "")
    user_id_str = state.get("user_id", "")
    result_path = state.get("result_path", "")
    source_lang = state.get("source_lang")
    target_lang = state.get("target_lang", "")
    input_path = state.get("input_path", "")
    assembly_warning = state.get("assembly_warning", "")
    if not job_id_str or not user_id_str or not result_path:
        return {}
    try:
        jid = uuid.UUID(job_id_str)
        uid = uuid.UUID(user_id_str)
    except ValueError:
        return {}
    filename = Path(input_path).name
    char_count = sum(
        len(c) for c in state.get("translated_chunks", [])
    )
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(TranslationJob)
            .where(TranslationJob.id == jid)
            .values(
                output_path=result_path,
                status=JobStatus.DONE,
                finished_at=datetime.now(),
                error_msg=assembly_warning or None,
            )
        )
        history = TranslationHistory(
            job_id=jid,
            user_id=uid,
            filename=filename,
            source_lang=source_lang,
            target_lang=target_lang,
            char_count=char_count,
        )
        session.add(history)
        await session.commit()
    logger.info(
        "Saved history",
        extra={"job_id": job_id_str, "filename": filename},
    )
    return {}
