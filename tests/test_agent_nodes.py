"""Tests for LangGraph nodes — function nodes and agent nodes with mock LLM."""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context_agent import ContextSummaryAgent
from app.agent.nodes.assemble_document import assemble_document_node
from app.agent.nodes.chunk_document import chunk_document_node
from app.agent.nodes.detect_language import detect_language_node
from app.agent.nodes.load_glossary import load_glossary_node
from app.agent.nodes.parse_document import parse_document_node
from app.agent.nodes.save_history import save_history_node
from app.agent.nodes.translate_chunk import translate_chunk_node
from app.agent.nodes.update_context import update_context_node
from app.agent.nodes.update_job_progress import update_job_progress_node
from app.agent.state import TranslationState
from app.models import TranslationJob, User
from app.models.job import JobStatus
from app.models.schemas import Block, BlockType, Chunk, ParsedDocument


async def test_parse_document_node(txt_file: Path) -> None:
    """Given a TXT path, parsed_doc is set in returned dict."""
    state = TranslationState(input_path=str(txt_file))
    result = await parse_document_node(state)
    assert "parsed_doc" in result
    assert result["parsed_doc"] is not None
    assert result["parsed_doc"].format == "txt"
    assert len(result["parsed_doc"].blocks) == 3


async def test_detect_language_node() -> None:
    """English text yields source_lang == 'en'."""
    blocks = [
        Block(type=BlockType.PARAGRAPH, text="This is English text for detection."),
    ]
    parsed = ParsedDocument(format="txt", blocks=blocks)
    state = TranslationState(parsed_doc=parsed)
    result = await detect_language_node(state)
    assert result["source_lang"] == "en"


async def test_chunk_document_node() -> None:
    """parsed_doc with 3 blocks yields chunks list with >= 1 entry."""
    blocks = [
        Block(type=BlockType.PARAGRAPH, text="Block one."),
        Block(type=BlockType.PARAGRAPH, text="Block two."),
        Block(type=BlockType.PARAGRAPH, text="Block three."),
    ]
    parsed = ParsedDocument(format="txt", blocks=blocks)
    state = TranslationState(parsed_doc=parsed)
    result = await chunk_document_node(state)
    assert "chunks" in result
    assert len(result["chunks"]) >= 1
    assert result["current_chunk_idx"] == 0
    assert result["translated_chunks"] == []
    assert result["context_summary"] == ""


async def test_update_job_progress_node(db_session: AsyncSession) -> None:
    """chunk_done increments in DB, returns updated current_chunk_idx."""
    user = User(id=uuid.uuid4(), email="progress@test.com")
    db_session.add(user)
    await db_session.commit()

    job = TranslationJob(
        id=uuid.uuid4(),
        user_id=user.id,
        status=JobStatus.PENDING,
        target_lang="ru",
        input_path="/tmp/input.txt",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    chunk = Chunk(
        index=0,
        blocks=[Block(type=BlockType.PARAGRAPH, text="Text")],
        text="Text",
    )
    state = TranslationState(
        job_id=str(job.id),
        chunks=[chunk],
        current_chunk_idx=0,
    )
    result = await update_job_progress_node(state)
    assert result["current_chunk_idx"] == 1

    await db_session.refresh(job)
    assert job.chunk_done == 1
    # DONE is finalized later in save_history_node.
    assert job.status == JobStatus.RUNNING


async def test_assemble_document_node_txt(tmp_path: Path) -> None:
    """Calls assemble_document, returns result_path ending in .txt."""
    blocks = [
        Block(type=BlockType.PARAGRAPH, text="One"),
        Block(type=BlockType.PARAGRAPH, text="Two"),
    ]
    parsed = ParsedDocument(format="txt", blocks=blocks)
    chunk = Chunk(
        index=0,
        blocks=blocks,
        text="One\n\nTwo",
    )
    # translated_chunks: one string per chunk (blocks joined by \n\n)
    state = TranslationState(
        parsed_doc=parsed,
        chunks=[chunk],
        translated_chunks=["Uno\n\nDos"],
        input_path=str(tmp_path / "input.txt"),
    )
    result = await assemble_document_node(state)
    assert "result_path" in result
    assert result["result_path"].endswith(".txt")
    assert Path(result["result_path"]).exists()


async def test_save_history_node(db_session: AsyncSession) -> None:
    """Inserts TranslationHistory row into DB."""
    user = User(id=uuid.uuid4(), email="history@test.com")
    db_session.add(user)
    await db_session.commit()

    job = TranslationJob(
        id=uuid.uuid4(),
        user_id=user.id,
        status=JobStatus.DONE,
        target_lang="ru",
        input_path="/tmp/doc.txt",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    state = TranslationState(
        job_id=str(job.id),
        user_id=str(user.id),
        result_path="/tmp/result.txt",
        input_path="/tmp/doc.txt",
        translated_chunks=["Translated"],
        source_lang="en",
        target_lang="ru",
    )
    await save_history_node(state)

    from app.models import TranslationHistory
    from sqlalchemy import select

    r = await db_session.execute(
        select(TranslationHistory).where(TranslationHistory.job_id == job.id)
    )
    hist = r.scalar_one_or_none()
    assert hist is not None
    assert hist.filename == "doc.txt"
    assert hist.source_lang == "en"
    assert hist.target_lang == "ru"
    await db_session.refresh(job)
    assert job.status == JobStatus.DONE


async def test_translate_chunk_uses_glossary(mock_llm: MagicMock) -> None:
    """Mock chain's ainvoke is called with a string containing the glossary term."""
    from app.agents.translation_agent import TranslationAgent, TranslateOutput

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=TranslateOutput(translated_text="TRANSLATED"))
    orig_init = TranslationAgent.__init__

    def patched_init(self: object, llm: object) -> None:
        orig_init(self, llm)
        setattr(self, "_chain", mock_chain)

    with patch.object(TranslationAgent, "__init__", patched_init):
        chunk = Chunk(
            index=0,
            blocks=[Block(type=BlockType.PARAGRAPH, text="Use TERM here.")],
            text="Use TERM here.",
        )
        state = TranslationState(
            llm=mock_llm,
            chunks=[chunk],
            current_chunk_idx=0,
            glossary={"TERM": "ТЕРМИН"},
            context_summary="",
            source_lang="en",
            target_lang="ru",
        )
        await translate_chunk_node(state)

    call_args = mock_chain.ainvoke.call_args
    assert call_args is not None
    messages = call_args[0][0]
    assert "TERM" in str(messages) or "ТЕРМИН" in str(messages)


async def test_translate_chunk_uses_context(mock_llm: MagicMock) -> None:
    """context_summary text appears in the call arguments."""
    from app.agents.translation_agent import TranslationAgent, TranslateOutput

    ctx = "Previous summary: key terms and names."
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=TranslateOutput(translated_text="TRANSLATED"))
    orig_init = TranslationAgent.__init__

    def patched_init(self: object, llm: object) -> None:
        orig_init(self, llm)
        setattr(self, "_chain", mock_chain)

    with patch.object(TranslationAgent, "__init__", patched_init):
        chunk = Chunk(
            index=0,
            blocks=[Block(type=BlockType.PARAGRAPH, text="New chunk.")],
            text="New chunk.",
        )
        state = TranslationState(
            llm=mock_llm,
            chunks=[chunk],
            current_chunk_idx=0,
            glossary={},
            context_summary=ctx,
            source_lang="en",
            target_lang="ru",
        )
        await translate_chunk_node(state)

    call_args = mock_chain.ainvoke.call_args
    assert call_args is not None
    messages = call_args[0][0]
    assert ctx in str(messages)


