"""Answer synthesizer agent using LangGraph create_agent."""

import time
from enum import Enum
from typing import Annotated, Any, Dict, Optional, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.context_manager import build_context_for_llm
from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.services.llm_service import LLMService

DEFAULT_RETRIEVED_CONTEXT_ANSWER_PROMPT = """你是一个严谨的知识库问答助手。

你的任务是基于给定检索结果回答用户问题。

要求：
- 直接回答问题，保持简洁清晰
- 只能使用检索结果中的信息，不要编造
- 如果检索结果不足以回答，请明确说明缺少相关信息
- 如有必要，可以简要列出依据
- 直接输出 Markdown 文本，不要输出 JSON"""


class AnswerNodeNames(str, Enum):
    ENTRY = "answer_entry"
    AGENT = "answer_agent"
    SUMMARY = "answer_summary"


class AnswerSynthesizerAgentState(TypedDict):
    """State for the answer synthesizer subgraph."""

    messages: Annotated[list, add_messages]
    original_query: str
    sub_queries: list[dict]
    retrieval_results: list[dict]
    final_answer: str
    answer_time: Optional[float]


async def answer_entry_node(state: AnswerSynthesizerAgentState) -> Dict[str, Any]:
    """Entry node: build the HumanMessage for the answer synthesizer agent."""
    original_query = state.get("original_query", "")
    sub_queries = state.get("sub_queries") or [
        {
            "query_id": "sq_1",
            "query_text": state.get("rewritten_query") or original_query,
        }
    ]
    retrieval_results = state.get("retrieval_results", [])
    context = build_context_for_llm(retrieval_results)
    sub_queries_text = "\n".join(
        f"{i + 1}. {sq['query_text']}" for i, sq in enumerate(sub_queries)
    )
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(
                content=(
                    f"用户原始问题：{original_query}\n"
                    f"检索用子问题：\n{sub_queries_text}\n\n"
                    f"检索结果：\n{context}\n\n"
                    "请基于以上检索结果，针对每个子问题分别回答，最后汇总。"
                ),
                additional_kwargs=agent_metadata(AnswerNodeNames.ENTRY.value),
            ),
        ],
        "answer_time": time.time(),
    }


async def answer_summary_node(state: AnswerSynthesizerAgentState) -> Dict[str, Any]:
    """Summary node: extract the final answer from agent messages."""
    start_time = state.get("answer_time")
    final_answer = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            final_answer = msg.content
            break
    answer_time = time.time() - start_time if start_time else 0.0
    return {
        "final_answer": final_answer,
        "messages": [AIMessage(content=final_answer)],
        "answer_time": answer_time,
    }


async def build_answer_synthesizer_subgraph(
    *,
    llm_service: LLMService,
    system_prompt: str | None = None,
    checkpointer=None,
):
    """Build the answer synthesizer subgraph: entry -> create_agent -> summary."""
    llm = await llm_service._get_streaming_model("generator")
    agent_graph = create_agent(
        model=llm,
        tools=[],
        state_schema=AnswerSynthesizerAgentState,
        context_schema=QARuntimeContext,
        checkpointer=checkpointer,
        system_prompt=system_prompt or DEFAULT_RETRIEVED_CONTEXT_ANSWER_PROMPT,
    )
    workflow = StateGraph(AnswerSynthesizerAgentState, context_schema=QARuntimeContext)
    workflow.add_node(AnswerNodeNames.ENTRY.value, answer_entry_node)
    workflow.add_node(AnswerNodeNames.AGENT.value, agent_graph)
    workflow.add_node(AnswerNodeNames.SUMMARY.value, answer_summary_node)
    workflow.set_entry_point(AnswerNodeNames.ENTRY.value)
    workflow.add_edge(AnswerNodeNames.ENTRY.value, AnswerNodeNames.AGENT.value)
    workflow.add_edge(AnswerNodeNames.AGENT.value, AnswerNodeNames.SUMMARY.value)
    workflow.add_edge(AnswerNodeNames.SUMMARY.value, END)
    return workflow.compile(checkpointer=checkpointer)


__all__ = [
    "AnswerSynthesizerAgentState",
    "DEFAULT_RETRIEVED_CONTEXT_ANSWER_PROMPT",
    "answer_entry_node",
    "answer_summary_node",
    "build_answer_synthesizer_subgraph",
]
