"""Glossary API — CRUD for user term mappings."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Glossary
from app.models.database import get_db
from app.services.user import get_or_create_user
from app.models.schemas import (
    GlossaryEntryCreate,
    GlossaryEntryResponse,
    GlossaryEntryUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[GlossaryEntryResponse])
async def list_glossary(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[GlossaryEntryResponse]:
    """Return all glossary entries for the given user.

    Args:
        user_id: UUID of the user whose glossary to fetch.
        db: Async database session (injected).

    Returns:
        List of GlossaryEntryResponse sorted by creation date.
    """
    result = await db.execute(
        select(Glossary)
        .where(Glossary.user_id == user_id)
        .order_by(Glossary.created_at)
    )
    rows = result.scalars().all()
    return [GlossaryEntryResponse.model_validate(r) for r in rows]


@router.post("", response_model=GlossaryEntryResponse, status_code=201)
async def create_glossary_entry(
    body: GlossaryEntryCreate,
    db: AsyncSession = Depends(get_db),
) -> GlossaryEntryResponse:
    """Add a new term → translation mapping to the user's glossary.

    Args:
        body: GlossaryEntryCreate with user_id, source_term, target_term.
        db: Async database session (injected).

    Returns:
        The created GlossaryEntryResponse.

    Raises:
        HTTPException 409: If the (user_id, source_term) pair already exists.
    """
    await get_or_create_user(db, body.user_id)

    entry = Glossary(
        user_id=body.user_id,
        source_term=body.source_term,
        target_term=body.target_term,
    )
    db.add(entry)
    try:
        await db.commit()
        await db.refresh(entry)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Term '{body.source_term}' already exists in the glossary.",
        )
    return GlossaryEntryResponse.model_validate(entry)


@router.put("/{entry_id}", response_model=GlossaryEntryResponse)
async def update_glossary_entry(
    entry_id: uuid.UUID,
    body: GlossaryEntryUpdate,
    db: AsyncSession = Depends(get_db),
) -> GlossaryEntryResponse:
    """Update an existing glossary entry.

    Args:
        entry_id: UUID of the glossary entry to update.
        body: New source_term and target_term values.
        db: Async database session (injected).

    Returns:
        Updated GlossaryEntryResponse.

    Raises:
        HTTPException 404: If the entry does not exist.
    """
    entry = await _get_entry_or_404(db, entry_id)
    await db.execute(
        update(Glossary)
        .where(Glossary.id == entry_id)
        .values(source_term=body.source_term, target_term=body.target_term)
    )
    await db.commit()
    await db.refresh(entry)
    return GlossaryEntryResponse.model_validate(entry)


@router.delete("/{entry_id}", status_code=204)
async def delete_glossary_entry(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a glossary entry.

    Args:
        entry_id: UUID of the glossary entry to delete.
        db: Async database session (injected).

    Raises:
        HTTPException 404: If the entry does not exist.
    """
    await _get_entry_or_404(db, entry_id)
    await db.execute(delete(Glossary).where(Glossary.id == entry_id))
    await db.commit()


async def _get_entry_or_404(db: AsyncSession, entry_id: uuid.UUID) -> Glossary:
    """Fetch a Glossary entry by id or raise HTTP 404.

    Args:
        db: Async database session.
        entry_id: UUID of the entry.

    Returns:
        Glossary ORM instance.

    Raises:
        HTTPException 404: If no entry with the given id exists.
    """
    result = await db.execute(select(Glossary).where(Glossary.id == entry_id))
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Glossary entry {entry_id} not found.")
    return entry
