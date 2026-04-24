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
    """Rewrite the current query and split parallel sub-questions."""
    start_time = time.time()
    original_query = state["original_query"]
    llm_service = runtime.context.llm_service if runtime and runtime.context else None
    if llm_service is None:
        sub_queries = [{"query_id": "sq_1", "query_text": original_query}]
        return {
            "sub_queries": sub_queries,
            "rewritten_query": original_query,
            "rewrite_time": time.time() - start_time,
        }
    history = extract_user_query_history(state.get("messages", []))
    try:
        texts = await StandaloneQuestionRewriterAgent(
            llm_service=llm_service
        ).rewrite_and_split(original_query, history)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        error("[fast.rewrite] failed: %s", exc)
        texts = [original_query]
    sub_queries = [
        {"query_id": f"sq_{i + 1}", "query_text": t} for i, t in enumerate(texts)
    ]
    info(
        "[fast.rewrite] query=%s sub_queries=%s",
        original_query,
        [sq["query_text"] for sq in sub_queries],
    )
    return {
        "sub_queries": sub_queries,
        "rewritten_query": sub_queries[0]["query_text"],
        "rewrite_time": time.time() - start_time,
    }


__all__ = ["extract_user_query_history", "rewrite_node"]
