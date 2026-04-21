"""Main graph builder for the instant-search capability."""

from dataclasses import fields

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from by_qa.config import get_settings
from by_qa.qa.instant.config import (
    InstantSearchAgentConfig,
    InstantSearchRetrievalConfig,
)
from by_qa.qa.instant.graphs.multi_hop import build_multi_hop_subgraph
from by_qa.qa.instant.graphs.single_hop import build_single_hop_subgraph
from by_qa.qa.instant.nodes import NodeNames, name2node
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext
from by_qa.qa.instant.runtime.dispatcher import ServiceToolDispatcher
from by_qa.qa.instant.runtime.factories import wrap_node
from by_qa.qa.instant.state import InstantSearchState
from by_qa.qa.services.checkpointer_factory import create_checkpointer_async
from by_qa.qa.services.llm_service import LLMService


def dispatch_subgraph_workers(state: InstantSearchState):
    """Dispatch worker graphs for the subgraph-parallel execution path."""
    sub_queries = state.get("sub_queries", [])
    original_query = state.get("original_query", "")

    sends = []
    for sq in sub_queries:
        query_type = sq.get("query_type", "single-hop")
        payload = {
            "sub_query": sq,
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
    config: InstantSearchAgentConfig | dict | None = None,
):
    """Build the instant-search main graph."""
    settings = get_settings()
    config_data = config or {}
    node_callbacks = getattr(config_data, "node_callbacks", None)
    if node_callbacks is None and isinstance(config_data, dict):
        node_callbacks = config_data.get("node_callbacks", {})
    node_callbacks = node_callbacks or {}

    llm_service = getattr(config_data, "llm_service", None)
    if isinstance(config_data, dict):
        llm_service = llm_service or config_data.get("llm_service")
    if llm_service is None:
        llm_service = LLMService()

    tool_providers = getattr(config_data, "tool_providers", None)
    if isinstance(config_data, dict):
        tool_providers = tool_providers or config_data.get("tool_providers", {})
    tool_providers = tool_providers or {}

    if isinstance(config_data, dict):
        retrieval_raw = config_data.get("retrieval", {})
        retrieval_cfg = (
            InstantSearchRetrievalConfig(**retrieval_raw)
            if isinstance(retrieval_raw, dict)
            else retrieval_raw
        )
    else:
        retrieval_cfg = getattr(config_data, "retrieval", None)

    kbs = retrieval_cfg.knowledge_bases if retrieval_cfg else []
    dispatcher = ServiceToolDispatcher(kbs)
    dispatcher_tools = dispatcher.build_tools()

    merged_providers = dict(tool_providers)
    for hop in ("single_hop", "multi_hop"):
        existing = merged_providers.get(hop)
        if existing:
            merged_providers[hop] = lambda e=existing, dt=dispatcher_tools: e() + dt
        else:
            merged_providers[hop] = lambda dt=dispatcher_tools: dt

    def _node(name: NodeNames):
        return wrap_node(name.value, name2node[name], node_callbacks.get(name.value))

    builder = StateGraph(InstantSearchState, context_schema=InstantSearchRuntimeContext)
    builder.add_node(NodeNames.DECOMPOSER.value, _node(NodeNames.DECOMPOSER))
    builder.add_node(NodeNames.ROUTER.value, _node(NodeNames.ROUTER))
    builder.add_node(NodeNames.FINAL_ANSWER.value, _node(NodeNames.FINAL_ANSWER))

    config_with_providers = (
        dict(config_data)
        if isinstance(config_data, dict)
        else {f.name: getattr(config_data, f.name) for f in fields(config_data)}
    )
    config_with_providers["tool_providers"] = merged_providers

    single_hop_worker = await build_single_hop_subgraph(
        config=config_with_providers, llm_service=llm_service
    )
    multi_hop_worker = await build_multi_hop_subgraph(
        config=config_with_providers, llm_service=llm_service
    )
    builder.add_node(NodeNames.SINGLE_HOP_WORKER.value, single_hop_worker)
    builder.add_node(NodeNames.MULTI_HOP_WORKER.value, multi_hop_worker)
    builder.add_node(
        NodeNames.SUBANSWER_AGGREGATOR.value, _node(NodeNames.SUBANSWER_AGGREGATOR)
    )

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

    checkpointer = await create_checkpointer_async(settings)
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "NodeNames",
    "build_instant_search_graph",
    "dispatch_subgraph_workers",
    "route_worker_output",
]
