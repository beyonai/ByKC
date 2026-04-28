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


async def multi_hop_entry_node(state: MultiHopState) -> Dict[str, Any]:
    sub_query = state.get("sub_query", {})
    reasoning_plan = sub_query.get("reasoning_chain", [])
    if not reasoning_plan:
        reasoning_plan = [sub_query.get("query_text", "")]
    message_content = f"Answer: {sub_query.get('query_text', '')}\nReference query steps:\n{'\n'.join(reasoning_plan)}"
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


async def build_multi_hop_subgraph(
    *,
    agent_override=None,
    summary_override=None,
    llm_service=None,
    checkpointer=None,
):
    """Build multi-hop subgraph using dedicated agent assembly."""
    if llm_service is None:
        raise ValueError("llm_service is required to build the multi-hop subgraph")
    agent_graph = await build_multi_hop_agent_graph(
        override=agent_override,
        llm_service=llm_service,
        checkpointer=checkpointer,
    )
    summary_graph = await build_multi_hop_summary_subgraph(
        llm_service=llm_service,
        override=summary_override,
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
