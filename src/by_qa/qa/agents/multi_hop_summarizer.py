"""Multi-hop summary agent using LangGraph create_agent."""

from enum import Enum
from typing import Annotated, Any, Dict, List, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from by_qa.core.logger import info
from by_qa.qa.common.config import AgentOverride
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.fallback_messages import FallbackMessage
from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.common.prompt_fragments import DEFAULT_LANGUAGE_INSTRUCTION
from by_qa.qa.common.reducers import merge_list_with_mode
from by_qa.qa.instant.state import SubAnswer
from by_qa.qa.services.llm_service import LLMService

DEFAULT_MULTI_HOP_SUMMARY_PROMPT = (
    """You are a professional information synthesis expert. Your task is to integrate multi-hop retrieval results and generate a final comprehensive answer.

Please organize your answer in the following structure:

### Answer Summary
[Comprehensive answer based on all step retrieval results]

### Reasoning Process
[Brief description of the multi-step reasoning process]

Please ensure the answer:
1. Covers all key information points
2. Is logically clear and well-organized
3. References relevant sources"""
    + DEFAULT_LANGUAGE_INSTRUCTION
)


class MultiHopSummaryNodeNames(str, Enum):
    ENTRY = "mh_summary_entry"
    AGENT = "mh_summary_agent"
    SUMMARY = "mh_summary_summary"


class MultiHopSummaryAgentState(TypedDict):
    """State for the multi-hop summary subgraph."""

    messages: Annotated[list, add_messages]
    sub_query: dict
    intermediate_results: list
    all_retrieval_results: Annotated[list, merge_list_with_mode]
    sub_answers: Annotated[list, merge_list_with_mode]


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


def _build_intermediate_context(
    intermediate_results: List[Dict], all_retrieval_results: List[Dict]
) -> str:
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
            step_context = f"Step {i}:\n"
            step_context += f"Sub-query: {query}\n"
            if source_contents:
                step_context += (
                    "Referenced sources:\n"
                    + "\n".join(f"  - {s}" for s in source_contents)
                    + "\n"
                )
            if answer:
                step_context += f"Answer: {answer}\n"
            context_parts.append(step_context)

    return (
        "\n".join(context_parts)
        if context_parts
        else FallbackMessage.NO_INTERMEDIATE_STEPS
    )


async def mh_summary_entry_node(
    state: MultiHopSummaryAgentState,
) -> Dict[str, Any]:
    sub_query = state.get("sub_query", {})
    if state.get("sub_answers"):
        info("[multi_hop] Summary entry: sub_answers already exist, skipping")
        return {"sub_answers": state["sub_answers"]}

    intermediate_results = state.get("intermediate_results", [])
    all_retrieval_results = state.get("all_retrieval_results", [])
    intermediate_context = _build_intermediate_context(
        intermediate_results, all_retrieval_results
    )
    info("[multi_hop] Summary entry: building context for LLM")
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(
                content=(
                    f"Original question: {sub_query.get('query_text', '')}\n\n"
                    "Multi-hop retrieval step details (including queries, answers, and referenced source content for each step):\n"
                    f"{intermediate_context}\n\n"
                    "Please integrate the above information and generate a final comprehensive answer."
                ),
                additional_kwargs=agent_metadata(MultiHopSummaryNodeNames.ENTRY.value),
            ),
        ],
    }


async def mh_summary_summary_node(
    state: MultiHopSummaryAgentState,
) -> Dict[str, Any]:
    if state.get("sub_answers"):
        return {}

    sub_query = state.get("sub_query", {})
    intermediate_results = state.get("intermediate_results", [])
    all_retrieval_results = state.get("all_retrieval_results", [])

    final_answer = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, AIMessage) and getattr(msg, "content", ""):
            final_answer = msg.content
            break

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


def _route_after_entry(state: Dict[str, Any]) -> str:
    if state.get("sub_answers"):
        return MultiHopSummaryNodeNames.SUMMARY.value
    return MultiHopSummaryNodeNames.AGENT.value


async def build_multi_hop_summary_subgraph(
    *,
    llm_service: LLMService,
    override: AgentOverride | None = None,
    checkpointer=None,
):
    override = override or AgentOverride()
    llm = await llm_service._get_streaming_model("generator")
    agent_graph = create_agent(
        model=llm,
        tools=[],
        state_schema=MultiHopSummaryAgentState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=override.prompt or DEFAULT_MULTI_HOP_SUMMARY_PROMPT,
    )
    workflow = StateGraph(MultiHopSummaryAgentState, context_schema=QARuntimeContext)
    workflow.add_node(MultiHopSummaryNodeNames.ENTRY.value, mh_summary_entry_node)
    workflow.add_node(MultiHopSummaryNodeNames.AGENT.value, agent_graph)
    workflow.add_node(MultiHopSummaryNodeNames.SUMMARY.value, mh_summary_summary_node)
    workflow.set_entry_point(MultiHopSummaryNodeNames.ENTRY.value)
    workflow.add_conditional_edges(
        MultiHopSummaryNodeNames.ENTRY.value,
        _route_after_entry,
        {
            MultiHopSummaryNodeNames.AGENT.value: MultiHopSummaryNodeNames.AGENT.value,
            MultiHopSummaryNodeNames.SUMMARY.value: MultiHopSummaryNodeNames.SUMMARY.value,
        },
    )
    workflow.add_edge(
        MultiHopSummaryNodeNames.AGENT.value, MultiHopSummaryNodeNames.SUMMARY.value
    )
    workflow.add_edge(MultiHopSummaryNodeNames.SUMMARY.value, END)
    return workflow.compile(checkpointer=checkpointer)


__all__ = [
    "DEFAULT_MULTI_HOP_SUMMARY_PROMPT",
    "MultiHopSummaryAgentState",
    "MultiHopSummaryNodeNames",
    "build_multi_hop_summary_subgraph",
    "mh_summary_entry_node",
    "mh_summary_summary_node",
]
