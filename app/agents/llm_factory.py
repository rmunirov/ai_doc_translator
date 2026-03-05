"""LLM factory — creates GigaChat or ChatOpenAI (LM Studio) from config."""

from pydantic import SecretStr
from langchain_core.language_models import BaseChatModel

from app.config import get_settings


def get_llm() -> BaseChatModel:
    """Create LLM instance from settings. Use for graph execution."""
    settings = get_settings()
    if settings.llm_provider == "lm_studio":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            base_url=settings.lm_studio_base_url,
            model=settings.lm_studio_model,
            api_key=SecretStr("lm-studio"),  # LM Studio does not validate API keys locally
            request_timeout=settings.llm_request_timeout,
        )
    from langchain_gigachat import GigaChat
    return GigaChat(
        credentials=settings.gigachat_api_key,
        scope=settings.gigachat_scope,
        model=settings.gigachat_model,
        verify_ssl_certs=False,
        timeout=settings.llm_request_timeout,
    )
