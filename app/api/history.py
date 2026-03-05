"""History API — list and delete translation history records."""

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TranslationHistory, TranslationJob
from app.models.database import get_db
from app.models.schemas import HistoryItemResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[HistoryItemResponse])
async def list_history(
    user_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> list[HistoryItemResponse]:
    """Return translation history for a user with pagination.

    Args:
        user_id: UUID of the user whose history to fetch.
        limit: Maximum number of records to return (default 20).
        offset: Number of records to skip for pagination (default 0).
        db: Async database session (injected).

    Returns:
        List of HistoryItemResponse ordered by creation date descending.
    """
    result = await db.execute(
        select(TranslationHistory)
        .where(TranslationHistory.user_id == user_id)
        .order_by(TranslationHistory.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.scalars().all()
    return [HistoryItemResponse.model_validate(r) for r in rows]


@router.delete("/{history_id}", status_code=204)
async def delete_history(
    history_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a history record and associated files from disk.

    Also deletes the related TranslationJob's input and output files.

    Args:
        history_id: UUID of the TranslationHistory record to delete.
        db: Async database session (injected).

    Raises:
        HTTPException 404: If the history record does not exist.
    """
    history = await _get_history_or_404(db, history_id)

    job_result = await db.execute(
        select(TranslationJob).where(TranslationJob.id == history.job_id)
    )
    job = job_result.scalar_one_or_none()

    if job:
        _delete_file_if_exists(job.input_path)
        _delete_file_if_exists(job.output_path)

    await db.execute(
        delete(TranslationHistory).where(TranslationHistory.id == history_id)
    )
    await db.commit()
    logger.info(
        "Deleted history record",
        extra={"history_id": str(history_id), "job_id": str(history.job_id)},
    )


def _delete_file_if_exists(path: str | None) -> None:
    """Remove a file from disk if the path is set and the file exists.

    Args:
        path: Filesystem path string or None.
    """
    if not path:
        return
    p = Path(path)
    if p.exists():
        try:
            p.unlink()
        except OSError as exc:
            logger.warning("Could not delete file %s: %s", path, exc)


async def _get_history_or_404(
    db: AsyncSession, history_id: uuid.UUID
) -> TranslationHistory:
    """Fetch a TranslationHistory record by id or raise HTTP 404.

    Args:
        db: Async database session.
        history_id: UUID of the record.

    Returns:
        TranslationHistory ORM instance.

    Raises:
        HTTPException 404: If no record with the given id exists.
    """
    result = await db.execute(
        select(TranslationHistory).where(TranslationHistory.id == history_id)
    )
    history = result.scalar_one_or_none()
    if history is None:
        raise HTTPException(
            status_code=404, detail=f"History record {history_id} not found."
        )
    return history
