"""Single-hop agent assembly for the instant-search capability."""

import json
from typing import Any, Dict, List

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import Command

from by_qa.config import get_settings
from by_qa.core.logger import info
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext
from by_qa.qa.instant.runtime.retrieval import search_knowledge_items
from by_qa.qa.instant.state import SingleHopState
from by_qa.qa.services.checkpointer_factory import create_checkpointer_async
from by_qa.qa.services.llm_service import LLMService


def _build_indexed_results(
    raw_results: List[Dict[str, Any]], counter: int
) -> List[Dict[str, Any]]:
    results = []
    for i, result in enumerate(raw_results):
        indexed_result = dict(result)
        indexed_result["index_id"] = f"r{counter + i + 1}"
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
    counter = state.get("result_counter", 0)

    raw_results = await search_knowledge_items(query, runtime.context)
    results = _build_indexed_results(raw_results, counter)
    llm_results = _build_llm_results(results)

    info(f"[single_hop] Retrieved {len(results)} docs")

    return Command(
        update={
            "retrieval_results": results,
            "result_counter": counter + len(results),
            "messages": [
                ToolMessage(
                    content=json.dumps(llm_results, ensure_ascii=False),
                    artifact=raw_results,
                    name="parallel_retrieval",
                    tool_call_id=runtime.tool_call_id,
                ),
                SystemMessage(
                    content="如果当前证据仍不足以回答问题，请继续调用 parallel_retrieval 收集更多信息；如果已经足够回答，请直接基于已有证据输出最终答案，不要再调用工具。"
                ),
            ],
        }
    )


class SingleHopMiddleware(AgentMiddleware):
    """Middleware seam for single-hop agent runtime extensions."""

    def __init__(self, settings):
        self.settings = settings

    async def abefore_model(self, state_unused, runtime_unused=None):
        del state_unused
        del runtime_unused
        return None

    async def awrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
        model_settings = dict(request.model_settings)
        model_settings["parallel_tool_calls"] = False
        return await handler(request.override(model_settings=model_settings))


DEFAULT_SINGLE_HOP_SYSTEM_PROMPT = """你是一个智能的单跳检索问答助手。

你的任务是回答一个单跳问题。这里的“单跳”表示不需要多步依赖推理，但并不代表一次检索就一定足够。

【工作流程】
1. 先分析当前问题还缺少哪些信息
2. 调用 parallel_retrieval 收集证据，可以多次调用
3. 当你认为证据已经足够时，直接基于现有证据输出最终答案

【工具说明】
- parallel_retrieval: 执行检索，返回带 index_id 的证据摘要

【重要规则】
- 如果证据不足，请继续检索，不要猜测
- 生成最终答案时请尽量在回答中显式引用你使用到的证据编号
- 最终答案应直接、完整、忠于检索证据"""


async def build_single_hop_agent_graph(
    *,
    system_prompt: str | None = None,
    extra_tools: List[Any] | None = None,
    extra_middleware: List[Any] | None = None,
    llm_service: LLMService,
):
    """Build the configurable single-hop agent graph."""
    settings = get_settings()
    llm = await llm_service._get_streaming_model("retrieval")
    checkpointer = await create_checkpointer_async(settings)
    tools = [parallel_retrieval] + (extra_tools or [])
    middleware = [SingleHopMiddleware(settings)] + (extra_middleware or [])
    return create_agent(
        model=llm,
        tools=tools,
        middleware=middleware,
        state_schema=SingleHopState,
        context_schema=InstantSearchRuntimeContext,
        checkpointer=checkpointer,
        system_prompt=system_prompt or DEFAULT_SINGLE_HOP_SYSTEM_PROMPT,
    )


__all__ = [
    "DEFAULT_SINGLE_HOP_SYSTEM_PROMPT",
    "SingleHopMiddleware",
    "build_single_hop_agent_graph",
    "parallel_retrieval",
]
