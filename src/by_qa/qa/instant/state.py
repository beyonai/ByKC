"""Instant-QA-specific state definitions."""

import operator
from typing import Annotated, Any, Literal, Optional, TypedDict

from langgraph.graph.message import Messages, add_messages

from by_qa.qa.agents.single_hop_react import SingleHopState
from by_qa.qa.common.reducers import merge_list_with_mode
from by_qa.qa.common.state import RetrievalResult, SubAnswer


class SubQuery(TypedDict):
    """Enhanced sub-query structure for instant QA with hop annotations."""

    query_id: str
    query_text: str
    query_type: Literal["single-hop", "multi-hop"]
    hop_count: int
    dependencies: list[str]
    reasoning_chain: Optional[list[str]]


def overwrite_value(left, right):
    """Reducer that always takes the right value for input parameters."""
    del left
    return right


class MultiHopState(TypedDict):
    """State for multi-hop subgraph."""

    sub_query: dict[str, Any]
    sub_query_idx: int
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
