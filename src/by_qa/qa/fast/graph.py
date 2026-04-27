"""LangGraph assembly for the fast QA capability."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.fast.nodes import name2node
from by_qa.qa.fast.state import FastQAState
from by_qa.qa.fast.types import NodeNames


async def build_fast_qa_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Build the linear fast QA graph."""
    from by_qa.qa.agents.answer_synthesizer import (  # noqa: E501
        answer_entry_node,
        answer_summary_node,
    )
    from by_qa.qa.agents.standalone_question_rewriter import (  # noqa: E501
        rewriter_entry_node,
        rewriter_summary_node,
    )

    builder = StateGraph(FastQAState, context_schema=QARuntimeContext)
    builder.add_node(NodeNames.REWRITE.value, rewriter_entry_node)
    builder.add_node("rewriter_summary", rewriter_summary_node)
    builder.add_node(NodeNames.RETRIEVE.value, name2node[NodeNames.RETRIEVE])
    builder.add_node(NodeNames.ANSWER.value, answer_entry_node)
    builder.add_node("answer_summary", answer_summary_node)
    builder.add_edge(START, NodeNames.REWRITE.value)
    builder.add_edge(NodeNames.REWRITE.value, "rewriter_summary")
    builder.add_edge("rewriter_summary", NodeNames.RETRIEVE.value)
    builder.add_edge(NodeNames.RETRIEVE.value, NodeNames.ANSWER.value)
    builder.add_edge(NodeNames.ANSWER.value, "answer_summary")
    builder.add_edge("answer_summary", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = ["NodeNames", "build_fast_qa_graph"]
