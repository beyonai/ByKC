"""Retrieve node for the fast QA graph."""

import time
from typing import Any

from by_qa.core.logger import info
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.fast.state import FastQAState
from by_qa.qa.tools.knowledge_tools import ServiceToolDispatcher

try:
    from langgraph.runtime import Runtime
except ImportError:
    Runtime = None  # type: ignore[assignment,misc]


async def retrieve_node(
    state: FastQAState,
    runtime: Runtime[QARuntimeContext] = None,
) -> dict[str, Any]:
    """Run a single knowledge-base search for the rewritten query."""
    if runtime is None or runtime.context is None:
        raise RuntimeError("runtime context is required for retrieve_node")
    start_time = time.time()
    query = state.get("rewritten_query") or state["original_query"]
    dispatcher = ServiceToolDispatcher(runtime.context.retrieval.knowledge_bases)
    results = await dispatcher.search_knowledge(query, runtime.context)
    info("[fast.retrieve] query=%s results=%s", query, len(results))
    return {
        "retrieval_results": results,
        "retrieval_time": time.time() - start_time,
    }


__all__ = ["retrieve_node"]
