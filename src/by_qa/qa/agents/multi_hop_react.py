"""Multi-hop ReAct agent: state, tools, nodes, and subgraph builder."""

import json
import operator
from enum import Enum
from typing import Annotated, Any, Dict, List, TypedDict

from langchain.agents import create_agent
from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, StateGraph
from langgraph.graph.message import Messages, add_messages
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from by_qa.core.logger import error, info
from by_qa.qa.agents.multi_hop_summarizer import build_multi_hop_summary_subgraph
from by_qa.qa.common.config import AgentOverride
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.common.middleware.tool_call_guard import ToolCallGuardMiddleware
from by_qa.qa.common.operation_registry import OPERATION_REGISTRY, OperationType
from by_qa.qa.common.prompt_fragments import DEFAULT_LANGUAGE_INSTRUCTION
from by_qa.qa.common.reducers import merge_list_with_mode
from by_qa.qa.common.state import SubAnswer
from by_qa.qa.services.llm_service import LLMService
from by_qa.qa.tools.knowledge_tools import DispatcherToolMiddleware

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class MultiHopState(TypedDict):
    """State for multi-hop subgraph."""

    sub_query: dict[str, Any]
    sub_query_idx: int
    messages: Annotated[Messages, add_messages]
    reasoning_plan: list[str]
    current_step: int
    intermediate_results: Annotated[list[dict[str, Any]], operator.add]
    current_hop: int
    intermediate_answers: list[dict[str, Any]]
    reasoning_chain: list[str]
    all_retrieval_results: Annotated[list[dict[str, Any]], merge_list_with_mode]
    sub_answers: Annotated[list[SubAnswer], merge_list_with_mode]
    result_counter: int


# ---------------------------------------------------------------------------
# Node names
# ---------------------------------------------------------------------------


class MultiHopNodeNames(str, Enum):
    ENTRY = "multi_hop_entry"
    AGENT = "multi_hop_agent"
    EXIT = "multi_hop_exit"
    SUMMARY = "multi_hop_summary"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def next_hop(
    current_query: str,
    current_answer: str,
    next_query: str,
    source_indices: List[str],
    state: Annotated[MultiHopState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Complete the current step and proceed to the next hop query."""
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
                            "message": f"Hop {current_step + 1} completed, retrieval result: {current_answer}. Retrieval context has been cleaned up.",
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
    """Complete multi-hop retrieval and jump to the summary node."""
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
                            "message": f"Hop {current_step + 1} completed, multi-hop retrieval finished, preparing to generate final answer.",
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


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

DEFAULT_MULTI_HOP_SYSTEM_PROMPT = (
    """You are an intelligent multi-hop problem-solving assistant.

Your task is to answer complex questions through multi-step reasoning.

[Workflow]
1. Analyze the current question that needs to be answered
2. Call search_knowledge to obtain information (can be called multiple times until sufficient information is collected)
3. When you are sure you need to proceed to the next reasoning step, call next_hop (this advances the step counter)
4. Repeat steps 2-3 until you can provide a final answer
5. Call finalize to give the complete answer and end the process

[Tool Description]
- search_knowledge: Retrieve information, results contain index_id (e.g., s0-1, s1-2) that can be used for citation
- next_hop: Complete the current step and proceed to the next hop. After calling, newly retrieved results will be marked as the next hop
- finalize: End the entire process and give the final answer

[Important Rules]
- next_hop advances the step counter, only call it when you are sure you want to proceed to the next reasoning step
- Within the same hop, you can call search_knowledge multiple times to collect information
- When calling next_hop or finalize, list the referenced document IDs in source_indices
- Always maintain coherence and logic in your reasoning"""
    + DEFAULT_LANGUAGE_INSTRUCTION
)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


async def build_multi_hop_agent_graph(
    *,
    override: AgentOverride | None = None,
    llm_service: LLMService,
    checkpointer: Any | None = None,
):
    """Build the configurable multi-hop agent graph."""
    override = override or AgentOverride()
    llm = await llm_service._get_streaming_model("retrieval")
    tools = [next_hop, finalize] + list(override.tools)
    middleware = [
        ToolCallGuardMiddleware(),
        DispatcherToolMiddleware(
            index_id_fn=lambda sub_query_idx, step, item_id: (
                f"{sub_query_idx}-{step}-{item_id}"
            ),
            follow_up_prompt="A retrieval has been completed. If this retrieval did not collect sufficient information, continue calling search_knowledge to collect more. Otherwise, immediately call next_hop to clean up context and proceed to the next query. If all retrievals are complete, immediately call finalize to end the multi-hop retrieval and generate the final answer.",
        ),
    ] + list(override.middleware)
    return create_agent(
        model=llm,
        tools=tools,
        middleware=middleware,
        state_schema=MultiHopState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=override.prompt or DEFAULT_MULTI_HOP_SYSTEM_PROMPT,
    )


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
    "DEFAULT_MULTI_HOP_SYSTEM_PROMPT",
    "MultiHopNodeNames",
    "MultiHopState",
    "build_multi_hop_agent_graph",
    "build_multi_hop_subgraph",
    "finalize",
    "multi_hop_entry_node",
    "multi_hop_error_node",
    "next_hop",
]
