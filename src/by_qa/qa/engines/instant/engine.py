"""Instant QA engine - streaming retrieval and answer orchestration."""

import traceback
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from by_qa.core.logger import error, info
from by_qa.qa.agents.multi_hop_react import MultiHopNodeNames
from by_qa.qa.agents.single_hop_react import SingleHopNodeNames
from by_qa.qa.common.base_engine import BaseQAEngine
from by_qa.qa.common.models import CoreInput, StreamEvent, StreamEventType
from by_qa.qa.common.operation_registry import OPERATION_REGISTRY, OperationType
from by_qa.qa.engines.instant.graph import build_instant_search_graph
from by_qa.qa.engines.instant.nodes.state_cleanup import MAX_RETAINED_USER_MESSAGES
from by_qa.qa.engines.instant.state import InstantSearchState
from by_qa.qa.engines.instant.types import NodeNames
from by_qa.qa.services.checkpointer_factory import get_checkpointer_backend_name
from by_qa.qa.services.session_history import OpenGaussSessionHistoryStore

USER_VISIBLE_ROLES: dict[str, list[str] | None] = {
    NodeNames.DECOMPOSER.value: None,
    NodeNames.CONTEXT_MANAGER.value: None,
    NodeNames.SINGLE_HOP_WORKER.value: None,
    SingleHopNodeNames.AGENT.value: None,
    SingleHopNodeNames.SUMMARY.value: None,
    SingleHopNodeNames.RECURSION_FALLBACK.value: None,
    NodeNames.MULTI_HOP_WORKER.value: None,
    NodeNames.SUBANSWER_AGGREGATOR.value: None,
    NodeNames.FINAL_ANSWER.value: None,
    OPERATION_REGISTRY[OperationType.KNOWLEDGE_SEARCH].tool_name: None,
    MultiHopNodeNames.AGENT.value: None,
    MultiHopNodeNames.SUMMARY.value: None,
    "model": [StreamEventType.TOKEN.value],
}


def _extract_search_result_chunks(tool_message: Any) -> list[dict[str, Any]]:
    """Read streamed retrieval chunks from tool artifacts only."""
    retrieval_results = getattr(tool_message, "artifact", None)
    if retrieval_results is not None:
        return retrieval_results
    return []


def _extract_tool_message(result: Any) -> ToolMessage | None:
    """Extract the first ToolMessage from various on_chain_end output shapes.

    LangGraph tools can return a ToolMessage directly, a Command wrapping state
    updates, or a list containing either of those — depending on the execution
    path (ToolCallGuardMiddleware, dispatcher post-processing, etc.).
    """
    if isinstance(result, ToolMessage):
        return result
    if isinstance(result, Command):
        messages = result.update.get("messages", [])
        for msg in messages:
            if isinstance(msg, ToolMessage):
                return msg
    if isinstance(result, list):
        for item in result:
            msg = _extract_tool_message(item)
            if msg is not None:
                return msg
    return None


