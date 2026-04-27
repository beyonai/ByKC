"""Standalone question rewriter agent using LangGraph create_agent."""

import time
from enum import Enum
from typing import Annotated, Any, Dict, Optional, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from by_qa.core.logger import info
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.messages import agent_metadata, extract_user_query_history
from by_qa.qa.common.prompt_fragments import DEFAULT_LANGUAGE_INSTRUCTION
from by_qa.qa.services.llm_service import LLMService

DEFAULT_STANDALONE_QUESTION_REWRITE_PROMPT = (
    """You are a question rewriting assistant. Based on user history, complete the following two tasks:
1. Fill in omitted subjects, objects, time references, and other context in the current input
2. If the completed question contains parallel independent sub-questions, split them into multiple complete questions

Requirements:
- Only identify parallel structures (removing conjunctions yields two or more semantically complete and mutually independent questions)
- Do not split chained modifier structures (A's B's C is a single question)
- Do not analyze reasoning depth
- Do not answer the question
- Output one complete question per line, no numbering or explanations
- If the current input is already complete with no parallel structure, output it as-is on one line"""
    + DEFAULT_LANGUAGE_INSTRUCTION
)


class RewriterNodeNames(str, Enum):
    ENTRY = "rewriter_entry"
    AGENT = "rewriter_agent"
    SUMMARY = "rewriter_summary"


class RewriterAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    original_query: str
    sub_queries: list[dict]
    rewritten_query: str
    rewrite_time: Optional[float]


async def rewriter_entry_node(state: RewriterAgentState) -> Dict[str, Any]:
    """Build HumanMessage from query and history, clear inherited messages."""
    original_query = state.get("original_query", "")
    messages = state.get("messages", [])
    history = extract_user_query_history(messages)
    if history:
        user_content = (
            "User history:\n"
            f"{history}\n\n"
            f"Current user input: {original_query}\n\n"
            "Output the rewritten questions, one per line."
        )
    else:
        user_content = f"Current user input: {original_query}\n\nOutput the rewritten questions, one per line."
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(
                content=user_content,
                additional_kwargs=agent_metadata(RewriterNodeNames.ENTRY.value),
            ),
        ],
        "rewrite_time": time.time(),
        "sub_queries": [],
        "rewritten_query": "",
    }


async def rewriter_summary_node(state: RewriterAgentState) -> Dict[str, Any]:
    """Parse agent output into sub_queries list."""
    start_time = state.get("rewrite_time")
    original_query = state.get("original_query", "")
    raw = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            raw = msg.content
            break
    try:
        lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
        texts = lines if lines else [original_query]
    except Exception:
        texts = [original_query]
    sub_queries = [
        {"query_id": f"sq_{i + 1}", "query_text": t} for i, t in enumerate(texts)
    ]
    rewrite_time = time.time() - start_time if start_time else 0.0
    info(
        "[fast.rewrite] query=%s sub_queries=%s",
        original_query,
        [sq["query_text"] for sq in sub_queries],
    )
    return {
        "sub_queries": sub_queries,
        "rewritten_query": sub_queries[0]["query_text"],
        "rewrite_time": rewrite_time,
    }


async def build_rewriter_subgraph(
    *,
    llm_service: LLMService,
    system_prompt: str | None = None,
    checkpointer=None,
):
    llm = await llm_service._get_streaming_model("classifier")
    agent_graph = create_agent(
        model=llm,
        tools=[],
        state_schema=RewriterAgentState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=system_prompt or DEFAULT_STANDALONE_QUESTION_REWRITE_PROMPT,
    )
    workflow = StateGraph(RewriterAgentState, context_schema=QARuntimeContext)
    workflow.add_node(RewriterNodeNames.ENTRY.value, rewriter_entry_node)
    workflow.add_node(RewriterNodeNames.AGENT.value, agent_graph)
    workflow.add_node(RewriterNodeNames.SUMMARY.value, rewriter_summary_node)
    workflow.set_entry_point(RewriterNodeNames.ENTRY.value)
    workflow.add_edge(RewriterNodeNames.ENTRY.value, RewriterNodeNames.AGENT.value)
    workflow.add_edge(RewriterNodeNames.AGENT.value, RewriterNodeNames.SUMMARY.value)
    workflow.add_edge(RewriterNodeNames.SUMMARY.value, END)
    return workflow.compile(checkpointer=checkpointer)


__all__ = [
    "DEFAULT_STANDALONE_QUESTION_REWRITE_PROMPT",
    "RewriterAgentState",
    "build_rewriter_subgraph",
    "rewriter_entry_node",
    "rewriter_summary_node",
]
