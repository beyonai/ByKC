"""Node names for the instant-search capability."""

from enum import Enum


class NodeNames(Enum):
    """Node names for the instant-search graph."""

    DECOMPOSER = "decomposer"
    ROUTER = "router"
    SUBGRAPH_EXECUTOR = "subgraph_executor"
    SUBANSWER_AGGREGATOR = "subanswer_aggregator"
    CONTEXT_MANAGER = "context_manager"
    SINGLE_HOP_WORKER = "single_hop_worker"
    SINGLE_HOP_ENTRY = "single_hop_entry"
    SINGLE_HOP_AGENT = "single_hop_agent"
    SINGLE_HOP_SUMMARY = "single_hop_summary"
    MULTI_HOP_WORKER = "multi_hop_worker"
    FINAL_ANSWER = "final_answer"
    MULTI_HOP_ENTRY = "multi_hop_entry"
    MULTI_HOP_AGENT = "multi_hop_agent"
    MULTI_HOP_EXIT = "multi_hop_exit"
    MULTI_HOP_SUMMARY = "multi_hop_summary"


__all__ = ["NodeNames"]
