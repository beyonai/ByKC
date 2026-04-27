"""Multi-hop summary agent using LangGraph create_agent."""

from enum import Enum
from typing import Annotated, Any, Dict, List, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from by_qa.core.logger import info
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.common.reducers import merge_list_with_mode
from by_qa.qa.instant.state import SubAnswer
from by_qa.qa.services.llm_service import LLMService

DEFAULT_MULTI_HOP_SUMMARY_PROMPT = """你是一个专业的信息整合专家。你的任务是整合多跳检索的结果，生成最终的综合答案。

请按以下结构组织回答：

### 答案总结
[基于所有步骤检索结果的综合回答]

### 推理过程
[简述多步推理的过程]

请确保答案：
1. 涵盖所有关键信息点
2. 逻辑清晰，条理分明
3. 引用相关来源"""


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

    return "\n".join(context_parts) if context_parts else "未找到中间步骤信息。"


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
                    f"原始问题：{sub_query.get('query_text', '')}\n\n"
                    "多跳检索步骤详情（包含各步骤查询、答案及引用来源内容）：\n"
                    f"{intermediate_context}\n\n"
                    "请整合以上信息，生成最终的综合答案。"
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
    system_prompt: str | None = None,
    checkpointer=None,
):
    llm = await llm_service._get_streaming_model("generator")
    agent_graph = create_agent(
        model=llm,
        tools=[],
        state_schema=MultiHopSummaryAgentState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=system_prompt or DEFAULT_MULTI_HOP_SUMMARY_PROMPT,
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
