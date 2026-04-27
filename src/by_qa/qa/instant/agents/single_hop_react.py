"""Single-hop agent assembly for the instant-search capability."""

from typing import Any, List

from langchain.agents import create_agent

from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.instant.runtime.tool_call_guard import ToolCallGuardMiddleware
from by_qa.qa.instant.state import SingleHopState
from by_qa.qa.services.llm_service import LLMService
from by_qa.qa.tools.knowledge_tools import DispatcherToolMiddleware

DEFAULT_SINGLE_HOP_SYSTEM_PROMPT = """你是一个智能的单跳检索问答助手。

你的任务是回答一个单跳问题。这里的"单跳"表示不需要多步依赖推理，但并不代表一次检索就一定足够。

【工作流程】
1. 先分析当前问题还缺少哪些信息
2. 调用 search_knowledge 收集证据，可以多次调用
3. 当你认为证据已经足够时，直接基于现有证据输出最终答案

【工具说明】
- search_knowledge: 执行检索，返回带 index_id 的证据摘要

【重要规则】
- 如果证据不足，请继续检索，不要猜测
- 生成最终答案时请尽量在回答中显式引用你使用到的证据编号
- 最终答案应直接、完整、忠于检索证据
"""


async def build_single_hop_agent_graph(
    *,
    system_prompt: str | None = None,
    extra_tools: List[Any] | None = None,
    extra_middleware: List[Any] | None = None,
    llm_service: LLMService,
    checkpointer: Any | None = None,
):
    """Build the configurable single-hop agent graph."""
    llm = await llm_service._get_streaming_model("retrieval")
    tools = list(extra_tools or [])
    middleware = [
        ToolCallGuardMiddleware(),
        DispatcherToolMiddleware(
            index_id_fn=lambda sub_query_idx, step, item_id: (
                f"{sub_query_idx}-{step}-{item_id}"
            ),
            follow_up_prompt="如果当前证据仍不足以回答问题，请继续调用 search_knowledge 收集更多信息；如果已经足够回答，请直接基于已有证据输出最终答案，不要再调用工具。",
        ),
    ] + list(extra_middleware or [])
    return create_agent(
        model=llm,
        tools=tools,
        middleware=middleware,
        state_schema=SingleHopState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=system_prompt or DEFAULT_SINGLE_HOP_SYSTEM_PROMPT,
    )


__all__ = [
    "DEFAULT_SINGLE_HOP_SYSTEM_PROMPT",
    "build_single_hop_agent_graph",
]
