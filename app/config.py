"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings read from .env file or environment variables."""

    # LLM
    llm_provider: Literal["gigachat", "ollama"] = "gigachat"
    gigachat_api_key: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-Pro"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_doc_translator"

    # Files
    upload_dir: str = "./uploads"
    result_dir: str = "./results"
    max_file_size_mb: int = 50

    # Queue
    queue_workers: int = 2

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
