"""Async task queue — runs translation jobs in background workers."""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update

from app.agent.graph import build_translation_graph
from app.agent.state import TranslationState
from app.agents.llm_factory import get_llm
from app.models import TranslationJob
from app.models.database import AsyncSessionLocal
from app.models.job import JobStatus

logger = logging.getLogger(__name__)


class TranslationQueue:
    """Manages a pool of async workers that execute translation jobs.

    Workers pull job_id strings from an asyncio.Queue, load the job from
    PostgreSQL, build a TranslationState, and invoke the LangGraph pipeline.
    """

    def __init__(self) -> None:
        """Initialize queue and internal tracking structures."""
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._running: dict[str, asyncio.Task[None]] = {}
        self._cancel_flags: dict[str, bool] = {}

    async def startup(self) -> None:
        """Reset any RUNNING jobs left over from a previous crash to ERROR.

        Should be called once at application startup before workers begin.
        """
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(TranslationJob)
                .where(TranslationJob.status == JobStatus.RUNNING)
                .values(
                    status=JobStatus.ERROR,
                    error_msg="Interrupted by restart",
                    finished_at=datetime.now(),
                )
            )
            await session.commit()
        logger.info("startup: reset stale RUNNING jobs to ERROR")

    async def enqueue(self, job_id: str) -> None:
        """Add a job_id to the processing queue.

        Args:
            job_id: String UUID of the TranslationJob to process.
        """
        self._cancel_flags[job_id] = False
        await self._queue.put(job_id)
        logger.info("Enqueued job", extra={"job_id": job_id})

    async def shutdown(self) -> None:
        """Cancel all running jobs and wait for workers to exit cleanly.

        Call this during application shutdown before the event loop exits.
        """
        for job_id, task in list(self._running.items()):
            if not task.done():
                task.cancel()
        logger.info("shutdown: cancelled %d running jobs", len(self._running))

    async def cancel(self, job_id: str) -> None:
        """Request cancellation of a running or queued job.

        Sets the cancel flag so the graph loop stops at the next chunk
        boundary. If the job is currently running, also cancels its Task.

        Args:
            job_id: String UUID of the job to cancel.
        """
        self._cancel_flags[job_id] = True
        task = self._running.get(job_id)
        if task and not task.done():
            task.cancel()
        logger.info("Cancellation requested", extra={"job_id": job_id})

    async def _worker(self) -> None:
        """Infinite worker loop — pulls job_ids and processes them."""
        while True:
            job_id = await self._queue.get()
            try:
                task: asyncio.Task[None] = asyncio.create_task(
                    self._run_job(job_id)
                )
                self._running[job_id] = task
                await task
            except asyncio.CancelledError:
                logger.info("Worker task cancelled", extra={"job_id": job_id})
            except Exception:
                logger.exception("Worker unexpected error", extra={"job_id": job_id})
            finally:
                self._running.pop(job_id, None)
                self._cancel_flags.pop(job_id, None)
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        """Load a job from DB, build state, run the translation graph.

        On success the graph nodes update the job status themselves.
        On error the job status is set to ERROR with an error message.
        On cancellation the job status is set to CANCELLED.

        Args:
            job_id: String UUID of the TranslationJob to run.
        """
        logger.info("Starting job", extra={"job_id": job_id})
        try:
            jid = uuid.UUID(job_id)
        except ValueError:
            logger.error("Invalid job_id UUID", extra={"job_id": job_id})
            return

        job = await self._load_job(jid)
        if job is None:
            logger.error("Job not found", extra={"job_id": job_id})
            return

        cancelled = self._cancel_flags.get(job_id, False)
        state = TranslationState(
            job_id=str(job.id),
            user_id=str(job.user_id),
            target_lang=job.target_lang,
            input_path=job.input_path,
            cancelled=cancelled,
            llm=get_llm(),
        )

        try:
            graph = build_translation_graph()
            await graph.ainvoke(state)
            logger.info("Job completed", extra={"job_id": job_id})
        except asyncio.CancelledError:
            await self._mark_job(jid, JobStatus.CANCELLED, "Cancelled by user")
            logger.info("Job cancelled", extra={"job_id": job_id})
            raise
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            await self._mark_job(jid, JobStatus.ERROR, error_msg)
            logger.error(
                "Job failed",
                extra={"job_id": job_id, "error": error_msg},
            )

    async def _load_job(self, jid: uuid.UUID) -> TranslationJob | None:
        """Fetch a TranslationJob from the database by UUID.

        Args:
            jid: UUID of the job.

        Returns:
            TranslationJob ORM instance or None if not found.
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TranslationJob).where(TranslationJob.id == jid)
            )
            return result.scalar_one_or_none()

    async def _mark_job(
        self, jid: uuid.UUID, status: JobStatus, error_msg: str
    ) -> None:
        """Update job status and error_msg in the database.

        Args:
            jid: UUID of the job to update.
            status: New JobStatus value.
            error_msg: Human-readable error description.
        """
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(TranslationJob)
                .where(TranslationJob.id == jid)
                .values(
                    status=status,
                    error_msg=error_msg,
                    finished_at=datetime.now(),
                )
            )
            await session.commit()


translation_queue = TranslationQueue()
