"""User service — get-or-create for anonymous users."""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User

logger = logging.getLogger(__name__)


async def get_or_create_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Return existing user or create anonymous user with given id.

    Used when the frontend sends a user_id from localStorage that may not
    yet exist in the database. Creates user in the same session/transaction
    so the caller can commit both user and FK-referencing rows together.

    Args:
        db: Async database session.
        user_id: UUID of the user (from frontend).

    Returns:
        User ORM instance (existing or newly created).
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            id=user_id,
            email=f"anonymous-{user_id}@local",
        )
        db.add(user)
        await db.flush()
    return user
