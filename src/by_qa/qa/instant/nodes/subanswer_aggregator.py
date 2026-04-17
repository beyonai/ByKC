"""Sub-answer aggregator node for instant-search."""

import time
from typing import Any, Dict

from by_qa.core.logger import info
from by_qa.qa.agents.subanswer_aggregator import SubAnswerAggregatorAgent
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext
from by_qa.qa.instant.state import InstantSearchState

try:
    from langgraph.runtime import Runtime
except ImportError:
    Runtime = None


async def subanswer_aggregator_node(
    state: InstantSearchState, runtime: Runtime[InstantSearchRuntimeContext] = None
) -> Dict[str, Any]:
    start_time = time.time()
    sub_answers = state.get("sub_answers", [])
    original_query = state.get("original_query", "")
    info(f"[subanswer_aggregator] Aggregating {len(sub_answers)} sub-answers")
    if not sub_answers:
        return {
            "final_answer": "未能生成答案",
            "citations": [],
            "confidence": 0.0,
            "aggregation_time": time.time() - start_time,
        }
    llm_service = runtime.context.llm_service if runtime and runtime.context else None
    if llm_service is None:
        raise RuntimeError(
            "llm_service is required in runtime context for subanswer_aggregator_node"
        )
    aggregator = SubAnswerAggregatorAgent(llm_service=llm_service)
    final_answer = await aggregator.aggregate(
        original_query=original_query, sub_answers=sub_answers
    )
    aggregation_time = time.time() - start_time
    info(f"[subanswer_aggregator] Aggregation completed in {aggregation_time:.2f}s ")
    return {
        "final_answer": final_answer,
        "aggregation_time": aggregation_time,
    }
