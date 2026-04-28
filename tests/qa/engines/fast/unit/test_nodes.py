"""Tests for fast QA graph nodes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from by_qa.qa.common.config import QARetrievalConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.engines.fast.nodes.retrieve import retrieve_node
from by_qa.qa.engines.fast.state import FastQAState


class FakeLLMService:
    pass


@pytest.mark.asyncio
async def test_retrieve_node_calls_public_search_once(monkeypatch):
    search = AsyncMock(return_value=[{"content": "hit"}])

    class FakeDispatcher:
        def __init__(self, knowledge_bases):
            self.knowledge_bases = knowledge_bases

        async def search_knowledge(self, query, runtime_context):
            return await search(query, runtime_context)

    monkeypatch.setattr(
        "by_qa.qa.engines.fast.nodes.retrieve.ServiceToolDispatcher", FakeDispatcher
    )
    runtime_context = QARuntimeContext(
        retrieval=QARetrievalConfig(knowledge_bases=[]),
        llm_service=FakeLLMService(),
    )

    result = await retrieve_node(
        {
            "original_query": "原问题",
            "sub_queries": [],
            "rewritten_query": "完整问题",
            "messages": [],
            "retrieval_results": [],
            "final_answer": "",
            "rewrite_time": None,
            "retrieval_time": None,
            "answer_time": None,
        },
        runtime=SimpleNamespace(context=runtime_context),
    )

    search.assert_awaited_once_with("完整问题", runtime_context)
    assert result["retrieval_results"] == [{"content": "hit"}]


def test_fast_qa_state_has_sub_queries_field():
    state: FastQAState = {
        "original_query": "test",
        "sub_queries": [{"query_id": "sq_1", "query_text": "test"}],
        "rewritten_query": "test",
        "retrieval_results": [],
        "final_answer": "",
        "messages": [],
        "rewrite_time": None,
        "retrieval_time": None,
        "answer_time": None,
    }
    assert state["sub_queries"] == [{"query_id": "sq_1", "query_text": "test"}]


@pytest.mark.asyncio
async def test_retrieve_node_calls_search_for_each_sub_query(monkeypatch):
    call_log = []

    class FakeDispatcher:
        def __init__(self, knowledge_bases):
            self.knowledge_bases = knowledge_bases

        async def search_knowledge(self, query, runtime_context):  # pylint: disable=unused-argument
            call_log.append(query)
            return [{"content": f"result for {query}", "chunk_id": query}]

    monkeypatch.setattr(
        "by_qa.qa.engines.fast.nodes.retrieve.ServiceToolDispatcher", FakeDispatcher
    )
    runtime_context = QARuntimeContext(
        retrieval=QARetrievalConfig(knowledge_bases=[]),
        llm_service=FakeLLMService(),
    )
    result = await retrieve_node(
        {
            "original_query": "广州和北京的营收各是多少",
            "sub_queries": [
                {"query_id": "sq_1", "query_text": "广州的营收是多少"},
                {"query_id": "sq_2", "query_text": "北京的营收是多少"},
            ],
            "rewritten_query": "广州的营收是多少",
            "retrieval_results": [],
            "final_answer": "",
            "messages": [],
            "rewrite_time": None,
            "retrieval_time": None,
            "answer_time": None,
        },
        runtime=SimpleNamespace(context=runtime_context),
    )
    assert set(call_log) == {"广州的营收是多少", "北京的营收是多少"}
    assert len(result["retrieval_results"]) == 2


@pytest.mark.asyncio
async def test_retrieve_node_deduplicates_by_chunk_id(monkeypatch):
    class FakeDispatcher:
        def __init__(self, knowledge_bases):
            pass

        async def search_knowledge(self, query, runtime_context):  # pylint: disable=unused-argument
            return [{"content": "共同结果", "chunk_id": "dup_chunk"}]

    monkeypatch.setattr(
        "by_qa.qa.engines.fast.nodes.retrieve.ServiceToolDispatcher", FakeDispatcher
    )
    runtime_context = QARuntimeContext(
        retrieval=QARetrievalConfig(knowledge_bases=[]),
        llm_service=FakeLLMService(),
    )
    result = await retrieve_node(
        {
            "original_query": "test",
            "sub_queries": [
                {"query_id": "sq_1", "query_text": "问题A"},
                {"query_id": "sq_2", "query_text": "问题B"},
            ],
            "rewritten_query": "问题A",
            "retrieval_results": [],
            "final_answer": "",
            "messages": [],
            "rewrite_time": None,
            "retrieval_time": None,
            "answer_time": None,
        },
        runtime=SimpleNamespace(context=runtime_context),
    )
    assert len(result["retrieval_results"]) == 1
