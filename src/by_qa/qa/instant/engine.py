"""Instant QA engine - streaming retrieval and answer orchestration."""

import traceback
from dataclasses import asdict, fields, is_dataclass
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from by_qa.core.logger import error, info
from by_qa.qa.common.base_engine import BaseQAEngine
from by_qa.qa.common.config import QAEngineConfig
from by_qa.qa.common.models import CoreInput, StreamEvent, StreamEventType
from by_qa.qa.common.operation_registry import OPERATION_REGISTRY, OperationType
from by_qa.qa.instant.graphs.main import NodeNames, build_instant_search_graph
from by_qa.qa.instant.state import InstantSearchState
from by_qa.qa.services.checkpointer_factory import create_checkpointer_async

USER_VISIBLE_ROLES: dict[str, list[str] | None] = {
    NodeNames.DECOMPOSER.value: None,
    NodeNames.CONTEXT_MANAGER.value: None,
    NodeNames.SINGLE_HOP_WORKER.value: None,
    NodeNames.SINGLE_HOP_AGENT.value: None,
    NodeNames.SINGLE_HOP_SUMMARY.value: None,
    NodeNames.MULTI_HOP_WORKER.value: None,
    NodeNames.SUBANSWER_AGGREGATOR.value: None,
    NodeNames.FINAL_ANSWER.value: None,
    OPERATION_REGISTRY[OperationType.KNOWLEDGE_SEARCH].tool_name: None,
    NodeNames.MULTI_HOP_AGENT.value: None,
    NodeNames.MULTI_HOP_SUMMARY.value: None,
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


class EventFilter:
    """Event filter that tracks visible instance ids and cleans parent ids."""

    def __init__(self, visible_roles: dict[str, list[str] | None]):
        self.visible_roles = visible_roles
        self._instance_role_map: dict[str, str] = {}
        self._visible_instance_ids: set[str] = set()

    def _is_event_visible(self, event: StreamEvent) -> bool:
        allowed_event_types = self.visible_roles.get(event.role)
        if event.role not in self.visible_roles:
            return False
        if allowed_event_types is None:
            return True
        return event.type.value in allowed_event_types

    def filter_event(self, event: StreamEvent) -> StreamEvent | None:
        role = event.role
        instance_id = event.instance_id
        if instance_id:
            self._instance_role_map[instance_id] = role or ""
        if not self._is_event_visible(event):
            return None
        if instance_id:
            self._visible_instance_ids.add(instance_id)
        if event.parent_ids:
            event.parent_ids = [
                parent_id
                for parent_id in event.parent_ids
                if parent_id in self._visible_instance_ids
            ]
        return event


class InstantQAEngine(BaseQAEngine):
    """Instant QA engine backed by capability-local agent graphs."""

    THREAD_ID_PREFIX = "instant_search"
    _recursion_limit = 50

    async def _get_graph(self):
        if self._graph is None:
            self._checkpointer = await create_checkpointer_async(self._settings)
            self._graph = await build_instant_search_graph(
                config=self.config, checkpointer=self._checkpointer
            )
        return self._graph

    async def _do_stream_search(
        self,
        input_data: CoreInput,
        session_id: str,
        message_id: str,
        config: RunnableConfig,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute instant QA and stream results incrementally."""
        try:
            initial_state = InstantSearchState(
                original_query=input_data.query,
                sub_queries=[],
                sub_answers={"mode": "RESET", "data": []},
                retrieval_results={"mode": "RESET", "data": []},
                final_answer="",
                citations=[],
                confidence=0.0,
                messages=[HumanMessage(content=input_data.query)],
                decomposition_time=None,
                retrieval_time=None,
                aggregation_time=None,
            )

            graph = await self._get_graph()
            event_filter = EventFilter(USER_VISIBLE_ROLES)

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
                                yield_event = StreamEvent.search_result_chunks(
                                    chunks=retrieval_results,
                                    role=OPERATION_REGISTRY[
                                        OperationType.KNOWLEDGE_SEARCH
                                    ].tool_name,
                                    instance_id=instance_id,
                                    parent_ids=parent_ids,
                                )
                                filtered_event = event_filter.filter_event(yield_event)
                                if filtered_event:
                                    yield filtered_event
                    elif role == NodeNames.FINAL_ANSWER.value:
                        final_answer = result.get("final_answer", "")
                        answer_event = StreamEvent.answer(
                            content=final_answer,
                            role=role,
                            instance_id=instance_id,
                            parent_ids=parent_ids,
                        )
                        filtered_answer_event = event_filter.filter_event(answer_event)
                        if filtered_answer_event:
                            yield filtered_answer_event
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
                    filtered_event = event_filter.filter_event(yield_event)
                    if filtered_event:
                        yield filtered_event
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


InstantSearchEngine = InstantQAEngine
InstantSearchAgent = InstantQAEngine


def create_instant_search_agent(
    config: QAEngineConfig | dict[str, Any] | None = None,
) -> InstantSearchAgent:
    """Create a feature-complete instant QA agent facade."""
    if config is None:
        normalized_config: dict[str, Any] = {}
    elif is_dataclass(config):
        # Extract non-dataclass fields before asdict to avoid unsafe deepcopy
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
    return InstantSearchAgent(config=normalized_config)
