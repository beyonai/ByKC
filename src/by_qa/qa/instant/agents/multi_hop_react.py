"""Multi-hop agent assembly for the instant-search capability."""

import json
from typing import Annotated, Any, List

from langchain.agents import create_agent
from langchain.tools import InjectedToolCallId, tool
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
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext
from by_qa.qa.instant.runtime.dispatcher import DispatcherToolMiddleware
from by_qa.qa.instant.runtime.operation_registry import (
    OPERATION_REGISTRY,
    OperationType,
)
from by_qa.qa.instant.runtime.tool_call_guard import ToolCallGuardMiddleware
from by_qa.qa.instant.state import MultiHopState
from by_qa.qa.services.checkpointer_factory import create_checkpointer_async
from by_qa.qa.services.llm_service import LLMService


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
            if (
                isinstance(msg, ToolMessage)
                and msg.name
                == OPERATION_REGISTRY[OperationType.KNOWLEDGE_SEARCH].tool_name
            ):
                delete_messages.append(RemoveMessage(id=msg.id))
            elif isinstance(msg, AIMessage) and msg.tool_calls:
                if any(
                    tc.get("name")
                    == OPERATION_REGISTRY[OperationType.KNOWLEDGE_SEARCH].tool_name
                    for tc in msg.tool_calls
                ):
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


DEFAULT_MULTI_HOP_SYSTEM_PROMPT = """你是一个智能的多跳问题求解助手。

你的任务是通过多步推理来回答复杂问题。

【工作流程】
1. 分析当前需要回答的问题
2. 调用 search_knowledge 获取信息（可以多次调用直到收集到足够信息）
3. 当确定需要进入下一个推理步骤时，调用 next_hop（会推进步骤计数器）
4. 重复步骤2-3直到能够给出最终答案
5. 调用 finalize 给出完整答案并结束流程

【工具说明】
- search_knowledge: 检索信息，返回的结果包含 index_id（如 s0-1, s1-2）可用于引用
- next_hop: 完成当前步骤并进入下一跳。调用后新检索的结果会标记为下一跳
- finalize: 结束整个流程，给出最终答案

【重要规则】
- next_hop 会推进步骤计数器，只有当你确定要进入下一个推理步骤时才调用
- 在同一跳内可以多次调用 search_knowledge 来收集信息
- 调用 next_hop 或 finalize 时，在 source_indices 中列出引用的文档ID
- 始终保持推理的连贯性和逻辑性"""


async def build_multi_hop_agent_graph(
    *,
    system_prompt: str | None = None,
    extra_tools: List[Any] | None = None,
    extra_middleware: List[Any] | None = None,
    llm_service: LLMService,
    checkpointer: Any | None = None,
):
    """Build the configurable multi-hop agent graph."""
    llm = await llm_service._get_streaming_model("retrieval")
    if checkpointer is None:
        checkpointer = await create_checkpointer_async(get_settings())
    tools = [next_hop, finalize] + list(extra_tools or [])
    middleware = [
        ToolCallGuardMiddleware(),
        DispatcherToolMiddleware(
            index_id_fn=lambda sub_query_idx, step, item_id: (
                f"{sub_query_idx}-{step}-{item_id}"
            ),
            follow_up_prompt="已经完成了一次检索，如果本次检索没有收集到足够信息，请继续调用 search_knowledge 来收集信息。否则立即调用 next_hop 进行上下文清理并进入下一个查询。如果所有检索都已完成，立即调用 finalize 来结束多跳检索并生成最终答案。",
        ),
    ] + list(extra_middleware or [])
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
    "build_multi_hop_agent_graph",
    "finalize",
    "next_hop",
]
