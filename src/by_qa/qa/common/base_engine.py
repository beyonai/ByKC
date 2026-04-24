"""Abstract base class for QA engines."""

import json
import uuid
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

from langchain_core.runnables import RunnableConfig

from by_qa.config import get_settings
from by_qa.core.logger import info, set_message_id, set_session_id
from by_qa.qa.common.config import QARetrievalConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.exceptions import ValidationError
from by_qa.qa.common.models import CoreInput, StreamEvent
from by_qa.qa.services.checkpointer_factory import close_checkpointer_async
from by_qa.qa.services.llm_service import LLMService


class BaseQAEngine(ABC):
    """Shared lifecycle, config, and run-preparation logic for QA engines."""

    THREAD_ID_PREFIX: str
    _recursion_limit: int = 20

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        # THREAD_ID_PREFIX is a class-level annotation; verify a concrete value exists
        if not isinstance(getattr(type(self), "THREAD_ID_PREFIX", None), str):
            raise TypeError(
                f"{type(self).__name__} must define THREAD_ID_PREFIX as a str"
            )
        self.config: dict[str, Any] = config or {}
        self._settings = get_settings()
        self._graph = None
        self._checkpointer = None
        self._runtime_context: QARuntimeContext | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    async def close(self) -> None:
        """Release checkpointer and graph resources."""
        await close_checkpointer_async(self._checkpointer)
        self._checkpointer = None
        self._graph = None

    def _get_config_value(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def _build_runtime_context(self) -> QARuntimeContext:
        retrieval_config = self._get_config_value("retrieval", {})
        if isinstance(retrieval_config, dict):
            retrieval_config = QARetrievalConfig(**retrieval_config)
        if not isinstance(retrieval_config, QARetrievalConfig):
            retrieval_config = QARetrievalConfig()
        llm_service = self._get_config_value("llm_service") or LLMService()
        return QARuntimeContext(retrieval=retrieval_config, llm_service=llm_service)

    def _get_runtime_context(self) -> QARuntimeContext:
        if self._runtime_context is None:
            self._runtime_context = self._build_runtime_context()
        return self._runtime_context

    def _prepare_run(
        self, input_data: CoreInput, *, recursion_limit: int = 20
    ) -> tuple[str, str, RunnableConfig]:
        """Validate input, set logging context, and build RunnableConfig."""
        if not input_data.query or not input_data.query.strip():
            raise ValidationError("Query cannot be empty")
        session_id = input_data.session_id or str(uuid.uuid4())
        message_id = input_data.message_id or str(uuid.uuid4())
        set_session_id(session_id)
        set_message_id(message_id)
        info(
            "[%s.stream_search] Input - query: %s",
            type(self).__name__,
            json.dumps(input_data.model_dump(), ensure_ascii=False),
        )
        config = RunnableConfig(
            callbacks=[],
            metadata={"session_id": session_id, "message_id": message_id},
            recursion_limit=recursion_limit,
            run_id=message_id,
        )
        config["configurable"] = {"thread_id": f"{self.THREAD_ID_PREFIX}_{session_id}"}
        return session_id, message_id, config

    @abstractmethod
    async def _get_graph(self):
        """Build and return the compiled LangGraph."""

    @abstractmethod
    async def _do_stream_search(
        self,
        input_data: CoreInput,
        session_id: str,
        message_id: str,
        config: RunnableConfig,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Subclass-specific streaming logic; called by stream_search."""

    async def stream_search(
        self, input_data: CoreInput
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute QA and stream user-visible events.

        Calls _prepare_run then delegates to _do_stream_search so subclasses
        can't forget to invoke _prepare_run.
        """
        session_id, message_id, config = self._prepare_run(
            input_data, recursion_limit=self._recursion_limit
        )
        async for event in self._do_stream_search(
            input_data, session_id, message_id, config
        ):
            yield event


__all__ = ["BaseQAEngine"]
