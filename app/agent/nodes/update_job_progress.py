"""Update job progress node — increments chunk_done in DB."""

import logging
from datetime import datetime
from typing import Any

import uuid
from sqlalchemy import update

from app.agent.state import TranslationState
from app.config import get_settings
from app.models import TranslationJob
from app.models.database import AsyncSessionLocal
from app.models.job import JobStatus

logger = logging.getLogger(__name__)


async def update_job_progress_node(state: TranslationState) -> dict[str, Any]:
    """Increment chunk_done in translation_jobs, set started_at.

    DB write is batched: every progress_update_batch_size chunks, plus
    always on first and last chunk.

    Note:
        Status ``DONE`` is not set here. It is finalized in ``save_history_node``
        only after document assembly succeeds.
    """
    job_id_str = state.get("job_id", "")
    current_idx = state.get("current_chunk_idx", 0)
    chunks = state.get("chunks", [])
    if not job_id_str:
        return {}
    try:
        jid = uuid.UUID(job_id_str)
    except ValueError:
        return {}
    chunk_done = current_idx + 1
    is_first = current_idx == 0
    is_last = chunk_done >= len(chunks)
    settings = get_settings()
    batch_size = max(1, settings.progress_update_batch_size)
    should_write = (
        is_first
        or is_last
        or (chunk_done % batch_size == 0)
    )
    if should_write:
        async with AsyncSessionLocal() as session:
            values: dict[str, Any] = {"chunk_done": chunk_done}
            if is_first:
                values["status"] = JobStatus.RUNNING
                values["started_at"] = datetime.now()
                values["chunk_total"] = len(chunks)
            await session.execute(
                update(TranslationJob).where(TranslationJob.id == jid).values(**values)
            )
            await session.commit()
        logger.info(
            "Updated job progress",
            extra={"job_id": job_id_str, "chunk_done": chunk_done},
        )
    return {"current_chunk_idx": chunk_done}
