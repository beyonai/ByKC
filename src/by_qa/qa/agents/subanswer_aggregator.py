"""Sub-answer aggregator agent using LangGraph create_agent."""

import time
from typing import Annotated, Any, Dict, Optional, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from by_qa.core.logger import info
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.messages import agent_metadata
from by_qa.qa.common.reducers import merge_list_with_mode
from by_qa.qa.services.llm_service import LLMService

SYSTEM_PROMPT = """你是一个专业的回答整合专家。你的任务是基于多个子查询的答案，生成对用户原始问题的完整回答。

## 核心要求

1. **综合回答**：整合所有子查询的答案，生成对原始问题的完整回答
2. **逻辑连贯**：确保回答逻辑清晰，各部分之间过渡自然
3. **Markdown格式**：直接输出Markdown格式的回复，不要输出JSON
4. **不添加引用**：不需要标注引用来源，专注于回答内容本身

## 回答结构

请根据子查询的数量和类型，灵活组织回答结构：

- **单个子查询**：直接呈现该子查询的答案
- **多个子查询**：
  - 如果子查询是并列关系（如"A和B的营收"），分别呈现后再给出综合结论
  - 如果子查询有依赖关系，按逻辑顺序呈现
  - 对于multi-hop子查询，简要说明推理过程

## 注意事项

1. 保持客观，不要添加子查询答案中没有的信息
2. 如果子查询答案之间有冲突，请指出并给出最可能的结论
3. 如果某些子查询未能找到答案，说明该部分信息缺失
4. 回答应该直接回应用户的原始问题"""


def _build_sub_answers_context(sub_answers: list[dict]) -> str:
    """Format sub-answers into a context string for the aggregator."""
    if not sub_answers:
        return "未找到子查询答案。"

    parts: list[str] = []
    for index, sub_answer in enumerate(sub_answers, 1):
        query_text = sub_answer.get("sub_query_text", f"子查询 {index}")
        query_type = sub_answer.get("query_type", "single-hop")
        answer = sub_answer.get("answer", "")
        reasoning_chain = sub_answer.get("reasoning_chain", [])
        confidence = sub_answer.get("confidence", 0.0)
        part = (
            f"## 子查询 {index}: {query_text}\n"
            f"类型: {query_type}\n"
            f"置信度: {confidence:.2f}\n\n"
            f"### 答案\n{answer}\n"
        )
        if reasoning_chain:
            part += "\n### 推理过程\n"
            for step in reasoning_chain:
                part += f"- {step}\n"
        parts.append(part)
    return "\n\n---\n\n".join(parts)


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
            "final_answer": "未能生成答案",
            "aggregation_time": 0.0,
        }

    sub_answers_context = _build_sub_answers_context(sub_answers)
    user_content = (
        f"用户原始问题：{original_query}\n\n"
        f"子查询答案：\n{sub_answers_context}\n\n"
        "请基于以上子查询答案，生成对用户原始问题的完整回答。"
    )
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(
                content=user_content,
                additional_kwargs=agent_metadata("subanswer_aggregator"),
            ),
        ],
        "aggregation_time": time.time(),
    }


async def aggregator_summary_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Summary node: extract the final answer from the agent response."""
    if state.get("final_answer") == "未能生成答案":
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
    if state.get("final_answer") == "未能生成答案":
        return "aggregator_summary"
    return "aggregator_agent"


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
    workflow.add_node("aggregator_entry", aggregator_entry_node)
    workflow.add_node("aggregator_agent", agent_graph)
    workflow.add_node("aggregator_summary", aggregator_summary_node)
    workflow.set_entry_point("aggregator_entry")
    workflow.add_conditional_edges(
        "aggregator_entry",
        _route_after_entry,
        {
            "aggregator_agent": "aggregator_agent",
            "aggregator_summary": "aggregator_summary",
        },
    )
    workflow.add_edge("aggregator_agent", "aggregator_summary")
    workflow.add_edge("aggregator_summary", END)
    return workflow.compile(checkpointer=checkpointer)


__all__ = [
    "AggregatorAgentState",
    "SYSTEM_PROMPT",
    "_build_sub_answers_context",
    "aggregator_entry_node",
    "aggregator_summary_node",
    "build_aggregator_subgraph",
]
