"""Tests for the fast QA LangGraph assembly."""

import pytest

from by_qa.qa.fast.graph import build_fast_qa_graph
from by_qa.qa.fast.types import NodeNames


@pytest.mark.asyncio
async def test_fast_graph_contains_linear_rewrite_retrieve_answer_nodes():
    graph = await build_fast_qa_graph()
    drawable = graph.get_graph()

    assert NodeNames.REWRITE.value in drawable.nodes
    assert NodeNames.RETRIEVE.value in drawable.nodes
    assert NodeNames.ANSWER.value in drawable.nodes

    edges = {(edge.source, edge.target) for edge in drawable.edges}
    assert (NodeNames.REWRITE.value, NodeNames.RETRIEVE.value) in edges
    assert (NodeNames.RETRIEVE.value, NodeNames.ANSWER.value) in edges
