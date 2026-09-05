"""Abstract base class for QA engines."""

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from langchain_core.runnables import RunnableConfig

from by_qa.config import get_settings
from by_qa.core.logger import info, set_message_id, set_session_id
from by_qa.qa.common.config import QARetrievalConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.event_filter import EventFilter
from by_qa.qa.common.exceptions import ValidationError
from by_qa.qa.common.models import CoreInput, StreamEvent, StreamEventType
from by_qa.qa.services.checkpointer_factory import (
    close_checkpointer_async,
    create_checkpointer_async,
)
from by_qa.qa.services.llm_service import LLMService


@dataclass
class _SessionLockEntry:
    lock: asyncio.Lock
    users: int = 0


_SESSION_LOCKS: dict[str, _SessionLockEntry] = {}
_SESSION_LOCKS_GUARD = asyncio.Lock()


@asynccontextmanager
async def _session_run_lock(thread_id: str):
    """Serialize one checkpoint thread while allowing other threads to run."""
    async with _SESSION_LOCKS_GUARD:
        entry = _SESSION_LOCKS.setdefault(
            thread_id, _SessionLockEntry(lock=asyncio.Lock())
        )
        entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        async with _SESSION_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0:
                _SESSION_LOCKS.pop(thread_id, None)


class BaseQAEngine(ABC):
    """Shared lifecycle, config, and run-preparation logic for QA engines."""

    THREAD_ID_PREFIX: str
    _recursion_limit: int = 20

    def __init__(self, config: dict[str, Any] | None = None) -> None:
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
        self._checkpointer = await create_checkpointer_async(self._settings)
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
            callbacks=self._get_config_value("callbacks"),
            metadata={"session_id": session_id, "message_id": message_id},
            recursion_limit=recursion_limit,
            run_id=message_id,
        )
        config["configurable"] = {
            "thread_id": self._checkpoint_thread_id(session_id, message_id)
        }
        return session_id, message_id, config

    def _checkpoint_thread_id(self, session_id: str, message_id: str) -> str:
        """Return the checkpoint thread used by one engine invocation."""
        del message_id
        return f"{self.THREAD_ID_PREFIX}_{session_id}"

    def _serialize_checkpoint_thread(self) -> bool:
        """Whether invocations sharing a checkpoint thread must be serialized."""
        return True

    async def _get_graph(self):
        """Lazy-init: create checkpointer if needed, then build graph."""
        if self._graph is None:
            if self._checkpointer is None:
                self._checkpointer = await create_checkpointer_async(self._settings)
            self._graph = await self._build_graph()
        return self._graph

    @abstractmethod
    async def _build_graph(self) -> Any:
        """Build and return the compiled LangGraph using self._checkpointer."""

    @abstractmethod
    async def _do_stream_search(
        self,
        input_data: CoreInput,
        session_id: str,
        message_id: str,
        config: RunnableConfig,
        graph: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Subclass-specific streaming logic."""

    def _get_visible_roles(self) -> dict[str, list[str] | None] | None:
        """Return role whitelist for event filtering, or None to skip filtering."""
        return None

    async def stream_search(
        self, input_data: CoreInput
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute QA and stream user-visible events."""
        session_id, message_id, config = self._prepare_run(
            input_data, recursion_limit=self._recursion_limit
        )
        thread_id = config["configurable"]["thread_id"]
        if self._serialize_checkpoint_thread():
            async with _session_run_lock(thread_id):
                async for event in self._stream_with_config(
                    input_data, session_id, message_id, config
                ):
                    yield event
            return

        async for event in self._stream_with_config(
            input_data, session_id, message_id, config
        ):
            yield event

    async def _stream_with_config(
        self,
        input_data: CoreInput,
        session_id: str,
        message_id: str,
        config: RunnableConfig,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute and filter a prepared graph invocation."""
        graph = await self._get_graph()
        event_filter = EventFilter(self._get_visible_roles())
        async for event in self._do_stream_search(
            input_data, session_id, message_id, config, graph
        ):
            if event.type == StreamEventType.ERROR:
                yield event
                continue
            filtered = event_filter.filter_event(event)
            if filtered is not None:
                yield filtered


__all__ = ["BaseQAEngine"]
