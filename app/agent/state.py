"""Translation graph state — Pydantic model shared across all nodes."""

from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from app.models.schemas import Chunk, ParsedDocument


class TranslationState(BaseModel):
    """State for the translation LangGraph pipeline."""

    model_config = {"arbitrary_types_allowed": True}

    job_id: str = ""
    user_id: str = ""
    target_lang: str = ""
    source_lang: str = ""
    input_path: str = ""
    parsed_doc: ParsedDocument | None = None
    chunks: list[Chunk] = Field(default_factory=list)
    current_chunk_idx: int = 0
    translated_chunks: list[str] = Field(default_factory=list)
    context_summary: str = ""
    glossary: dict[str, str] = Field(default_factory=dict)
    result_path: str = ""
    assembly_warning: str = ""
    cancelled: bool = False
    error: str | None = None
    llm: BaseChatModel | None = None

    def get(self, key: str, default: Any = None) -> Any:
        """Get state field by key, like dict.get(). Used by LangGraph nodes."""
        val = getattr(self, key, default)
        return default if val is None and default is not None else val
