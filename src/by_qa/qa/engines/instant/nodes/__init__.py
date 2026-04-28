"""Instant-search node implementations owned by the capability package."""

from by_qa.qa.common.context_manager import context_manager_node
from by_qa.qa.engines.instant.nodes.final_answer import final_answer_from_messages_node
from by_qa.qa.engines.instant.nodes.router import router_conditional_edge, router_node
from by_qa.qa.engines.instant.types import NodeNames

name2node = {
    NodeNames.ROUTER: router_node,
    NodeNames.CONTEXT_MANAGER: context_manager_node,
    NodeNames.FINAL_ANSWER: final_answer_from_messages_node,
}

__all__ = [
    "NodeNames",
    "context_manager_node",
    "final_answer_from_messages_node",
    "name2node",
    "router_conditional_edge",
    "router_node",
]
