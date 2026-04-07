"""Router node for instant-search."""

from typing import Dict, Literal

from by_qa.core.logger import info
from by_qa.qa.instant.state import InstantSearchState


async def router_node(state: InstantSearchState) -> Dict:
    sub_queries = state.get("sub_queries", [])
    is_single_query = len(sub_queries) == 1
    if is_single_query:
        query_type = sub_queries[0].get("query_type", "unknown")
        routing_path: Literal["single_worker_path", "subgraph_parallel_path"] = (
            "single_worker_path"
        )
        info(f"[router] Routing to single_worker_path (single {query_type} query)")
    else:
        routing_path = "subgraph_parallel_path"
        query_types = [sq.get("query_type", "unknown") for sq in sub_queries]
        info(
            f"[router] Routing to subgraph_parallel_path ({len(sub_queries)} queries: {query_types})"
        )
    return {"routing_path": routing_path}


def router_conditional_edge(
    state: InstantSearchState,
) -> Literal["single_worker_path", "subgraph_parallel_path"]:
    return state.get("routing_path", "subgraph_parallel_path")
