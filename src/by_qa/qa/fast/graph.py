"""LangGraph assembly for the fast QA capability."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from by_qa.qa.agents.answer_synthesizer import build_answer_synthesizer_subgraph
from by_qa.qa.agents.standalone_question_rewriter import build_rewriter_subgraph
from by_qa.qa.common.config import AgentOverride
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.fast.nodes import name2node
from by_qa.qa.fast.state import FastQAState
from by_qa.qa.fast.types import AgentNames, NodeNames
from by_qa.qa.services.llm_service import LLMService


async def build_fast_qa_graph(
    *,
    llm_service: LLMService,
    agents: dict[str, AgentOverride] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Build the linear fast QA graph."""
    agents = agents or {}

    def _agent_override(key: str) -> AgentOverride:
        override = agents.get(key)
        if override is None:
            return AgentOverride()
        if isinstance(override, dict):
            return AgentOverride(**override)
        return override

    rewriter_override = _agent_override(AgentNames.REWRITER)
    answer_override = _agent_override(AgentNames.ANSWER)

    builder = StateGraph(FastQAState, context_schema=QARuntimeContext)

    rewriter_subgraph = await build_rewriter_subgraph(
        llm_service=llm_service,
        override=rewriter_override,
        checkpointer=checkpointer,
    )
    builder.add_node(NodeNames.REWRITE.value, rewriter_subgraph)
    builder.add_node(NodeNames.RETRIEVE.value, name2node[NodeNames.RETRIEVE])
    answer_subgraph = await build_answer_synthesizer_subgraph(
        llm_service=llm_service,
        override=answer_override,
        checkpointer=checkpointer,
    )
    builder.add_node(NodeNames.ANSWER.value, answer_subgraph)

    builder.add_edge(START, NodeNames.REWRITE.value)
    builder.add_edge(NodeNames.REWRITE.value, NodeNames.RETRIEVE.value)
    builder.add_edge(NodeNames.RETRIEVE.value, NodeNames.ANSWER.value)
    builder.add_edge(NodeNames.ANSWER.value, END)
    return builder.compile(checkpointer=checkpointer)


__all__ = ["NodeNames", "build_fast_qa_graph"]