async def test_translate_chunk_appends_result(mock_llm: MagicMock) -> None:
    """translated_chunks grows by 1."""
    from app.agents.translation_agent import TranslationAgent, TranslateOutput

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(
        return_value=TranslateOutput(translated_text="TRANSLATED")
    )
    orig_init = TranslationAgent.__init__

    def patched_init(self: object, llm: object) -> None:
        orig_init(self, llm)
        setattr(self, "_chain", mock_chain)

    with patch.object(TranslationAgent, "__init__", patched_init):
        chunk = Chunk(
            index=0,
            blocks=[Block(type=BlockType.PARAGRAPH, text="Hello.")],
            text="Hello.",
        )
        state = TranslationState(
            llm=mock_llm,
            chunks=[chunk],
            current_chunk_idx=0,
            glossary={},
            context_summary="",
            source_lang="en",
            target_lang="ru",
        )
        result = await translate_chunk_node(state)
    assert len(result["translated_chunks"]) == 1
    assert result["translated_chunks"][0] == "TRANSLATED"


async def test_update_context_node(mock_llm: MagicMock) -> None:
    """context_summary in returned dict is non-empty string."""
    from app.agents.context_agent import ContextOutput, ContextSummaryAgent

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(
        return_value=ContextOutput(summary="Key terms: X, Y. Brief summary.")
    )
    orig_init = ContextSummaryAgent.__init__

    def patched_init(self: object, llm: object) -> None:
        orig_init(self, llm)
        setattr(self, "_chain", mock_chain)

    with patch.object(ContextSummaryAgent, "__init__", patched_init):
        # Chunk must be >= 300 tokens to trigger context update
        large_chunk = " ".join(["word"] * 250)
        state = TranslationState(
            llm=mock_llm,
            translated_chunks=[large_chunk],
        )
        result = await update_context_node(state)
    assert "context_summary" in result
    assert result["context_summary"]
    assert "Key terms" in result["context_summary"]


async def test_update_context_node_timeout_returns_empty(
    mock_llm: MagicMock,
) -> None:
    """Timeout in context update should not block pipeline."""
    large_chunk = " ".join(["word"] * 250)
    state = TranslationState(
        llm=mock_llm,
        translated_chunks=[large_chunk],
    )

    with patch(
        "app.agent.nodes.update_context._MAX_RETRIES",
        1,
    ), patch.object(
        ContextSummaryAgent,
        "arun",
        AsyncMock(side_effect=TimeoutError()),
    ):
        result = await update_context_node(state)

    assert result == {"context_summary": ""}


async def test_load_glossary_node(db_session: AsyncSession) -> None:
    """Loads user's glossary from DB."""
    from app.models import Glossary

    user = User(id=uuid.uuid4(), email="glossary@test.com")
    db_session.add(user)
    await db_session.commit()

    g = Glossary(
        user_id=user.id,
        source_term="API",
        target_term="API",
    )
    db_session.add(g)
    await db_session.commit()

    state = TranslationState(user_id=str(user.id))
    result = await load_glossary_node(state)
    assert result["glossary"] == {"API": "API"}
