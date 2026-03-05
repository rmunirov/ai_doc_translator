"""Abstract base class for all LLM agents."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class BaseAgent(ABC, Generic[InputT, OutputT]):
    """Base class for all AI agents in the project.

    Each agent has a system prompt, typed Input/Output models, and supports
    both sync (run) and async (arun) invocation.
    """

    SYSTEM_PROMPT: str  # must be defined as class constant in every subclass

    def __init__(self, llm: BaseChatModel) -> None:
        """Initialize agent with an LLM instance."""
        self._llm = llm
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", self.SYSTEM_PROMPT),
            ("human", "{input}"),
        ])

    @abstractmethod
    def run(self, data: InputT) -> OutputT:
        """Synchronous invocation. Use in CLI, scripts, sync tests."""

    @abstractmethod
    async def arun(self, data: InputT) -> OutputT:
        """Asynchronous invocation. Use in FastAPI handlers and pipelines."""
