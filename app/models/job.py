"""ORM model for the translation_jobs table and JobStatus enum."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base

if TYPE_CHECKING:
    from app.models.history import TranslationHistory
    from app.models.user import User


class JobStatus(str, enum.Enum):
    """Possible states of a translation job."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class TranslationJob(Base):
    """A single document translation task."""

    __tablename__ = "translation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            name="jobstatus",
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
        default=JobStatus.PENDING,
    )
    source_lang: Mapped[str | None] = mapped_column(String(8), nullable=True)
    target_lang: Mapped[str] = mapped_column(String(8), nullable=False)
    input_path: Mapped[str] = mapped_column(Text, nullable=False)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="jobs")
    history: Mapped[TranslationHistory | None] = relationship(
        back_populates="job", uselist=False
    )
