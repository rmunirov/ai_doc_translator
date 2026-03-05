"""Translations API — upload, status, download, cancel."""

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import TranslationJob
from app.models.database import get_db
from app.services.user import get_or_create_user
from app.models.job import JobStatus
from app.models.schemas import CancelResponse, JobStatusResponse, UploadResponse
from app.services.task_queue import translation_queue

logger = logging.getLogger(__name__)

router = APIRouter()

_ALLOWED_EXTENSIONS = {".pdf", ".txt", ".html", ".htm"}

@router.post("/upload", response_model=UploadResponse, status_code=202)
async def upload_file(
    file: UploadFile,
    target_lang: str = Form(...),
    user_id: uuid.UUID = Form(...),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    """Upload a document and enqueue it for translation.

    Args:
        file: The document to translate (PDF, TXT, HTML).
        target_lang: ISO 639-1 target language code.
        user_id: UUID of the user initiating the translation.
        db: Async database session (injected).

    Returns:
        UploadResponse with the new job_id and status "pending".

    Raises:
        HTTPException 400: If the file type is unsupported or exceeds size limit.
    """
    settings = get_settings()
    logger.info(
        "Upload request: filename=%s, target_lang=%s, user_id=%s",
        file.filename,
        target_lang,
        user_id,
    )

    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        logger.warning("Rejected upload: unsupported extension %s", ext)
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: pdf, txt, html.",
        )

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.max_file_size_mb:
        logger.warning(
            "Rejected upload: file too large (%.1f MB > %s MB)", size_mb, settings.max_file_size_mb
        )
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({size_mb:.1f} MB). Max: {settings.max_file_size_mb} MB.",
        )

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    save_path = upload_dir / f"{uuid.uuid4()}{ext}"
    save_path.write_bytes(content)
    await get_or_create_user(db, user_id)

    job = TranslationJob(
        user_id=user_id,
        status=JobStatus.PENDING,
        target_lang=target_lang,
        input_path=str(save_path),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    await translation_queue.enqueue(str(job.id))
    logger.info("Job enqueued: job_id=%s, target_lang=%s", job.id, target_lang)

    return UploadResponse(job_id=job.id, status="pending")


@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JobStatusResponse:
    """Return the current status and progress of a translation job.

    Args:
        job_id: UUID of the translation job.
        db: Async database session (injected).

    Returns:
        JobStatusResponse with status, progress counters, and optional error.

    Raises:
        HTTPException 404: If the job does not exist.
    """
    logger.debug("Status request: job_id=%s", job_id)
    job = await _get_job_or_404(db, job_id)
    return JobStatusResponse(
        job_id=job.id,
        status=job.status.value,
        source_lang=job.source_lang,
        target_lang=job.target_lang,
        chunk_done=job.chunk_done,
        chunk_total=job.chunk_total,
        error_msg=job.error_msg,
    )


@router.get("/{job_id}/download")
async def download_result(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Download the translated document.

    Args:
        job_id: UUID of the completed translation job.
        db: Async database session (injected).

    Returns:
        FileResponse with the translated file as an attachment.

    Raises:
        HTTPException 404: If the job does not exist or is not yet done.
    """
    logger.info("Download request: job_id=%s", job_id)
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.DONE or not job.output_path:
        logger.warning(
            "Download rejected: job_id=%s, status=%s (result not ready)",
            job_id,
            job.status.value,
        )
        raise HTTPException(
            status_code=404,
            detail="Result not available. Job must be in 'done' status.",
        )
    output_path = Path(job.output_path)
    if not output_path.exists():
        logger.error("Output file missing on disk: job_id=%s, path=%s", job_id, output_path)
        raise HTTPException(status_code=404, detail="Output file not found on disk.")

    original_name = Path(job.input_path).stem
    filename = f"{original_name}_translated{output_path.suffix}"
    logger.info("Serving download: job_id=%s, filename=%s", job_id, filename)
    return FileResponse(
        path=str(output_path),
        filename=filename,
        media_type="application/octet-stream",
    )


@router.delete("/{job_id}", response_model=CancelResponse)
async def cancel_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CancelResponse:
    """Cancel a pending or running translation job.

    Args:
        job_id: UUID of the job to cancel.
        db: Async database session (injected).

    Returns:
        CancelResponse confirming cancellation.

    Raises:
        HTTPException 404: If the job does not exist.
        HTTPException 409: If the job is already done, failed, or cancelled.
    """
    logger.info("Cancel request: job_id=%s", job_id)
    job = await _get_job_or_404(db, job_id)
    if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        logger.warning(
            "Cancel rejected: job_id=%s, status=%s (not cancellable)",
            job_id,
            job.status.value,
        )
        raise HTTPException(
            status_code=409,
            detail=f"Job cannot be cancelled in status '{job.status.value}'.",
        )
    await translation_queue.cancel(str(job_id))
    logger.info("Job cancelled: job_id=%s", job_id)
    return CancelResponse(status="cancelled")


async def _get_job_or_404(db: AsyncSession, job_id: uuid.UUID) -> TranslationJob:
    """Fetch a TranslationJob by id or raise HTTP 404.

    Args:
        db: Async database session.
        job_id: UUID of the job to fetch.

    Returns:
        TranslationJob ORM instance.

    Raises:
        HTTPException 404: If no job with the given id exists.
    """
    result = await db.execute(
        select(TranslationJob).where(TranslationJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        logger.debug("Job not found: job_id=%s", job_id)
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    return job
