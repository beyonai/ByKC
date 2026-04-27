"""Node names for the instant-search capability."""

from enum import Enum


class NodeNames(Enum):
    """Node names for the instant-search main graph."""

    DECOMPOSER = "decomposer"
    ROUTER = "router"
    SUBGRAPH_EXECUTOR = "subgraph_executor"
    SUBANSWER_AGGREGATOR = "subanswer_aggregator"
    CONTEXT_MANAGER = "context_manager"
    SINGLE_HOP_WORKER = "single_hop_worker"
    MULTI_HOP_WORKER = "multi_hop_worker"
    FINAL_ANSWER = "final_answer"


__all__ = ["NodeNames"]
