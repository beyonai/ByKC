"""Tests for the fast QA LangGraph assembly."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from by_qa.qa.fast.graph import build_fast_qa_graph
from by_qa.qa.fast.types import NodeNames


def _mock_llm_service():
    llm_service = MagicMock()
    fake_model = MagicMock()
    fake_model.bind = MagicMock(return_value=fake_model)
    llm_service._get_streaming_model = AsyncMock(return_value=fake_model)
    return llm_service


@pytest.mark.asyncio
async def test_fast_graph_contains_linear_rewrite_retrieve_answer_nodes():
    graph = await build_fast_qa_graph(llm_service=_mock_llm_service())
    drawable = graph.get_graph()

    assert NodeNames.REWRITE.value in drawable.nodes
    assert NodeNames.RETRIEVE.value in drawable.nodes
    assert NodeNames.ANSWER.value in drawable.nodes

    edges = {(edge.source, edge.target) for edge in drawable.edges}
    assert (NodeNames.REWRITE.value, NodeNames.RETRIEVE.value) in edges
    assert (NodeNames.RETRIEVE.value, NodeNames.ANSWER.value) in edges
