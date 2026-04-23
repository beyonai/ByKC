"""Multi-hop subgraph builder for the instant-search capability."""

from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

try:
    from langgraph.runtime import Runtime
except ImportError:
    Runtime = None  # type: ignore[assignment,misc]

from by_qa.config import get_settings
from by_qa.core.logger import error, info
from by_qa.qa.instant.agents.multi_hop_react import build_multi_hop_agent_graph
from by_qa.qa.instant.nodes.node_enum import NodeNames
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext
from by_qa.qa.instant.state import MultiHopState, SubAnswer
from by_qa.qa.services.checkpointer_factory import create_checkpointer_async
from by_qa.qa.services.llm_service import LLMService


def _normalize_to_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _extract_sources(retrieval_results: List[Dict]) -> List[Dict]:
    sources = []
    seen = set()
    for result in retrieval_results:
        key = result.get("source", "") + result.get("content", "")[:50]
        if key not in seen:
            seen.add(key)
            sources.append(
                {
                    "content": result.get("content", ""),
                    "source": result.get("source", ""),
                    "source_type": result.get("source_type", ""),
                    "score": result.get("score", 0.0),
                    "step": result.get("step"),
                }
            )
    return sources


def _calculate_confidence(retrieval_results: List[Dict]) -> float:
    if not retrieval_results:
        return 0.0
    scores = [r.get("score", 0.0) for r in retrieval_results[:3]]
    return sum(scores) / len(scores) if scores else 0.0


async def multi_hop_summary_node(
    state: MultiHopState,
    runtime: Runtime[InstantSearchRuntimeContext] = None,
    llm: LLMService | None = None,
) -> Dict[str, Any]:
    sub_query = state.get("sub_query", {})
    intermediate_results = state.get("intermediate_results", [])
    all_retrieval_results = state.get("all_retrieval_results", [])
    if state.get("sub_answers"):
        info("[multi_hop] Summary node: sub_answers already exist, skipping")
        return {}

    info("[multi_hop] Summary node generating final answer")
    retrieval_by_index = {}
    for result in all_retrieval_results:
        index_id = result.get("index_id")
        if index_id:
            retrieval_by_index[index_id] = result

    context_parts = []
    for i, result in enumerate(intermediate_results, 1):
        answer = result.get("answer", "")
        query = result.get("query", "")
        source_indices = result.get("source_indices", [])
        source_contents = []
        for idx in source_indices:
            retrieval = retrieval_by_index.get(idx)
            if retrieval:
                content = retrieval.get("content", "")
                source_type = retrieval.get("source_type", "unknown")
                source = retrieval.get("source", "unknown")
                source_contents.append(f"[({source_type}) {source}\n{content}")
        if answer or source_contents:
            step_context = f"步骤 {i}:\n"
            step_context += f"子查询: {query}\n"
            if source_contents:
                step_context += (
                    "引用来源:\n"
                    + "\n".join(f"  - {s}" for s in source_contents)
                    + "\n"
                )
            if answer:
                step_context += f"答案: {answer}\n"
            context_parts.append(step_context)

    intermediate_context = (
        "\n".join(context_parts) if context_parts else "未找到中间步骤信息。"
    )
    if llm is None and runtime and runtime.context:
        llm = runtime.context.llm_service
    if llm is None:
        raise RuntimeError(
            "llm_service is required in runtime context for multi_hop_summary_node"
        )
    messages = [
        SystemMessage(
            content="""你是一个专业的信息整合专家。你的任务是整合多跳检索的结果，生成最终的综合答案。

请按以下结构组织回答：

### 答案总结
[基于所有步骤检索结果的综合回答]

### 推理过程
[简述多步推理的过程]

请确保答案：
1. 涵盖所有关键信息点
2. 逻辑清晰，条理分明
3. 引用相关来源"""
        ),
        HumanMessage(
            content=f"""原始问题：{sub_query.get("query_text", "")}

多跳检索步骤详情（包含各步骤查询、答案及引用来源内容）：
{intermediate_context}

请整合以上信息，生成最终的综合答案。"""
        ),
    ]
    final_answer = await llm.generate(
        messages=messages, model_type="generator", json_mode=False
    )
    sub_answer = SubAnswer(
        sub_query_id=sub_query.get("query_id", "unknown"),
        sub_query_text=sub_query.get("query_text", ""),
        query_type="multi-hop",
        answer=final_answer,
        reasoning_chain=[r.get("answer", "") for r in intermediate_results],
        intermediate_answers=intermediate_results,
        sources=_extract_sources(all_retrieval_results),
        confidence=_calculate_confidence(all_retrieval_results),
        retrieval_results=all_retrieval_results,
    )
    info(
        "[multi_hop] Summary node generated final answer: "
        f"query={sub_query.get('query_text', '')}, final_answer={final_answer}"
    )
    return {"sub_answers": [sub_answer], "messages": [AIMessage(content=final_answer)]}


