"""Tests for the fast QA engine."""

from unittest.mock import MagicMock, patch

import pytest

from by_qa.qa.common.config import KnowledgeBaseConfig
from by_qa.qa.common.models import CoreInput, StreamEventType
from by_qa.qa.common.operation_registry import OperationType
from by_qa.qa.engines.fast.engine import FastQAEngine


def _mock_settings():
    return type("Settings", (), {})()


@pytest.mark.asyncio
async def test_stream_search_emits_search_chunks_and_answer_events():
    with patch(
        "by_qa.qa.common.base_engine.get_settings", return_value=_mock_settings()
    ):
        engine = FastQAEngine()

    mock_graph = MagicMock()

    async def mock_astream_events(*_args, **_kwargs):
        yield {
            "event": "on_chain_end",
            "name": "retrieve",
            "metadata": {"langgraph_node": "retrieve"},
            "run_id": "run-retrieve",
            "parent_ids": [],
            "data": {
                "output": {
                    "retrieval_results": [
                        {"content": "报销需要审批", "source": "/policy.md"}
                    ]
                }
            },
        }
        yield {
            "event": "on_chain_end",
            "name": "answer",
            "metadata": {"langgraph_node": "answer"},
            "run_id": "run-answer",
            "parent_ids": ["run-retrieve"],
            "data": {"output": {"final_answer": "需要提交审批。"}},
        }

    mock_graph.astream_events = mock_astream_events
    engine._graph = mock_graph

    events = [
        event async for event in engine.stream_search(CoreInput(query="怎么报销发票"))
    ]

    search_events = [
        event for event in events if event.type == StreamEventType.SEARCH_RESULT_CHUNKS
    ]
    answer_events = [event for event in events if event.type == StreamEventType.ANSWER]

    assert len(search_events) == 1
    assert search_events[0].role == "knowledge_search"
    assert search_events[0].data["chunks"] == [
        {"content": "报销需要审批", "source": "/policy.md"}
    ]
    assert len(answer_events) == 1
    assert answer_events[0].role == "answer"
    assert answer_events[0].data["content"] == "需要提交审批。"


@pytest.mark.asyncio
async def test_stream_search_passes_runtime_context_and_fast_thread_id():
    with patch(
        "by_qa.qa.common.base_engine.get_settings", return_value=_mock_settings()
    ):
        engine = FastQAEngine(
            config={
                "retrieval": {
                    "knowledge_bases": [
                        {
                            "kb_code": "kb1",
                            "kb_name": "KB1",
                            "service_name": "svc-a",
                            "operations": {
                                "knowledgeSearch": "/api/v1/knowledgeItems/search"
                            },
                        }
                    ]
                }
            }
        )

    mock_graph = MagicMock()
    captured = {}

    async def mock_astream_events(initial_state, config=None, **kwargs):
        captured["initial_state"] = initial_state
        captured["config"] = config
        captured["context"] = kwargs.get("context")
        yield {
            "event": "on_chain_end",
            "name": "answer",
            "metadata": {"langgraph_node": "answer"},
            "run_id": "run-answer",
            "parent_ids": [],
            "data": {"output": {"final_answer": "Answer"}},
        }

    mock_graph.astream_events = mock_astream_events
    engine._graph = mock_graph

    async for event in engine.stream_search(
        CoreInput(query="Test", session_id="s1", message_id="m1")
    ):
        del event

    assert captured["initial_state"]["sub_queries"] == []
    assert captured["initial_state"]["original_query"] == "Test"
    assert captured["config"]["configurable"]["thread_id"] == "fast_qa_s1"
    assert captured["config"]["run_id"] == "m1"
    assert captured["context"].retrieval.knowledge_bases == [
        KnowledgeBaseConfig(
            kb_code="kb1",
            kb_name="KB1",
            service_name="svc-a",
            operations={
                OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"
            },
        )
    ]
