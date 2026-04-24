"""LangGraph assembly for the fast QA capability."""

from langgraph.graph import END, START, StateGraph

from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.fast.nodes import name2node
from by_qa.qa.fast.state import FastQAState
from by_qa.qa.fast.types import NodeNames


async def build_fast_qa_graph():
    """Build the linear fast QA graph."""
    builder = StateGraph(FastQAState, context_schema=QARuntimeContext)
    builder.add_node(NodeNames.REWRITE.value, name2node[NodeNames.REWRITE])
    builder.add_node(NodeNames.RETRIEVE.value, name2node[NodeNames.RETRIEVE])
    builder.add_node(NodeNames.ANSWER.value, name2node[NodeNames.ANSWER])
    builder.add_edge(START, NodeNames.REWRITE.value)
    builder.add_edge(NodeNames.REWRITE.value, NodeNames.RETRIEVE.value)
    builder.add_edge(NodeNames.RETRIEVE.value, NodeNames.ANSWER.value)
    builder.add_edge(NodeNames.ANSWER.value, END)
    return builder.compile()


__all__ = ["NodeNames", "build_fast_qa_graph"]
