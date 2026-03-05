"""Context summary agent — extracts key terms and summary from translated text."""

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)

CONTEXT_SYSTEM = """Из переведённого текста извлеки:
1. Ключевые термины и их перевод (если есть)
2. Имена собственные
3. Краткое содержание (1-3 предложения) для контекста следующего перевода

Ответ дай в виде структурированного резюме на том же языке, что и текст. 
Сжато, без лишних слов."""

CONTEXT_HUMAN = "{translated_text}"


class ContextInput(BaseModel):
    """Input for the context summary agent."""

    translated_text: str = ""


class ContextOutput(BaseModel):
    """Output of the context summary agent."""

    summary: str = Field(description="Key terms, names, and brief content summary")


class ContextSummaryAgent(BaseAgent[ContextInput, ContextOutput]):
    """Agent that summarizes translated text for context continuity."""

    SYSTEM_PROMPT = CONTEXT_SYSTEM

    def __init__(self, llm: BaseChatModel) -> None:
        """Initialize with LLM and structured output chain."""
        super().__init__(llm)
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", self.SYSTEM_PROMPT),
            ("human", CONTEXT_HUMAN),
        ])
        self._chain = self._prompt | self._llm.with_structured_output(
            ContextOutput
        )

    def run(self, data: ContextInput) -> ContextOutput:
        """Sync summary extraction."""
        try:
            result = self._chain.invoke(
                {"translated_text": data.translated_text}
            )
            assert isinstance(result, ContextOutput)
            return result
        except Exception as exc:
            logger.error(
                "ContextSummaryAgent.run failed",
                extra={"input_len": len(data.translated_text), "error": str(exc)},
            )
            raise

    async def arun(self, data: ContextInput) -> ContextOutput:
        """Async summary extraction."""
        try:
            result = await self._chain.ainvoke(
                {"translated_text": data.translated_text}
            )
            assert isinstance(result, ContextOutput)
            return result
        except Exception as exc:
            logger.error(
                "ContextSummaryAgent.arun failed",
                extra={"input_len": len(data.translated_text), "error": str(exc)},
            )
            raise
