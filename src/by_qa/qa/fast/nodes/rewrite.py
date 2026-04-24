"""Rewrite node for the fast QA graph."""

import time
from typing import Any

from langchain_core.messages import HumanMessage

from by_qa.core.logger import error, info
from by_qa.qa.agents.standalone_question_rewriter import StandaloneQuestionRewriterAgent
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.fast.state import FastQAState

try:
    from langgraph.runtime import Runtime
except ImportError:
    Runtime = None  # type: ignore[assignment,misc]


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


async def rewrite_node(
    state: FastQAState,
    runtime: Runtime[QARuntimeContext] = None,
) -> dict[str, Any]:
    """Rewrite the current query into a standalone retrieval query."""
    start_time = time.time()
    original_query = state["original_query"]
    llm_service = runtime.context.llm_service if runtime and runtime.context else None
    if llm_service is None:
        return {
            "rewritten_query": original_query,
            "rewrite_time": time.time() - start_time,
        }
    history = extract_user_query_history(state.get("messages", []))
    try:
        rewritten_query = await StandaloneQuestionRewriterAgent(
            llm_service=llm_service
        ).rewrite(original_query, history)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        error("[fast.rewrite] failed: %s", exc)
        rewritten_query = original_query
    info("[fast.rewrite] query=%s rewritten=%s", original_query, rewritten_query)
    return {
        "rewritten_query": rewritten_query,
        "rewrite_time": time.time() - start_time,
    }


__all__ = ["extract_user_query_history", "rewrite_node"]
