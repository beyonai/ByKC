"""Multi-hop subgraph builder for the instant-search capability."""

from enum import Enum
from typing import Any, Dict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from by_qa.core.logger import error, info
from by_qa.qa.agents.multi_hop_summarizer import build_multi_hop_summary_subgraph
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.instant.agents.multi_hop_react import build_multi_hop_agent_graph
from by_qa.qa.instant.state import MultiHopState, SubAnswer


class MultiHopNodeNames(str, Enum):
    ENTRY = "multi_hop_entry"
    AGENT = "multi_hop_agent"
    EXIT = "multi_hop_exit"
    SUMMARY = "multi_hop_summary"


def _normalize_to_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


async def multi_hop_entry_node(state: MultiHopState) -> Dict[str, Any]:
    sub_query = state.get("sub_query", {})
    reasoning_plan = sub_query.get("reasoning_chain", [])
    if not reasoning_plan:
        reasoning_plan = [sub_query.get("query_text", "")]
    message_content = f"请回答: {sub_query.get('query_text', '')}\n参考下面的查询步骤:\n{'\n'.join(reasoning_plan)}"
    info(f"[multi_hop] Entry node for: {sub_query.get('query_text', '')[:50]}...")
    return {
        "messages": [
            HumanMessage(
                content=message_content,
                additional_kwargs=agent_metadata(MultiHopNodeNames.ENTRY.value),
            )
        ],
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
    summary_graph = await build_multi_hop_summary_subgraph(
        llm_service=llm_service,
        system_prompt=prompt_overrides.get("multi_hop_summary"),
        checkpointer=checkpointer,
    )

    workflow = StateGraph(MultiHopState, context_schema=QARuntimeContext)
    workflow.add_node(MultiHopNodeNames.ENTRY.value, multi_hop_entry_node)
    workflow.add_node(MultiHopNodeNames.AGENT.value, agent_graph)
    workflow.add_node(MultiHopNodeNames.SUMMARY.value, summary_graph)
    workflow.set_entry_point(MultiHopNodeNames.ENTRY.value)
    workflow.add_edge(MultiHopNodeNames.ENTRY.value, MultiHopNodeNames.AGENT.value)
    workflow.add_edge(MultiHopNodeNames.AGENT.value, MultiHopNodeNames.SUMMARY.value)
    workflow.add_edge(MultiHopNodeNames.SUMMARY.value, END)
    compiled = workflow.compile(checkpointer=checkpointer)
    info("[multi_hop] Compiled multi-hop subgraph with streaming support")
    return compiled


__all__ = [
    "build_multi_hop_subgraph",
    "multi_hop_entry_node",
    "multi_hop_error_node",
]
