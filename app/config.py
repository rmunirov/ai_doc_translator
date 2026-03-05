"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings read from .env file or environment variables."""

    # LLM
    llm_provider: Literal["gigachat", "lm_studio"] = "gigachat"
    gigachat_api_key: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-Pro"
    lm_studio_base_url: str = "http://localhost:1234/v1"
    lm_studio_model: str = "local"
    llm_request_timeout: int = 600  # seconds for HTTP requests to LLM API

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_doc_translator"

    # Files
    upload_dir: str = "./uploads"
    result_dir: str = "./results"
    max_file_size_mb: int = 50

    # Queue
    queue_workers: int = 2

    # Chunking (document_chunker)
    chunk_max_tokens_pdf: int = 1200
    chunk_min_tokens: int = 400

    # Context update (update_context_node) — skip LLM if chunk < N tokens
    context_update_min_tokens: int = 300
    context_update_timeout_sec: int = 90
    context_update_max_chars: int = 12000

    # Job progress (update_job_progress_node) — DB write every N chunks
    progress_update_batch_size: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
