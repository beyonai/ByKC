"""Instant-QA-specific state definitions."""

from typing import Annotated, Any, Literal, Optional, TypedDict

from langgraph.graph.message import add_messages

from by_qa.qa.agents.multi_hop_react import MultiHopState
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
