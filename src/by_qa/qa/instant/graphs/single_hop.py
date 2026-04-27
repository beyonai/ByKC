"""Single-hop subgraph builder for the instant-search capability."""

from enum import Enum
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph

from by_qa.core.logger import info
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.instant.agents.single_hop_react import build_single_hop_agent_graph
from by_qa.qa.instant.state import SingleHopState, SubAnswer


class SingleHopNodeNames(str, Enum):
    ENTRY = "single_hop_entry"
    AGENT = "single_hop_agent"
    SUMMARY = "single_hop_summary"


def _extract_final_answer(messages: List[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and getattr(message, "content", ""):
            return message.content
        if (
            isinstance(message, dict)
            and message.get("type") == "ai"
            and message.get("content")
        ):
            return message["content"]
    return ""


def _normalize_to_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _extract_sources(
    retrieval_results: List[Dict], cited_indices: List[str]
) -> List[Dict]:
    allowed = set(cited_indices or [])
    sources = []
    for result in retrieval_results:
        if allowed and result.get("index_id") not in allowed:
            continue
        sources.append(
            {
                "content": result.get("content", ""),
                "source": result.get("source", ""),
                "source_type": result.get("source_type", ""),
                "score": result.get("score", 0.0),
            }
        )
    return sources


def _calculate_confidence(
    retrieval_results: List[Dict], cited_indices: List[str]
) -> float:
    relevant_results = retrieval_results
    if cited_indices:
        cited_set = set(cited_indices)
        relevant_results = [
            r for r in retrieval_results if r.get("index_id") in cited_set
        ]
    if not relevant_results:
        return 0.0
    scores = [r.get("score", 0.0) for r in relevant_results[:3]]
    return sum(scores) / len(scores) if scores else 0.0


async def single_hop_entry_node(state: SingleHopState) -> Dict[str, Any]:
    """Initialize the single-hop agent state."""
    sub_query = state.get("sub_query", {})
    query_text = sub_query.get("query_text", "")
    info(f"[single_hop] Entry node for: {query_text[:50]}...")
    return {
        "messages": [
            HumanMessage(
                content=f"Answer this single-hop question: {query_text}",
                additional_kwargs=agent_metadata(SingleHopNodeNames.ENTRY.value),
            )
        ],
        "retrieval_results": {"mode": "RESET", "data": []},
        "cited_indices": [],
        "result_counter": 0,
    }


async def single_hop_summary_node(state: SingleHopState) -> Dict[str, Any]:
    """Build the single-hop sub-answer from the agent result state."""
    if state.get("sub_answers"):
        info("[single_hop] Summary node: sub_answers already exist, skipping")
        return {}

    sub_query = state.get("sub_query", {})
    query_id = sub_query.get("query_id", "unknown")
    query_text = sub_query.get("query_text", "")
    final_answer = _extract_final_answer(state.get("messages", []))
    retrieval_results = state.get("retrieval_results", [])
    cited_indices = state.get("cited_indices", [])

    sub_answer = SubAnswer(
        sub_query_id=query_id,
        sub_query_text=query_text,
        query_type="single-hop",
        answer=final_answer,
        reasoning_chain=[],
        intermediate_answers=[],
        sources=_extract_sources(retrieval_results, cited_indices),
        confidence=_calculate_confidence(retrieval_results, cited_indices),
        retrieval_results=retrieval_results,
    )
    info(
        "[single_hop] Summary node generated final answer: "
        f"query={query_text}, final_answer={final_answer}"
    )
    return {
        "sub_answers": [sub_answer],
        "messages": [AIMessage(content=final_answer)],
    }


async def build_single_hop_subgraph(config=None, llm_service=None, checkpointer=None):
    """Build single-hop subgraph using dedicated agent assembly."""
    if llm_service is None:
        raise ValueError("llm_service is required to build the single-hop subgraph")
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
        tool_providers["single_hop"]() if "single_hop" in tool_providers else []
    )
    agent_graph = await build_single_hop_agent_graph(
        system_prompt=prompt_overrides.get("single_hop"),
        extra_tools=[*tools, *provider_tools],
        extra_middleware=_normalize_to_list(agent_middleware.get("single_hop")),
        llm_service=llm_service,
        checkpointer=checkpointer,
    )

    workflow = StateGraph(SingleHopState, context_schema=QARuntimeContext)
    workflow.add_node(SingleHopNodeNames.ENTRY.value, single_hop_entry_node)
    workflow.add_node(SingleHopNodeNames.AGENT.value, agent_graph)
    workflow.add_node(SingleHopNodeNames.SUMMARY.value, single_hop_summary_node)
    workflow.set_entry_point(SingleHopNodeNames.ENTRY.value)
    workflow.add_edge(SingleHopNodeNames.ENTRY.value, SingleHopNodeNames.AGENT.value)
    workflow.add_edge(SingleHopNodeNames.AGENT.value, SingleHopNodeNames.SUMMARY.value)
    workflow.add_edge(SingleHopNodeNames.SUMMARY.value, END)
    return workflow.compile(checkpointer=checkpointer)


__all__ = [
    "build_single_hop_subgraph",
    "single_hop_entry_node",
    "single_hop_summary_node",
]
