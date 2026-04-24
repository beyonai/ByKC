"""State definitions for the fast QA graph."""

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class FastQAState(TypedDict):
    """State for the linear fast QA graph."""

    original_query: str
    rewritten_query: str
    retrieval_results: list[dict[str, Any]]
    final_answer: str
    messages: Annotated[list, add_messages]
    rewrite_time: float | None
    retrieval_time: float | None
    answer_time: float | None


__all__ = ["FastQAState"]
