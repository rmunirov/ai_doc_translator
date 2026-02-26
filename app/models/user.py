"""ORM model for the users table."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base

if TYPE_CHECKING:
    from app.models.glossary import Glossary
    from app.models.history import TranslationHistory
    from app.models.job import TranslationJob


class User(Base):
    """Registered user."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    jobs: Mapped[list[TranslationJob]] = relationship(back_populates="user")
    glossaries: Mapped[list[Glossary]] = relationship(back_populates="user")
    history_entries: Mapped[list[TranslationHistory]] = relationship(
        back_populates="user"
    )
