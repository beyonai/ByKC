"""Fallback answer node for workers stopped before the graph recursion limit."""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from by_qa.core.logger import info, warning
from by_qa.core.model_config import LLMModelProfile
from by_qa.qa.common.context_manager import (
    build_context_for_llm,
    calculate_available_tokens,
    truncate_retrieval_results_round_robin,
)
from by_qa.qa.common.state import SubAnswer
from by_qa.qa.services.llm_service import LLMService

DEFAULT_RECURSION_FALLBACK_PROMPT = """You are a rigorous knowledge base QA assistant.

The retrieval agent was stopped because it had run for too long. Produce the best useful answer possible from only the partial material provided.

Requirements:
- Answer the user's question directly and concisely.
- Treat retrieval results and completed intermediate answers as evidence.
- Agent working replies may help organize the evidence, but do not treat unsupported claims in them as facts.
- Clearly state what remains uncertain or unsupported when the evidence is incomplete.
- Do not mention internal recursion limits, agent failures, fallback logic, or implementation details.
- Do not fabricate information.
- Do not output citation identifiers or a Sources section.
- Respond in the same language as the user's question."""


def should_use_recursion_fallback(state: dict[str, Any]) -> bool:
    """Return whether the loop guard requested fallback synthesis."""
    return bool(state.get("recursion_fallback_required"))


def _extract_agent_replies(messages: list[Any]) -> list[str]:
    replies: list[str] = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        content = message.content
        if isinstance(content, str) and content.strip():
            replies.append(content.strip())
    return replies


def _extract_sources(retrieval_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources = []
    seen = set()
    for result in retrieval_results:
        key = (result.get("source", ""), result.get("content", "")[:80])
        if key in seen:
            continue
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


def _calculate_confidence(retrieval_results: list[dict[str, Any]]) -> float:
    if not retrieval_results:
        return 0.0
    scores = [result.get("score", 0.0) for result in retrieval_results[:3]]
    return sum(scores) / len(scores) if scores else 0.0


def _deterministic_fallback_answer(
    query_text: str,
    retrieval_results: list[dict[str, Any]],
    agent_replies: list[str],
) -> str:
    """Return a usable answer even if the final fallback model call also fails."""
    if agent_replies:
        return agent_replies[-1]

    contents = [
        result.get("content", "").strip()
        for result in retrieval_results[:3]
        if result.get("content", "").strip()
    ]
    is_chinese = any("\u4e00" <= char <= "\u9fff" for char in query_text)
    if contents:
        prefix = (
            "基于目前获取到的有限信息，暂时只能确认："
            if is_chinese
            else "Based on the limited information retrieved so far:"
        )
        return prefix + "\n\n" + "\n".join(f"- {item}" for item in contents)
    return (
        "目前获取到的信息不足，暂时无法给出可靠答案。"
        if is_chinese
        else "The information retrieved so far is insufficient for a reliable answer."
    )


async def _bounded_retrieval_results(
    retrieval_results: list[dict[str, Any]], llm_service: LLMService
) -> list[dict[str, Any]]:
    model_config = await llm_service.get_model_config(LLMModelProfile.STANDARD)
    if model_config.max_model_len is None:
        return retrieval_results
    available_tokens = calculate_available_tokens(model_config.max_model_len)
    bounded, _ = truncate_retrieval_results_round_robin(
        retrieval_results, available_tokens
    )
    return bounded


def build_recursion_fallback_node(
    *, llm_service: LLMService, query_type: Literal["single-hop", "multi-hop"]
):
    """Build a LangGraph node that synthesizes a partial-evidence answer."""

    async def recursion_fallback_node(state: dict[str, Any]) -> dict[str, Any]:
        sub_query = state.get("sub_query", {})
        query_text = sub_query.get("query_text", "")
        retrieval_results = state.get("retrieval_results", [])
        try:
            bounded_results = await _bounded_retrieval_results(
                retrieval_results, llm_service
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            warning(
                "[recursion_fallback] Context bounding failed; using raw results: %s",
                exc,
            )
            bounded_results = retrieval_results
        agent_replies = _extract_agent_replies(state.get("messages", []))
        intermediate_results = state.get("intermediate_results", [])
        context = build_context_for_llm(bounded_results)

        user_prompt = (
            f"User question:\n{query_text}\n\n"
            f"Retrieved evidence collected so far:\n{context}\n\n"
            "Completed intermediate answers:\n"
            f"{json.dumps(intermediate_results, ensure_ascii=False)}\n\n"
            "Agent working replies:\n"
            f"{json.dumps(agent_replies, ensure_ascii=False)}\n\n"
            "Generate the best evidence-grounded answer available now."
        )
        try:
            model = await llm_service._get_streaming_model(LLMModelProfile.STANDARD)
            response = await model.ainvoke(
                [
                    SystemMessage(content=DEFAULT_RECURSION_FALLBACK_PROMPT),
                    HumanMessage(content=user_prompt),
                ]
            )
            answer = (
                response.content
                if isinstance(response.content, str)
                else str(response.content)
            )
            if not answer.strip():
                answer = _deterministic_fallback_answer(
                    query_text, bounded_results, agent_replies
                )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            warning(
                "[recursion_fallback] Model synthesis failed; using deterministic answer: %s",
                exc,
            )
            answer = _deterministic_fallback_answer(
                query_text, bounded_results, agent_replies
            )
        sub_answer = SubAnswer(
            sub_query_id=sub_query.get("query_id", "unknown"),
            sub_query_text=query_text,
            query_type=query_type,
            answer=answer,
            reasoning_chain=state.get("reasoning_chain", []),
            intermediate_answers=intermediate_results,
            sources=_extract_sources(retrieval_results),
            confidence=_calculate_confidence(retrieval_results),
            retrieval_results=retrieval_results,
        )
        info(
            "[recursion_fallback] Generated partial-evidence answer for query=%s",
            query_text,
        )
        return {
            "sub_answers": [sub_answer],
            "messages": [AIMessage(content=answer)],
        }

    recursion_fallback_node.__name__ = "recursion_fallback"
    return recursion_fallback_node


__all__ = [
    "DEFAULT_RECURSION_FALLBACK_PROMPT",
    "_deterministic_fallback_answer",
    "build_recursion_fallback_node",
    "should_use_recursion_fallback",
]
