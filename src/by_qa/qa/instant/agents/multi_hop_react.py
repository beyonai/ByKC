"""Multi-hop agent assembly for the instant-search capability."""

import json
from typing import Annotated, Any, Dict, List

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from by_qa.config import get_settings
from by_qa.core.logger import info
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext
from by_qa.qa.instant.runtime.retrieval import search_knowledge_items
from by_qa.qa.instant.state import MultiHopState
from by_qa.qa.services.checkpointer_factory import create_checkpointer_async
from by_qa.qa.services.llm_service import get_llm_service


def _build_indexed_results(
    raw_results: List[Dict[str, Any]], current_step: int, counter: int
) -> List[Dict[str, Any]]:
    results = []
    for i, result in enumerate(raw_results):
        indexed_result = dict(result)
        indexed_result["index_id"] = f"s{current_step}-{counter + i + 1}"
        indexed_result["step"] = current_step
        results.append(indexed_result)
    return results


def _build_llm_results(results: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    return [
        {
            "index_id": result.get("index_id", ""),
            "content": result.get("content", ""),
        }
        for result in results
    ]


@tool
async def parallel_retrieval(
    query: str,
    runtime: ToolRuntime[InstantSearchRuntimeContext],
) -> Command:
    """执行检索获取信息。"""
    state = runtime.state
    current_step = state.get("current_step", 0)
    counter = state.get("result_counter", 0)

    raw_results = await search_knowledge_items(query, runtime.context)

    results = _build_indexed_results(raw_results, current_step, counter)
    llm_results = _build_llm_results(results)

    info(f"[multi_hop] Retrieved {len(results)} docs for step {current_step}")

    return Command(
        update={
            "all_retrieval_results": results,
            "result_counter": counter + len(results),
            "messages": [
                ToolMessage(
                    content=json.dumps(llm_results, ensure_ascii=False),
                    artifact=raw_results,
                    name="parallel_retrieval",
                    tool_call_id=runtime.tool_call_id,
                ),
                SystemMessage(
                    content="已经完成了一次检索，如果本次检索没有收集到足够信息，请继续调用 parallel_retrieval 来收集信息。否则立即调用 next_hop 进行上下文清理并进入下一个查询。如果所有检索都已完成，立即调用 finalize 来结束多跳检索并生成最终答案。"
                ),
            ],
        }
    )


@tool
def next_hop(
    current_query: str,
    current_answer: str,
    next_query: str,
    source_indices: List[str],
    state: Annotated[MultiHopState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """完成当前步骤并进入下一跳查询。"""
    messages = state.get("messages", [])
    current_step = state.get("current_step", 0)
    new_step = current_step + 1

    delete_messages = []
    last_human_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            last_human_idx = i
            break

    if last_human_idx != -1:
        for msg in messages[last_human_idx + 1 :]:
            if not msg.id:
                continue
            if isinstance(msg, ToolMessage) and msg.name == "parallel_retrieval":
                delete_messages.append(RemoveMessage(id=msg.id))
            elif isinstance(msg, AIMessage) and msg.tool_calls:
                if any(tc.get("name") == "parallel_retrieval" for tc in msg.tool_calls):
                    delete_messages.append(RemoveMessage(id=msg.id))
            elif isinstance(msg, SystemMessage):
                delete_messages.append(RemoveMessage(id=msg.id))

    new_result = {
        "step": current_step + 1,
        "answer": current_answer,
        "query": current_query,
        "source_indices": source_indices,
    }

    return Command(
        update={
            "result_counter": 0,
            "current_step": new_step,
            "intermediate_results": [new_result],
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        {
                            "message": f"第{current_step + 1}跳完成，检索结果为：{current_answer}。检索上下文已清理。",
                            "next_query": next_query,
                        },
                        ensure_ascii=False,
                    ),
                    name="next_hop",
                    tool_call_id=tool_call_id,
                )
            ]
            + delete_messages,
        }
    )


@tool(return_direct=True)
def finalize(
    current_query: str,
    current_answer: str,
    source_indices: List[str],
    state: Annotated[MultiHopState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """完成多跳检索并跳转到总结节点。"""
    current_step = state.get("current_step", 0)
    info(
        f"[multi_hop] Finalize called at step {current_step + 1}, jumping to summary node"
    )

    new_result = {
        "step": current_step + 1,
        "answer": current_answer,
        "query": current_query,
        "source_indices": source_indices,
        "is_final": True,
    }

    return Command(
        update={
            "intermediate_results": [new_result],
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        {
                            "message": f"第{current_step + 1}跳完成，多跳检索结束，准备生成最终答案。",
                            "current_answer": current_answer,
                        },
                        ensure_ascii=False,
                    ),
                    name="finalize",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


class MultiHopMiddleware(AgentMiddleware):
    """Lightweight middleware seam for the multi-hop agent runtime."""

    def __init__(self, settings):
        self.settings = settings

    async def abefore_model(self, state_unused, runtime_unused):
        del state_unused
        del runtime_unused
        return None


DEFAULT_MULTI_HOP_SYSTEM_PROMPT = """你是一个智能的多跳问题求解助手。

你的任务是通过多步推理来回答复杂问题。

【工作流程】
1. 分析当前需要回答的问题
2. 调用 parallel_retrieval 获取信息（可以多次调用直到收集到足够信息）
3. 当确定需要进入下一个推理步骤时，调用 next_hop（会推进步骤计数器）
4. 重复步骤2-3直到能够给出最终答案
5. 调用 finalize 给出完整答案并结束流程

【工具说明】
- parallel_retrieval: 检索信息，返回的结果包含 index_id（如 s0-1, s1-2）可用于引用
- next_hop: 完成当前步骤并进入下一跳。调用后新检索的结果会标记为下一跳
- finalize: 结束整个流程，给出最终答案

【重要规则】
- next_hop 会推进步骤计数器，只有当你确定要进入下一个推理步骤时才调用
- 在同一跳内可以多次调用 parallel_retrieval 来收集信息
- 调用 next_hop 或 finalize 时，在 source_indices 中列出引用的文档ID
- 始终保持推理的连贯性和逻辑性"""


async def build_multi_hop_agent_graph(
    *,
    system_prompt: str | None = None,
    extra_tools: List[Any] | None = None,
    extra_middleware: List[Any] | None = None,
    model: Any | None = None,
    llm_factory: Any | None = None,
):
    """Build the configurable multi-hop agent graph."""
    settings = get_settings()
    llm_service = get_llm_service()
    llm = model
    if llm is None and llm_factory is not None:
        llm = llm_factory("retrieval")
    if llm is None:
        llm = llm_service._get_streaming_model("retrieval")
    checkpointer = await create_checkpointer_async(settings)
    tools = [parallel_retrieval, next_hop, finalize] + (extra_tools or [])
    middleware = [MultiHopMiddleware(settings)] + (extra_middleware or [])
    return create_agent(
        model=llm,
        tools=tools,
        middleware=middleware,
        state_schema=MultiHopState,
        context_schema=InstantSearchRuntimeContext,
        checkpointer=checkpointer,
        system_prompt=system_prompt or DEFAULT_MULTI_HOP_SYSTEM_PROMPT,
    )


__all__ = [
    "DEFAULT_MULTI_HOP_SYSTEM_PROMPT",
    "MultiHopMiddleware",
    "build_multi_hop_agent_graph",
    "finalize",
    "next_hop",
    "parallel_retrieval",
]
