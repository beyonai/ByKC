"""LangGraph assembly for the fast QA capability."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from by_qa.qa.agents.answer_synthesizer import build_answer_synthesizer_subgraph
from by_qa.qa.agents.standalone_question_rewriter import build_rewriter_subgraph
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.fast.nodes import name2node
from by_qa.qa.fast.state import FastQAState
from by_qa.qa.fast.types import NodeNames
from by_qa.qa.services.llm_service import LLMService


async def build_fast_qa_graph(
    checkpointer: BaseCheckpointSaver | None = None,
    llm_service: LLMService | None = None,
):
    """Build the linear fast QA graph."""
    builder = StateGraph(FastQAState, context_schema=QARuntimeContext)

    if llm_service is not None:
        rewriter_subgraph = await build_rewriter_subgraph(
            llm_service=llm_service, checkpointer=checkpointer
        )
        builder.add_node(NodeNames.REWRITE.value, rewriter_subgraph)
    else:

        async def _passthrough_rewrite(state):
            original_query = state["original_query"]
            return {
                "sub_queries": [{"query_id": "sq_1", "query_text": original_query}],
                "rewritten_query": original_query,
                "rewrite_time": 0.0,
            }

        builder.add_node(NodeNames.REWRITE.value, _passthrough_rewrite)

    builder.add_node(NodeNames.RETRIEVE.value, name2node[NodeNames.RETRIEVE])

    if llm_service is not None:
        answer_subgraph = await build_answer_synthesizer_subgraph(
            llm_service=llm_service, checkpointer=checkpointer
        )
        builder.add_node(NodeNames.ANSWER.value, answer_subgraph)
    else:

        async def _passthrough_answer(state):  # pylint: disable=unused-argument
            return {"final_answer": "", "answer_time": 0.0}

        builder.add_node(NodeNames.ANSWER.value, _passthrough_answer)

    builder.add_edge(START, NodeNames.REWRITE.value)
    builder.add_edge(NodeNames.REWRITE.value, NodeNames.RETRIEVE.value)
    builder.add_edge(NodeNames.RETRIEVE.value, NodeNames.ANSWER.value)
    builder.add_edge(NodeNames.ANSWER.value, END)
    return builder.compile(checkpointer=checkpointer)


__all__ = ["NodeNames", "build_fast_qa_graph"]
