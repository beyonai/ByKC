"""Node names for the instant-search capability."""

from enum import Enum

from by_qa.qa.agents.multi_hop_summarizer import MultiHopSummaryNodeNames
from by_qa.qa.agents.query_decomposer import DecomposerNodeNames
from by_qa.qa.agents.subanswer_aggregator import AggregatorNodeNames
from by_qa.qa.instant.graphs.multi_hop import MultiHopNodeNames
from by_qa.qa.instant.graphs.single_hop import SingleHopNodeNames


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


class AgentNames(str, Enum):
    """Agent names for the instant-search engine configuration."""

    DECOMPOSER = DecomposerNodeNames.AGENT.value
    SINGLE_HOP = SingleHopNodeNames.AGENT.value
    MULTI_HOP = MultiHopNodeNames.AGENT.value
    MULTI_HOP_SUMMARY = MultiHopSummaryNodeNames.AGENT.value
    AGGREGATOR = AggregatorNodeNames.AGENT.value


__all__ = ["AgentNames", "NodeNames"]
