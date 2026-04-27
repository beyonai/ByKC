"""Standalone question rewriter agent using LangGraph create_agent."""

import time
from typing import Annotated, Any, Dict, Optional, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from by_qa.core.logger import info
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.services.llm_service import LLMService

DEFAULT_STANDALONE_QUESTION_REWRITE_PROMPT = """你是一个问题改写助手。结合用户历史输入，完成以下两件事：
1. 补全当前输入中省略的主语、对象、时间等上下文
2. 如果补全后的问题包含并列的独立子问题，将其拆分为多个完整问题

要求：
- 只识别并列结构（去掉连接词后能拆出两个以上语义完整且互相独立的问题）
- 不拆分链式修饰结构（A的B的C是单一问题）
- 不分析推理深度
- 不回答问题
- 每行输出一个完整问题，不输出编号或解释
- 如果当前输入已完整且无并列结构，原样输出一行"""


class RewriterAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    original_query: str
    sub_queries: list[dict]
    rewritten_query: str
    rewrite_time: Optional[float]


def extract_user_query_history(messages: list[Any], max_turns: int = 5) -> str:
    """Extract previous user inputs, excluding the current turn."""
    user_queries: list[str] = []
    first_user_found = False
    for msg in reversed(messages or []):
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", "")
        elif isinstance(msg, HumanMessage):
            role = "user"
            content = msg.content
        else:
            continue
        if role != "user" or not content:
            continue
        if not first_user_found:
            first_user_found = True
            continue
        user_queries.append(str(content))
        if len(user_queries) >= max_turns:
            break
    user_queries.reverse()
    return "\n".join(f"用户: {query}" for query in user_queries)


async def rewriter_entry_node(state: RewriterAgentState) -> Dict[str, Any]:
    """Build HumanMessage from query and history, clear inherited messages."""
    original_query = state.get("original_query", "")
    messages = state.get("messages", [])
    history = extract_user_query_history(messages)
    if history:
        user_content = (
            "用户历史输入：\n"
            f"{history}\n\n"
            f"当前用户输入：{original_query}\n\n"
            "请输出改写后的问题，每行一个。"
        )
    else:
        user_content = (
            f"当前用户输入：{original_query}\n\n请输出改写后的问题，每行一个。"
        )
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(content=user_content),
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
    workflow.add_node("rewriter_entry", rewriter_entry_node)
    workflow.add_node("rewriter_agent", agent_graph)
    workflow.add_node("rewriter_summary", rewriter_summary_node)
    workflow.set_entry_point("rewriter_entry")
    workflow.add_edge("rewriter_entry", "rewriter_agent")
    workflow.add_edge("rewriter_agent", "rewriter_summary")
    workflow.add_edge("rewriter_summary", END)
    return workflow.compile(checkpointer=checkpointer)


__all__ = [
    "DEFAULT_STANDALONE_QUESTION_REWRITE_PROMPT",
    "RewriterAgentState",
    "build_rewriter_subgraph",
    "extract_user_query_history",
    "rewriter_entry_node",
    "rewriter_summary_node",
]
