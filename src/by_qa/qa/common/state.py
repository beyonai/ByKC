"""Shared state types used across QA agents and engines."""

from typing import Any, Literal, Optional, TypedDict


class RetrievalResult(TypedDict):
    """Retrieval result structure for QA."""

    content: str
    source: str
    source_type: Literal["knowledge_base", "web"]
    score: float
    token_count: int
    sub_query_id: str
    sub_query_text: str
    truncated: Optional[bool]
    hop_number: Optional[int]


class SubAnswer(TypedDict):
    """Sub-answer structure for QA."""

    sub_query_id: str
    sub_query_text: str
    query_type: Literal["single-hop", "multi-hop"]
    answer: str
    reasoning_chain: list[str]
    intermediate_answers: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    confidence: float
    retrieval_results: list[RetrievalResult]


__all__ = ["RetrievalResult", "SubAnswer"]