class InstantQAEngine(BaseQAEngine):
    """Instant QA engine backed by capability-local agent graphs."""

    THREAD_ID_PREFIX = "instant_search"
    _recursion_limit = 50

    def _uses_concurrent_session_history(self) -> bool:
        if self._get_config_value("session_history_store") is not None:
            return True
        backend = getattr(self._settings, "checkpointer_backend", "")
        return str(backend).lower() == "opengauss"

    def _checkpoint_thread_id(self, session_id: str, message_id: str) -> str:
        if self._uses_concurrent_session_history():
            return f"{self.THREAD_ID_PREFIX}_{session_id}_{message_id}"
        return super()._checkpoint_thread_id(session_id, message_id)

    def _serialize_checkpoint_thread(self) -> bool:
        return not self._uses_concurrent_session_history()

    def _get_session_history_store(self):
        override = self._get_config_value("session_history_store")
        if override is not None:
            return override
        if get_checkpointer_backend_name(self._checkpointer) == "opengauss":
            return OpenGaussSessionHistoryStore(self._checkpointer.conn)
        return None

    def _get_visible_roles(self) -> dict[str, list[str] | None] | None:
        return USER_VISIBLE_ROLES

    async def _build_graph(self):
        return await build_instant_search_graph(
            config=self.config, checkpointer=self._checkpointer
        )

    async def _do_stream_search(
        self,
        input_data: CoreInput,
        session_id: str,
        message_id: str,
        config: RunnableConfig,
        graph: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute instant QA and stream results incrementally."""
        history_store = self._get_session_history_store()
        turn_reserved = False
        succeeded = False
        role = None
        instance_id = None
        parent_ids = None
        try:
            previous_queries: list[str] = []
            if history_store is not None:
                reserved_turn = await history_store.reserve_turn(
                    engine=self.THREAD_ID_PREFIX,
                    session_id=session_id,
                    message_id=message_id,
                    query=input_data.query,
                    history_limit=MAX_RETAINED_USER_MESSAGES - 1,
                )
                previous_queries = reserved_turn.previous_queries
                turn_reserved = True

            initial_state = InstantSearchState(
                original_query=input_data.query,
                sub_queries=[],
                sub_answers={"mode": "RESET", "data": []},
                retrieval_results={"mode": "RESET", "data": []},
                final_answer="",
                citations=[],
                confidence=0.0,
                messages=[
                    *[HumanMessage(content=query) for query in previous_queries],
                    HumanMessage(content=input_data.query),
                ],
                decomposition_time=None,
                retrieval_time=None,
                aggregation_time=None,
            )

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
                yield_event = None
                event_type = event.get("event", "unknown")
                role = (
                    event.get("metadata", {}).get("langgraph_node", "unknown")
                    if event.get("name") == "ChatOpenAI"
                    else event.get("name", "unknown")
                )
                instance_id = event.get("run_id")
                parent_ids = event.get("parent_ids", [])

                if event_type == "on_chain_start":
                    kwargs = {}
                    if role in [
                        NodeNames.MULTI_HOP_WORKER.value,
                        NodeNames.SINGLE_HOP_WORKER.value,
                    ]:
                        kwargs["content"] = (
                            event["data"]
                            .get("input", {})
                            .get("sub_query", {})
                            .get("query_text", "")
                        )
                    elif (
                        role == "tools"
                        and event["data"]["input"]["tool_call"]["name"]
                        == OPERATION_REGISTRY[OperationType.KNOWLEDGE_SEARCH].tool_name
                    ):
                        role = OPERATION_REGISTRY[
                            OperationType.KNOWLEDGE_SEARCH
                        ].tool_name
                        kwargs["content"] = event["data"]["input"]["tool_call"]["args"][
                            "query"
                        ]
                    yield_event = StreamEvent.node_start(
                        role=role,
                        instance_id=instance_id,
                        parent_ids=parent_ids,
                        **kwargs,
                    )
                elif event_type == "on_chain_end":
                    result = event["data"].get("output", {})
                    if role == "tools":
                        tool_name = event["data"]["input"]["tool_call"]["name"]
                        if (
                            tool_name
                            == OPERATION_REGISTRY[
                                OperationType.KNOWLEDGE_SEARCH
                            ].tool_name
                        ):
                            tool_message = _extract_tool_message(result)
                            if tool_message is not None:
                                retrieval_results = _extract_search_result_chunks(
                                    tool_message
                                )
                                yield StreamEvent.search_result_chunks(
                                    chunks=retrieval_results,
                                    role=OPERATION_REGISTRY[
                                        OperationType.KNOWLEDGE_SEARCH
                                    ].tool_name,
                                    instance_id=instance_id,
                                    parent_ids=parent_ids,
                                )
                    elif role == NodeNames.FINAL_ANSWER.value:
                        final_answer = result.get("final_answer", "")
                        yield StreamEvent.answer(
                            content=final_answer,
                            role=role,
                            instance_id=instance_id,
                            parent_ids=parent_ids,
                        )
                    yield_event = StreamEvent.node_end(
                        role=role,
                        instance_id=instance_id,
                        parent_ids=parent_ids,
                    )
                elif event_type == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if chunk.content or chunk.usage_metadata:
                        yield_event = StreamEvent.token(
                            content=chunk.content,
                            role=role,
                            instance_id=instance_id,
                            parent_ids=parent_ids,
                            usage_metadata=chunk.usage_metadata
                            if chunk.usage_metadata
                            else None,
                        )
                if yield_event:
                    yield yield_event
            succeeded = True
            info("[stream_search] Completed successfully")
        except Exception as exc:
            error("[stream_search] Error occurred - error: %s", traceback.format_exc())
            yield StreamEvent.error(
                error=str(exc),
                error_type=type(exc).__name__,
                role=role,
                instance_id=instance_id,
                parent_ids=parent_ids,
            )
        finally:
            if turn_reserved:
                await history_store.finish_turn(
                    engine=self.THREAD_ID_PREFIX,
                    session_id=session_id,
                    message_id=message_id,
                    succeeded=succeeded,
                    retention_limit=MAX_RETAINED_USER_MESSAGES,
                )
