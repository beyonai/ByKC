"""Compiled worker-graph accessors for the instant-search capability."""

from by_qa.qa.instant.graphs.multi_hop import build_multi_hop_subgraph
from by_qa.qa.instant.graphs.single_hop import build_single_hop_subgraph

_single_hop_subgraph = None
_multi_hop_subgraph = None


async def get_single_hop_worker(config=None):
    """Return the compiled single-hop subgraph used as a worker graph."""
    global _single_hop_subgraph
    if _single_hop_subgraph is None:
        _single_hop_subgraph = await build_single_hop_subgraph(config=config)
    return _single_hop_subgraph


async def get_multi_hop_worker(config=None):
    """Return the compiled multi-hop subgraph used as a worker graph."""
    global _multi_hop_subgraph
    if _multi_hop_subgraph is None:
        _multi_hop_subgraph = await build_multi_hop_subgraph(config=config)
    return _multi_hop_subgraph


__all__ = ["get_multi_hop_worker", "get_single_hop_worker"]
