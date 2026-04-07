"""Decomposer agent helpers for the instant-search capability."""

from by_qa.qa.agents.query_decomposer import DecompositionResult, decompose_query


async def decompose_instant_search_query(
    query: str,
    conversation_history: str = "",
    analyze_hop_type: bool = True,
    detect_dependencies: bool = True,
) -> DecompositionResult:
    """Invoke the decomposer agent for instant-search query splitting."""
    return await decompose_query(
        query=query,
        conversation_history=conversation_history,
        analyze_hop_type=analyze_hop_type,
        detect_dependencies=detect_dependencies,
    )


__all__ = ["DecompositionResult", "decompose_instant_search_query"]
