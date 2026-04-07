"""Instant-search node implementations owned by the capability package."""

from by_qa.qa.instant.nodes.context_manager import context_manager_node
from by_qa.qa.instant.nodes.decomposer import decomposer_node
from by_qa.qa.instant.nodes.final_answer import final_answer_from_messages_node
from by_qa.qa.instant.nodes.node_enum import NodeNames
from by_qa.qa.instant.nodes.router import router_conditional_edge, router_node
from by_qa.qa.instant.nodes.subanswer_aggregator import subanswer_aggregator_node

name2node = {
    NodeNames.DECOMPOSER: decomposer_node,
    NodeNames.ROUTER: router_node,
    NodeNames.SUBANSWER_AGGREGATOR: subanswer_aggregator_node,
    NodeNames.CONTEXT_MANAGER: context_manager_node,
    NodeNames.FINAL_ANSWER: final_answer_from_messages_node,
}

__all__ = [
    "NodeNames",
    "context_manager_node",
    "decomposer_node",
    "final_answer_from_messages_node",
    "name2node",
    "router_conditional_edge",
    "router_node",
    "subanswer_aggregator_node",
]
