"""Fast QA graph nodes."""

from by_qa.qa.fast.nodes.answer import answer_node
from by_qa.qa.fast.nodes.retrieve import retrieve_node
from by_qa.qa.fast.nodes.rewrite import rewrite_node
from by_qa.qa.fast.types import NodeNames

name2node = {
    NodeNames.REWRITE: rewrite_node,
    NodeNames.RETRIEVE: retrieve_node,
    NodeNames.ANSWER: answer_node,
}


__all__ = [
    "answer_node",
    "name2node",
    "retrieve_node",
    "rewrite_node",
]
