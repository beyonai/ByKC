"""Instant-QA-specific state definitions."""

import operator
from typing import Annotated, Any, Literal, Optional, TypedDict

from langgraph.graph.message import Messages, add_messages

from by_qa.qa.common.reducers import merge_list_with_mode


class SubQuery(TypedDict):
    """Enhanced sub-query structure for instant QA with hop annotations."""

    query_id: str
    query_text: str
    query_type: Literal["single-hop", "multi-hop"]
    hop_count: int
    dependencies: list[str]
    reasoning_chain: Optional[list[str]]


class RetrievalResult(TypedDict):
    """Retrieval result structure for instant QA."""

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
    """Sub-answer structure for instant QA."""

    sub_query_id: str
    sub_query_text: str
    query_type: Literal["single-hop", "multi-hop"]
    answer: str
    reasoning_chain: list[str]
    intermediate_answers: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    confidence: float
    retrieval_results: list[RetrievalResult]


def overwrite_value(left, right):
    """Reducer that always takes the right value for input parameters."""
    del left
    return right


class SingleHopState(TypedDict):
    """State for single-hop subgraph."""

    sub_query: dict[str, Any]
    cited_indices: list[str]
    result_counter: int
    retrieval_results: Annotated[list[dict[str, Any]], merge_list_with_mode]
    sub_answers: Annotated[list[SubAnswer], merge_list_with_mode]
    messages: Annotated[list, add_messages]


class MultiHopState(TypedDict):
    """State for multi-hop subgraph."""

    sub_query: dict[str, Any]
    messages: Annotated[Messages, add_messages]
    reasoning_plan: list[str]
    current_step: int
    intermediate_results: Annotated[list[dict[str, Any]], operator.add]
    current_hop: int
    intermediate_answers: list[dict[str, Any]]
    reasoning_chain: list[str]
    all_retrieval_results: Annotated[list[dict[str, Any]], merge_list_with_mode]
    sub_answers: Annotated[list[SubAnswer], merge_list_with_mode]
    result_counter: int


class InstantQAState(TypedDict):
    """Instant QA graph state."""

    original_query: str
    sub_queries: list[SubQuery]
    decomposition_metadata: Optional[dict[str, Any]]
    routing_path: Optional[Literal["single_worker_path", "subgraph_parallel_path"]]
    retrieval_results: Annotated[list[RetrievalResult], merge_list_with_mode]
    sub_answers: Annotated[list[SubAnswer], merge_list_with_mode]
    sub_answer_map: Optional[dict[str, SubAnswer]]
    final_answer: str
    citations: list[dict[str, Any]]
    confidence: float
    messages: Annotated[list, add_messages]
    decomposition_time: Optional[float]
    retrieval_time: Optional[float]
    subgraph_execution_time: Optional[float]
    aggregation_time: Optional[float]


InstantSearchState = InstantQAState


__all__ = [
    "InstantSearchState",
    "InstantQAState",
    "MultiHopState",
    "RetrievalResult",
    "SingleHopState",
    "SubAnswer",
    "SubQuery",
    "overwrite_value",
]
