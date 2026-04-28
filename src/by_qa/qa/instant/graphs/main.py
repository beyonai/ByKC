"""Main graph builder for the instant-search capability."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from by_qa.config import get_settings
from by_qa.qa.agents.query_decomposer import build_decomposer_subgraph
from by_qa.qa.agents.subanswer_aggregator import build_aggregator_subgraph
from by_qa.qa.common.config import AgentOverride, QAEngineConfig, QARetrievalConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.instant.graphs.multi_hop import build_multi_hop_subgraph
from by_qa.qa.instant.graphs.single_hop import build_single_hop_subgraph
from by_qa.qa.instant.nodes import NodeNames, name2node
from by_qa.qa.instant.nodes.node_enum import AgentNames
from by_qa.qa.instant.state import InstantSearchState
from by_qa.qa.services.checkpointer_factory import create_checkpointer_async
from by_qa.qa.services.llm_service import LLMService
from by_qa.qa.tools.knowledge_tools import ServiceToolDispatcher


def dispatch_subgraph_workers(state: InstantSearchState):
    """Dispatch worker graphs for the subgraph-parallel execution path."""
    sub_queries = state.get("sub_queries", [])
    original_query = state.get("original_query", "")

    sends = []
    for sub_query_idx, sq in enumerate(sub_queries):
        query_type = sq.get("query_type", "single-hop")
        payload = {
            "sub_query": sq,
            "sub_query_idx": sub_query_idx,
            "original_query": original_query,
            "sub_answers": [],
            "retrieval_results": [],
            "messages": [],
        }
        if query_type == "single-hop":
            sends.append(Send(NodeNames.SINGLE_HOP_WORKER.value, payload))
        else:
            sends.append(Send(NodeNames.MULTI_HOP_WORKER.value, payload))
    return sends


def route_worker_output(state: InstantSearchState) -> str:
    """Route worker outputs based on whether the original query has one sub-query."""
    if len(state.get("sub_queries", [])) == 1:
        return NodeNames.FINAL_ANSWER.value
    return NodeNames.SUBANSWER_AGGREGATOR.value


async def build_instant_search_graph(
    config: QAEngineConfig | dict | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Build the instant-search main graph."""
    settings = get_settings()
    config_data = config or {}

    llm_service = getattr(config_data, "llm_service", None)
    if isinstance(config_data, dict):
        llm_service = llm_service or config_data.get("llm_service")
    if llm_service is None:
        llm_service = LLMService()

    agents: dict[str, AgentOverride] = getattr(config_data, "agents", None) or {}
    if isinstance(config_data, dict):
        agents = agents or config_data.get("agents", {})

    if isinstance(config_data, dict):
        retrieval_raw = config_data.get("retrieval", {})
        retrieval_cfg = (
            QARetrievalConfig(**retrieval_raw)
            if isinstance(retrieval_raw, dict)
            else retrieval_raw
        )
    else:
        retrieval_cfg = getattr(config_data, "retrieval", None)

    kbs = retrieval_cfg.knowledge_bases if retrieval_cfg else []
    dispatcher = ServiceToolDispatcher(kbs)
    dispatcher_tools = dispatcher.build_tools()

    def _agent_override(key: str) -> AgentOverride:
        override = agents.get(key)
        if override is None:
            return AgentOverride()
        if isinstance(override, dict):
            return AgentOverride(**override)
        return override

    decomposer_override = _agent_override(AgentNames.DECOMPOSER)
    aggregator_override = _agent_override(AgentNames.AGGREGATOR)

    builder = StateGraph(InstantSearchState, context_schema=QARuntimeContext)
    decomposer_subgraph = await build_decomposer_subgraph(
        llm_service=llm_service,
        override=decomposer_override,
        checkpointer=checkpointer,
    )
    builder.add_node(NodeNames.DECOMPOSER.value, decomposer_subgraph)
    builder.add_node(NodeNames.ROUTER.value, name2node[NodeNames.ROUTER])
    builder.add_node(NodeNames.FINAL_ANSWER.value, name2node[NodeNames.FINAL_ANSWER])

    single_hop_override = _agent_override(AgentNames.SINGLE_HOP)
    single_hop_override.tools = [*single_hop_override.tools, *dispatcher_tools]
    single_hop_worker = await build_single_hop_subgraph(
        agent_override=single_hop_override,
        llm_service=llm_service,
        checkpointer=checkpointer,
    )

    multi_hop_override = _agent_override(AgentNames.MULTI_HOP)
    multi_hop_override.tools = [*multi_hop_override.tools, *dispatcher_tools]
    multi_hop_summary_override = _agent_override(AgentNames.MULTI_HOP_SUMMARY)
    multi_hop_worker = await build_multi_hop_subgraph(
        agent_override=multi_hop_override,
        summary_override=multi_hop_summary_override,
        llm_service=llm_service,
        checkpointer=checkpointer,
    )

    builder.add_node(NodeNames.SINGLE_HOP_WORKER.value, single_hop_worker)
    builder.add_node(NodeNames.MULTI_HOP_WORKER.value, multi_hop_worker)
    aggregator_subgraph = await build_aggregator_subgraph(
        llm_service=llm_service,
        override=aggregator_override,
        checkpointer=checkpointer,
    )
    builder.add_node(NodeNames.SUBANSWER_AGGREGATOR.value, aggregator_subgraph)

    builder.add_edge(START, NodeNames.DECOMPOSER.value)
    builder.add_edge(NodeNames.DECOMPOSER.value, NodeNames.ROUTER.value)

    builder.add_conditional_edges(
        NodeNames.ROUTER.value,
        dispatch_subgraph_workers,
        [NodeNames.SINGLE_HOP_WORKER.value, NodeNames.MULTI_HOP_WORKER.value],
    )
    builder.add_conditional_edges(
        NodeNames.SINGLE_HOP_WORKER.value,
        route_worker_output,
        {
            NodeNames.FINAL_ANSWER.value: NodeNames.FINAL_ANSWER.value,
            NodeNames.SUBANSWER_AGGREGATOR.value: NodeNames.SUBANSWER_AGGREGATOR.value,
        },
    )
    builder.add_conditional_edges(
        NodeNames.MULTI_HOP_WORKER.value,
        route_worker_output,
        {
            NodeNames.FINAL_ANSWER.value: NodeNames.FINAL_ANSWER.value,
            NodeNames.SUBANSWER_AGGREGATOR.value: NodeNames.SUBANSWER_AGGREGATOR.value,
        },
    )
    builder.add_edge(NodeNames.SUBANSWER_AGGREGATOR.value, END)
    builder.add_edge(NodeNames.FINAL_ANSWER.value, END)

    if checkpointer is None:
        checkpointer = await create_checkpointer_async(settings)
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "NodeNames",
    "build_instant_search_graph",
    "dispatch_subgraph_workers",
    "route_worker_output",
]
