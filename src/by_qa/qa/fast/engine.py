"""Fast QA engine - linear rewrite, retrieval, and answer orchestration."""

import traceback
from dataclasses import asdict, fields, is_dataclass
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from by_qa.core.logger import error
from by_qa.qa.common.base_engine import BaseQAEngine
from by_qa.qa.common.models import CoreInput, StreamEvent
from by_qa.qa.fast.config import FastQAConfig
from by_qa.qa.fast.graph import build_fast_qa_graph
from by_qa.qa.fast.state import FastQAState
from by_qa.qa.fast.types import NodeNames


class FastQAEngine(BaseQAEngine):
    """Fast QA engine backed by a linear LangGraph."""

    THREAD_ID_PREFIX = "fast_qa"
    _recursion_limit = 20

    async def _build_graph(self):
        return await build_fast_qa_graph(checkpointer=self._checkpointer)

    async def _do_stream_search(
        self,
        input_data: CoreInput,
        session_id: str,
        message_id: str,
        config: RunnableConfig,
        graph: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Fast QA streaming logic."""
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