async def multi_hop_entry_node(state: MultiHopState) -> Dict[str, Any]:
    sub_query = state.get("sub_query", {})
    reasoning_plan = sub_query.get("reasoning_chain", [])
    if not reasoning_plan:
        reasoning_plan = [sub_query.get("query_text", "")]
    message_content = f"请回答: {sub_query.get('query_text', '')}\n参考下面的查询步骤:\n{'\n'.join(reasoning_plan)}"
    info(f"[multi_hop] Entry node for: {sub_query.get('query_text', '')[:50]}...")
    return {
        "messages": [HumanMessage(content=message_content)],
        "reasoning_plan": reasoning_plan,
        "current_step": 0,
        "current_hop": 0,
        "intermediate_results": [],
        "intermediate_answers": [],
        "reasoning_chain": [],
        "all_retrieval_results": {"mode": "RESET", "data": []},
        "result_counter": 0,
    }


def multi_hop_error_node(state: MultiHopState, error_msg: str) -> Dict[str, Any]:
    sub_query = state.get("sub_query", {})
    error(f"[multi_hop] Error node: {error_msg}")
    return {
        "sub_answers": [
            SubAnswer(
                sub_query_id=sub_query.get("query_id", "unknown"),
                sub_query_text=sub_query.get("query_text", ""),
                query_type="multi-hop",
                answer=f"Error: {error_msg}",
                reasoning_chain=[],
                intermediate_answers=[],
                sources=[],
                confidence=0.0,
                retrieval_results=[],
            )
        ]
    }


async def build_multi_hop_subgraph(config=None, llm_service=None, checkpointer=None):
    """Build multi-hop subgraph using dedicated agent assembly."""
    if llm_service is None:
        raise ValueError("llm_service is required to build the multi-hop subgraph")
    settings = get_settings()
    if checkpointer is None:
        checkpointer = await create_checkpointer_async(settings)
    config_data = config or {}
    prompt_overrides = getattr(config_data, "prompt_overrides", None)
    tool_providers = getattr(config_data, "tool_providers", None)
    agent_middleware = getattr(config_data, "agent_middleware", None)
    tools = getattr(config_data, "tools", None)
    if isinstance(config_data, dict):
        prompt_overrides = prompt_overrides or config_data.get("prompt_overrides", {})
        tool_providers = tool_providers or config_data.get("tool_providers", {})
        agent_middleware = agent_middleware or config_data.get("agent_middleware", {})
        tools = tools or config_data.get("tools", [])
    prompt_overrides = prompt_overrides or {}
    tool_providers = tool_providers or {}
    agent_middleware = agent_middleware or {}
    tools = _normalize_to_list(tools)
    provider_tools = _normalize_to_list(
        tool_providers["multi_hop"]() if "multi_hop" in tool_providers else []
    )
    agent_graph = await build_multi_hop_agent_graph(
        system_prompt=prompt_overrides.get("multi_hop"),
        extra_tools=[*tools, *provider_tools],
        extra_middleware=_normalize_to_list(agent_middleware.get("multi_hop")),
        llm_service=llm_service,
        checkpointer=checkpointer,
    )

    workflow = StateGraph(MultiHopState, context_schema=InstantSearchRuntimeContext)
    workflow.add_node(NodeNames.MULTI_HOP_ENTRY.value, multi_hop_entry_node)
    workflow.add_node(NodeNames.MULTI_HOP_AGENT.value, agent_graph)
    workflow.add_node(NodeNames.MULTI_HOP_SUMMARY.value, multi_hop_summary_node)
    workflow.set_entry_point(NodeNames.MULTI_HOP_ENTRY.value)
    workflow.add_edge(NodeNames.MULTI_HOP_ENTRY.value, NodeNames.MULTI_HOP_AGENT.value)
    workflow.add_edge(
        NodeNames.MULTI_HOP_AGENT.value, NodeNames.MULTI_HOP_SUMMARY.value
    )
    workflow.add_edge(NodeNames.MULTI_HOP_SUMMARY.value, END)
    compiled = workflow.compile(checkpointer=checkpointer)
    info("[multi_hop] Compiled multi-hop subgraph with streaming support")
    return compiled


__all__ = [
    "build_multi_hop_subgraph",
    "multi_hop_entry_node",
    "multi_hop_error_node",
    "multi_hop_summary_node",
]
