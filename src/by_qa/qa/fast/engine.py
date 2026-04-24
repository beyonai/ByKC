"""Fast QA engine - linear rewrite, retrieval, and answer orchestration."""

import json
import traceback
import uuid
from dataclasses import asdict, fields, is_dataclass
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from by_qa.config import get_settings
from by_qa.core.logger import error, info, set_message_id, set_session_id
from by_qa.qa.common.config import QARetrievalConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.exceptions import ValidationError
from by_qa.qa.common.models import CoreInput, StreamEvent
from by_qa.qa.fast.config import FastQAConfig
from by_qa.qa.fast.graph import build_fast_qa_graph
from by_qa.qa.fast.state import FastQAState
from by_qa.qa.fast.types import NodeNames
from by_qa.qa.services.checkpointer_factory import (
    close_checkpointer_async,
    create_checkpointer_async,
)
from by_qa.qa.services.llm_service import LLMService


class FastQAEngine:
    """Fast QA engine backed by a linear LangGraph."""

    THREAD_ID_PREFIX = "fast_qa"

    def __init__(self, config: FastQAConfig | dict[str, Any] | None = None):
        self.config = config or {}
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
        """Release graph and checkpointer resources."""
        await close_checkpointer_async(self._checkpointer)
        self._checkpointer = None
        self._graph = None

    async def _get_graph(self):
        if self._graph is None:
            self._checkpointer = await create_checkpointer_async(self._settings)
            self._graph = await build_fast_qa_graph(checkpointer=self._checkpointer)
        return self._graph

    def _get_config_value(self, key: str, default=None):
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

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

    async def stream_search(
        self, input_data: CoreInput
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute fast QA and stream user-visible events."""
        if not input_data.query or not input_data.query.strip():
            raise ValidationError("Query cannot be empty")

        session_id = input_data.session_id or str(uuid.uuid4())
        message_id = input_data.message_id or str(uuid.uuid4())
        set_session_id(session_id)
        set_message_id(message_id)
        info(
            "[fast.stream_search] Input - query: %s",
            json.dumps(input_data.model_dump(), ensure_ascii=False),
        )

        role = None
        instance_id = None
        parent_ids = []
        try:
            initial_state = FastQAState(
                original_query=input_data.query,
                sub_queries=[],
                rewritten_query="",
                retrieval_results=[],
                final_answer="",
                messages=[HumanMessage(content=input_data.query)],
                rewrite_time=None,
                retrieval_time=None,
                answer_time=None,
            )
            graph = await self._get_graph()
            config = RunnableConfig(
                callbacks=[],
                metadata={"session_id": session_id, "message_id": message_id},
                recursion_limit=20,
                run_id=message_id,
            )
            config["configurable"] = {
                "thread_id": f"{self.THREAD_ID_PREFIX}_{session_id}"
            }

            async for event in graph.astream_events(
                initial_state,
                config=config,
                context=self._get_runtime_context(),
                version="v2",
                subgraphs=True,
                stream_mode=["custom", "messages", "updates"],
            ):
                if event.get("name") == "LangGraph":
                    continue
                event_type = event.get("event", "unknown")
                role = (
                    event.get("metadata", {}).get("langgraph_node", "unknown")
                    if event.get("name") == "ChatOpenAI"
                    else event.get("name", "unknown")
                )
                instance_id = event.get("run_id")
                parent_ids = event.get("parent_ids", [])

                if event_type == "on_chain_start" and role in {
                    NodeNames.REWRITE.value,
                    NodeNames.RETRIEVE.value,
                    NodeNames.ANSWER.value,
                }:
                    yield StreamEvent.node_start(
                        role=self._visible_role(role),
                        instance_id=instance_id,
                        parent_ids=parent_ids,
                    )
                    continue

                if event_type == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if chunk.content:
                        yield StreamEvent.token(
                            content=chunk.content,
                            role=role,
                            instance_id=instance_id,
                            parent_ids=parent_ids,
                        )
                    continue

                if event_type != "on_chain_end":
                    continue

                result = event["data"].get("output", {})
                if role == NodeNames.RETRIEVE.value:
                    yield StreamEvent.search_result_chunks(
                        chunks=result.get("retrieval_results", []),
                        role="knowledge_search",
                        instance_id=instance_id,
                        parent_ids=parent_ids,
                    )
                elif role == NodeNames.ANSWER.value:
                    yield StreamEvent.answer(
                        content=result.get("final_answer", ""),
                        role=NodeNames.ANSWER.value,
                        instance_id=instance_id,
                        parent_ids=parent_ids,
                    )
                if role in {
                    NodeNames.REWRITE.value,
                    NodeNames.RETRIEVE.value,
                    NodeNames.ANSWER.value,
                }:
                    node_end_kwargs: dict[str, Any] = {}
                    if role == NodeNames.REWRITE.value:
                        node_end_kwargs["subQueries"] = result.get("sub_queries", [])
                    yield StreamEvent.node_end(
                        role=self._visible_role(role),
                        instance_id=instance_id,
                        parent_ids=parent_ids,
                        **node_end_kwargs,
                    )

            yield StreamEvent.done(session_id=session_id, role="fast_qa")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            error(
                "[fast.stream_search] Error occurred - error: %s",
                traceback.format_exc(),
            )
            yield StreamEvent.error(
                error=str(exc),
                error_type=type(exc).__name__,
                role=role,
                instance_id=instance_id,
                parent_ids=parent_ids,
            )

    def _visible_role(self, role: str) -> str:
        if role == NodeNames.RETRIEVE.value:
            return "knowledge_search"
        return role


def create_fast_qa_engine(
    config: FastQAConfig | dict[str, Any] | None = None,
) -> FastQAEngine:
    """Create a fast QA engine."""
    if config is None:
        normalized_config: dict[str, Any] | FastQAConfig = {}
    elif is_dataclass(config):
        non_dc: dict[str, Any] = {}
        for f in fields(config):
            val = getattr(config, f.name)
            if (
                val is not None
                and not is_dataclass(val)
                and not isinstance(val, (dict, list, tuple, str, int, float, bool))
            ):
                non_dc[f.name] = val
        normalized_config = asdict(config)
        normalized_config.update(non_dc)
    else:
        normalized_config = dict(config)
    return FastQAEngine(config=normalized_config)


__all__ = ["FastQAEngine", "create_fast_qa_engine"]
