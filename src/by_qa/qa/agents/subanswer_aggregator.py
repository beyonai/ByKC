"""Sub-answer aggregator agent using LangGraph create_agent."""

import time
from enum import Enum
from typing import Annotated, Any, Dict, Optional, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from by_qa.core.logger import info
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.fallback_messages import FallbackMessage
from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.common.prompt_fragments import DEFAULT_LANGUAGE_INSTRUCTION
from by_qa.qa.common.reducers import merge_list_with_mode
from by_qa.qa.services.llm_service import LLMService

SYSTEM_PROMPT = (
    """You are a professional answer aggregation expert. Your task is to generate a complete answer to the user's original question based on multiple sub-query answers.

## Core Requirements

1. **Comprehensive answer**: Integrate all sub-query answers to generate a complete response to the original question
2. **Logical coherence**: Ensure the answer is logically clear with natural transitions between sections
3. **Markdown format**: Output directly in Markdown format, do not output JSON
4. **No citations**: Do not annotate citation sources, focus on the answer content itself

## Answer Structure

Organize the answer structure flexibly based on the number and type of sub-queries:

- **Single sub-query**: Present the sub-query answer directly
- **Multiple sub-queries**:
  - If sub-queries are parallel (e.g., "revenue of A and B"), present each separately then give a comprehensive conclusion
  - If sub-queries have dependencies, present in logical order
  - For multi-hop sub-queries, briefly explain the reasoning process

## Notes

1. Stay objective, do not add information not present in sub-query answers
2. If sub-query answers conflict, point it out and provide the most likely conclusion
3. If some sub-queries failed to find answers, indicate that information is missing
4. The answer should directly address the user's original question"""
    + DEFAULT_LANGUAGE_INSTRUCTION
)


def _build_sub_answers_context(sub_answers: list[dict]) -> str:
    """Format sub-answers into a context string for the aggregator."""
    if not sub_answers:
        return FallbackMessage.NO_SUB_QUERY_ANSWERS

    parts: list[str] = []
    for index, sub_answer in enumerate(sub_answers, 1):
        query_text = sub_answer.get("sub_query_text", f"Sub-query {index}")
        query_type = sub_answer.get("query_type", "single-hop")
        answer = sub_answer.get("answer", "")
        reasoning_chain = sub_answer.get("reasoning_chain", [])
        confidence = sub_answer.get("confidence", 0.0)
        part = (
            f"## Sub-query {index}: {query_text}\n"
            f"Type: {query_type}\n"
            f"Confidence: {confidence:.2f}\n\n"
            f"### Answer\n{answer}\n"
        )
        if reasoning_chain:
            part += "\n### Reasoning Process\n"
            for step in reasoning_chain:
                part += f"- {step}\n"
        parts.append(part)
    return "\n\n---\n\n".join(parts)


class AggregatorNodeNames(str, Enum):
    ENTRY = "aggregator_entry"
    AGENT = "aggregator_agent"
    SUMMARY = "aggregator_summary"


class AggregatorAgentState(TypedDict):
    """State for the aggregator subgraph."""

    messages: Annotated[list, add_messages]
    original_query: str
    sub_answers: Annotated[list, merge_list_with_mode]
    final_answer: str
    aggregation_time: Optional[float]


async def aggregator_entry_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Entry node: build the HumanMessage for the aggregator agent."""
    original_query = state["original_query"]
    sub_answers = state.get("sub_answers", [])

    if not sub_answers:
        return {
            "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)],
            "final_answer": FallbackMessage.FAILED_TO_GENERATE_ANSWER,
            "aggregation_time": 0.0,
        }

    sub_answers_context = _build_sub_answers_context(sub_answers)
    user_content = (
        f"User original question: {original_query}\n\n"
        f"Sub-query answers:\n{sub_answers_context}\n\n"
        "Based on the above sub-query answers, generate a complete answer to the user's original question."
    )
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(
                content=user_content,
                additional_kwargs=agent_metadata(AggregatorNodeNames.ENTRY.value),
            ),
        ],
        "aggregation_time": time.time(),
    }


async def aggregator_summary_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Summary node: extract the final answer from the agent response."""
    if state.get("final_answer") == FallbackMessage.FAILED_TO_GENERATE_ANSWER:
        return {}

    messages = state.get("messages", [])
    original_query = state.get("original_query", "")
    start_time = state.get("aggregation_time", time.time())

    final_answer = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and getattr(msg, "content", ""):
            final_answer = msg.content
            break
        if isinstance(msg, dict) and msg.get("type") == "ai" and msg.get("content"):
            final_answer = msg["content"]
            break

    aggregation_time = time.time() - start_time
    info(f"[subanswer_aggregator] Aggregation completed in {aggregation_time:.2f}s ")
    info(
        "[subanswer_aggregator] Aggregation generated final answer: "
        f"query={original_query}, final_answer={final_answer}"
    )
    return {
        "final_answer": final_answer,
        "aggregation_time": aggregation_time,
    }


def _route_after_entry(state: Dict[str, Any]) -> str:
    """Route after entry: skip agent if sub_answers was empty."""
    if state.get("final_answer") == FallbackMessage.FAILED_TO_GENERATE_ANSWER:
        return AggregatorNodeNames.SUMMARY.value
    return AggregatorNodeNames.AGENT.value


async def build_aggregator_subgraph(
    *,
    llm_service: LLMService,
    system_prompt: str | None = None,
    checkpointer=None,
):
    """Build the aggregator subgraph: entry → create_agent → summary."""
    llm = await llm_service._get_streaming_model("generator")

    agent_graph = create_agent(
        model=llm,
        tools=[],
        state_schema=AggregatorAgentState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=system_prompt or SYSTEM_PROMPT,
    )

    workflow = StateGraph(AggregatorAgentState, context_schema=QARuntimeContext)
    workflow.add_node(AggregatorNodeNames.ENTRY.value, aggregator_entry_node)
    workflow.add_node(AggregatorNodeNames.AGENT.value, agent_graph)
    workflow.add_node(AggregatorNodeNames.SUMMARY.value, aggregator_summary_node)
    workflow.set_entry_point(AggregatorNodeNames.ENTRY.value)
    workflow.add_conditional_edges(
        AggregatorNodeNames.ENTRY.value,
        _route_after_entry,
        {
            AggregatorNodeNames.AGENT.value: AggregatorNodeNames.AGENT.value,
            AggregatorNodeNames.SUMMARY.value: AggregatorNodeNames.SUMMARY.value,
        },
    )
    workflow.add_edge(
        AggregatorNodeNames.AGENT.value, AggregatorNodeNames.SUMMARY.value
    )
    workflow.add_edge(AggregatorNodeNames.SUMMARY.value, END)
    return workflow.compile(checkpointer=checkpointer)


__all__ = [
    "AggregatorAgentState",
    "SYSTEM_PROMPT",
    "_build_sub_answers_context",
    "aggregator_entry_node",
    "aggregator_summary_node",
    "build_aggregator_subgraph",
]
