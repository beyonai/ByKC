"""Tests for the instant QA engine."""

from unittest.mock import MagicMock, patch

import pytest

from by_qa.qa.common.models import CoreInput, StreamEventType
from by_qa.qa.instant.config import KnowledgeBaseConfig
from by_qa.qa.instant.engine import InstantQAEngine, _extract_search_result_chunks
from by_qa.qa.instant.runtime.operation_registry import OperationType


def _mock_settings():
    settings = type("Settings", (), {})()
    return settings


def test_extract_search_result_chunks_only_reads_artifact():
    with_artifact = type("ToolMessage", (), {"artifact": [{"content": "doc-a"}]})()
    without_artifact = type("ToolMessage", (), {"content": '[{"content": "doc-b"}]'})()

    assert _extract_search_result_chunks(with_artifact) == [{"content": "doc-a"}]
    assert _extract_search_result_chunks(without_artifact) == []


@pytest.mark.asyncio
async def test_stream_search_emits_answer_event_for_final_answer_node():
    with patch("by_qa.qa.instant.engine.get_settings", return_value=_mock_settings()):
        engine = InstantQAEngine()

    mock_graph = MagicMock()

    async def mock_astream_events(*_args, **_kwargs):
        yield {
            "event": "on_chain_end",
            "name": "final_answer",
            "metadata": {"langgraph_node": "final_answer"},
            "run_id": "run-final",
            "parent_ids": ["run-parent"],
            "data": {"output": {"final_answer": "worker answer"}},
        }

    mock_graph.astream_events = mock_astream_events
    engine._graph = mock_graph

    events = []
    async for event in engine.stream_search(CoreInput(query="Test question")):
        events.append(event)

    answer_events = [event for event in events if event.type == StreamEventType.ANSWER]
    assert len(answer_events) == 1
    assert answer_events[0].role == "final_answer"
    assert answer_events[0].data["content"] == "worker answer"


@pytest.mark.asyncio
async def test_stream_search_passes_runtime_context_into_langgraph():
    with patch("by_qa.qa.instant.engine.get_settings", return_value=_mock_settings()):
        engine = InstantQAEngine(
            config={
                "retrieval": {
                    "knowledge_bases": [
                        {
                            "kb_code": "hr-policy",
                            "kb_name": "人力制度知识库",
                            "kb_description": "公司人事制度与流程",
                            "service_name": "kb-search-service-a",
                            "operations": {"search": "/api/v1/knowledgeItems/search"},
                        }
                    ]
                }
            }
        )

    mock_graph = MagicMock()
    captured = {}

    async def mock_astream_events(*_args, **_kwargs):
        captured["context"] = _kwargs.get("context")
        yield {
            "event": "on_chain_end",
            "name": "final_answer",
            "metadata": {"langgraph_node": "final_answer"},
            "run_id": "run-final",
            "parent_ids": [],
            "data": {"output": {"final_answer": "worker answer"}},
        }

    mock_graph.astream_events = mock_astream_events
    engine._graph = mock_graph

    events = []
    async for event in engine.stream_search(CoreInput(query="Test question")):
        events.append(event)

    runtime_context = captured["context"]
    assert runtime_context.retrieval.knowledge_bases == [
        KnowledgeBaseConfig(
            kb_code="hr-policy",
            kb_name="人力制度知识库",
            kb_description="公司人事制度与流程",
            service_name="kb-search-service-a",
            operations={OperationType.SEARCH: "/api/v1/knowledgeItems/search"},
        )
    ]


@pytest.mark.asyncio
async def test_stream_search_sets_prefixed_thread_id_and_request_run_id():
    with patch("by_qa.qa.instant.engine.get_settings", return_value=_mock_settings()):
        engine = InstantQAEngine()

    mock_graph = MagicMock()
    captured = {}

    async def mock_astream_events(initial_state, config=None, **kwargs):
        del initial_state
        del kwargs
        captured["config"] = config
        yield {
            "event": "on_chain_end",
            "name": "final_answer",
            "metadata": {"langgraph_node": "final_answer"},
            "run_id": "run-final",
            "parent_ids": [],
            "data": {"output": {"final_answer": "Answer"}},
        }

    mock_graph.astream_events = mock_astream_events
    engine._graph = mock_graph

    async for unused_event in engine.stream_search(
        CoreInput(query="Test", session_id="session-42", message_id="msg-42")
    ):
        del unused_event

    assert (
        captured["config"]["configurable"]["thread_id"] == "instant_search_session-42"
    )
    assert captured["config"]["metadata"]["message_id"] == "msg-42"
    assert captured["config"]["run_id"] == "msg-42"
