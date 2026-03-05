"""Translation agent — translates a text chunk preserving structure and glossary terms."""

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)

TRANSLATE_SYSTEM = """Ты профессиональный переводчик с {source_lang} на {target_lang}.

Глоссарий (используй точно, не изменяй):
{glossary_formatted}

Контекст предыдущих переводов:
{context_summary}

Правила перевода:
- Сохраняй структуру: число абзацев и их границы. Между абзацами — двойной
  перевод строки. Не объединяй абзацы.
- Переводи весь текст полностью — не оставляй непереведённые английские слова,
  не смешивай латиницу и кириллицу в одном слове (напр. conversational →
  разговорный, а не Конversational).
- Имена собственные транслитерируй корректно (Xinchi → Синьци, Maggie → Мэгги).
- Используй естественные русские обороты; избегай дословных калькирований
  (напр. "coding by feel" → "писали код интуитивно" или "по наитию", а не
  "кодили на ощущении").
- Переводи только текст, без комментариев."""

TRANSLATE_HUMAN = "{chunk_text}"


class TranslateInput(BaseModel):
    """Input for the translation agent."""

    text: str
    source_lang: str = "en"
    target_lang: str
    glossary: dict[str, str] = Field(default_factory=dict)
    context_summary: str = ""


class TranslateOutput(BaseModel):
    """Output of the translation agent."""

    translated_text: str


class TranslationAgent(BaseAgent[TranslateInput, TranslateOutput]):
    """Agent that translates a text chunk using glossary and context."""

    SYSTEM_PROMPT = TRANSLATE_SYSTEM

    def __init__(self, llm: BaseChatModel) -> None:
        """Initialize with LLM and structured output chain."""
        super().__init__(llm)
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", self.SYSTEM_PROMPT),
            ("human", TRANSLATE_HUMAN),
        ])
        self._chain = self._prompt | self._llm.with_structured_output(
            TranslateOutput
        )

    def _format_glossary(self, glossary: dict[str, str]) -> str:
        """Format glossary dict as 'term -> translation' lines."""
        if not glossary:
            return "(пусто)"
        return "\n".join(f"{k} -> {v}" for k, v in glossary.items())

    def _build_messages(self, data: TranslateInput) -> dict[str, Any]:
        """Build message variables for the chain."""
        return {
            "source_lang": data.source_lang,
            "target_lang": data.target_lang,
            "glossary_formatted": self._format_glossary(data.glossary),
            "context_summary": data.context_summary or "(нет)",
            "chunk_text": data.text,
        }

    def run(self, data: TranslateInput) -> TranslateOutput:
        """Sync translation of a text chunk."""
        try:
            result = self._chain.invoke(self._build_messages(data))
            assert isinstance(result, TranslateOutput)
            return result
        except Exception as exc:
            logger.error(
                "TranslationAgent.run failed",
                extra={"input_len": len(data.text), "error": str(exc)},
            )
            raise

    async def arun(self, data: TranslateInput) -> TranslateOutput:
        """Async translation of a text chunk."""
        try:
            result = await self._chain.ainvoke(self._build_messages(data))
            assert isinstance(result, TranslateOutput)
            return result
        except Exception as exc:
            logger.exception(
                "TranslationAgent.arun failed: %s (input_len=%d)",
                exc,
                len(data.text),
            )
            raise
