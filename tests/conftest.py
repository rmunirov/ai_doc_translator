"""Shared pytest fixtures for AI Doc Translator tests."""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agents.translation_agent import TranslateOutput
from app.models import User
from app.models.database import Base, get_db
from langchain_core.language_models import BaseChatModel


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async SQLite in-memory DB session for tests.

    Patches app.models.database so agent nodes use this engine.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False
    )

    # Patch so agent nodes (update_job_progress, save_history, load_glossary)
    # use this test engine. Must patch both the database module and each
    # node module that imports AsyncSessionLocal (they hold their own ref).
    import app.models.database as db_module

    original_engine = db_module.engine
    original_session = db_module.AsyncSessionLocal
    db_module.engine = engine
    db_module.AsyncSessionLocal = async_session_factory

    from unittest.mock import patch

    patches = [
        patch("app.agent.nodes.update_job_progress.AsyncSessionLocal", async_session_factory),
        patch("app.agent.nodes.save_history.AsyncSessionLocal", async_session_factory),
        patch("app.agent.nodes.load_glossary.AsyncSessionLocal", async_session_factory),
    ]
    for p in patches:
        p.start()

    try:
        async with async_session_factory() as session:
            yield session
    finally:
        for p in patches:
            p.stop()
        db_module.engine = original_engine
        db_module.AsyncSessionLocal = original_session
        await engine.dispose()


@pytest.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Create and return a test user in the database."""
    user = User(id=uuid.uuid4(), email="test@example.com")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Mock LLM fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm() -> MagicMock:
    """Provide a MagicMock for BaseChatModel with structured output chain."""
    llm = MagicMock(spec=BaseChatModel)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=TranslateOutput(translated_text="TRANSLATED"))
    llm.with_structured_output.return_value = chain
    return llm


# ---------------------------------------------------------------------------
# HTTP client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provide an httpx AsyncClient with get_db overridden to use db_session."""
    from app.main import app

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Mock translation queue so upload doesn't actually process jobs
    from app.services.task_queue import translation_queue

    translation_queue.enqueue = AsyncMock()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# File fixtures (TXT, HTML, PDF)
# ---------------------------------------------------------------------------


@pytest.fixture
def txt_file(tmp_path: Path) -> Path:
    """Create a minimal TXT file with paragraphs separated by double newlines."""
    path = tmp_path / "sample.txt"
    path.write_text(
        "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def html_file(tmp_path: Path) -> Path:
    """Create a minimal HTML file with h1, h2, p, li, table."""
    path = tmp_path / "sample.html"
    path.write_text(
        """<!DOCTYPE html>
<html><body>
<h1>Main Heading</h1>
<h2>Sub Heading</h2>
<p>A paragraph of text.</p>
<ul><li>List item one</li><li>List item two</li></ul>
<table><tr><td>Cell A</td><td>Cell B</td></tr></table>
</body></html>""",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def pdf_file(tmp_path: Path) -> Path:
    """Create a minimal one-line PDF using reportlab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    path = tmp_path / "sample.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(100, 750, "Sample PDF content for testing.")
    c.save()
    return path
