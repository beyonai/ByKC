"""Decomposer node for instant-search graph."""

import time
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage

from by_qa.core.logger import info
from by_qa.qa.agents.query_decomposer import DecompositionResult, QueryDecomposerAgent
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.instant.state import InstantSearchState

try:
    from langgraph.runtime import Runtime
except ImportError:
    Runtime = None


def _extract_user_queries(messages: List[Any], max_turns: int = 5) -> str:
    if not messages:
        return ""
    user_queries = []
    first_user_found = False
    for msg in reversed(messages):
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", "")
        elif isinstance(msg, HumanMessage):
            role = "user"
            content = msg.content
        else:
            continue
        if role == "user" and content:
            if not first_user_found:
                first_user_found = True
                continue
            if len(content) > 200:
                content = content[:200] + "..."
            user_queries.append(content)
            if len(user_queries) >= max_turns:
                break
    user_queries.reverse()
    return "\n".join(f"用户: {q}" for q in user_queries)


async def decomposer_node(
    state: InstantSearchState, runtime: Runtime[QARuntimeContext] = None
) -> Dict[str, Any]:
    start_time = time.time()
    original_query = state["original_query"]
    messages = state.get("messages", [])
    info(f"[decomposer] Decomposing query: {original_query}")
    conversation_history = _extract_user_queries(messages, max_turns=5)
    if conversation_history:
        info(
            f"[decomposer] Using {len(conversation_history.split(chr(10)))} previous user queries"
        )
    llm_service = runtime.context.llm_service if runtime and runtime.context else None
    if llm_service is None:
        raise RuntimeError(
            "llm_service is required in runtime context for decomposer_node"
        )
    result: DecompositionResult = await QueryDecomposerAgent(
        llm_service=llm_service
    ).decompose(
        query=original_query,
        conversation_history=conversation_history,
        analyze_hop_type=True,
        detect_dependencies=True,
    )
    decomposition_time = time.time() - start_time
    single_hop_count = sum(
        1 for sq in result.sub_queries if sq.query_type == "single-hop"
    )
    multi_hop_count = sum(
        1 for sq in result.sub_queries if sq.query_type == "multi-hop"
    )
    info(
        f"[decomposer] Generated {len(result.sub_queries)} sub-queries "
        f"({single_hop_count} single-hop, {multi_hop_count} multi-hop) "
        f"in {decomposition_time:.2f}s"
    )
    sub_queries_dicts = [
        {
            "query_id": sq.query_id,
            "query_text": sq.query_text,
            "query_type": sq.query_type,
            "hop_count": sq.hop_count,
            "dependencies": sq.dependencies,
            "reasoning_chain": sq.reasoning_chain or [],
        }
        for sq in result.sub_queries
    ]
    return {
        "sub_queries": sub_queries_dicts,
        "decomposition_metadata": result.metadata,
        "decomposition_time": decomposition_time,
        "messages": [
            {
                "role": "assistant",
                "content": f"已将问题分解为 {len(result.sub_queries)} 个子查询 "
                f"({single_hop_count} 单跳, {multi_hop_count} 多跳)",
            }
        ],
    }
