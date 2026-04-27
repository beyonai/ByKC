"""Fast QA graph nodes."""

from by_qa.qa.fast.nodes.retrieve import retrieve_node
from by_qa.qa.fast.types import NodeNames

name2node = {
    NodeNames.RETRIEVE: retrieve_node,
}

__all__ = [
    "name2node",
    "retrieve_node",
]
