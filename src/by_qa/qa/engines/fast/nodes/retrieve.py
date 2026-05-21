"""Retrieve node for the fast QA graph."""

import asyncio
import time
from typing import Any

from by_qa.core.logger import info
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.operation_registry import OperationType
from by_qa.qa.engines.fast.state import FastQAState
from by_qa.qa.tools.knowledge_tools import ServiceToolDispatcher

try:
    from langgraph.runtime import Runtime
except ImportError:
    Runtime = None  # type: ignore[assignment,misc]


async def retrieve_node(
    state: FastQAState,
    runtime: Runtime[QARuntimeContext] = None,
) -> dict[str, Any]:
    """Run parallel knowledge-base searches for all sub-queries."""
    if runtime is None or runtime.context is None:
        raise RuntimeError("runtime context is required for retrieve_node")
    start_time = time.time()
    sub_queries = state.get("sub_queries") or [
        {
            "query_id": "sq_1",
            "query_text": state.get("rewritten_query") or state["original_query"],
        }
    ]
    dispatcher = ServiceToolDispatcher(runtime.context.retrieval.knowledge_bases)
    raw_results = await asyncio.gather(
        *[
            dispatcher.dispatch(
                OperationType.KNOWLEDGE_SEARCH,
                {"query": sq["query_text"]},
                runtime.context,
            )
            for sq in sub_queries
        ],
        return_exceptions=True,
    )
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for sub_result in raw_results:
        if isinstance(sub_result, BaseException):
            continue
        for item in sub_result:
            chunk_id = item.get("chunk_id")
            if chunk_id is not None:
                if chunk_id in seen_ids:
                    continue
                seen_ids.add(chunk_id)
            merged.append(item)
    info(
        "[fast.retrieve] sub_queries=%s total_results=%s", len(sub_queries), len(merged)
    )
    return {
        "retrieval_results": merged,
        "retrieval_time": time.time() - start_time,
    }


__all__ = ["retrieve_node"]
