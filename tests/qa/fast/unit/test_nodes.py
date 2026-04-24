"""Tests for fast QA graph nodes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from by_qa.qa.agents.standalone_question_rewriter import StandaloneQuestionRewriterAgent
from by_qa.qa.common.config import QARetrievalConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.fast.nodes.answer import answer_node
from by_qa.qa.fast.nodes.retrieve import retrieve_node
from by_qa.qa.fast.nodes.rewrite import rewrite_node
from by_qa.qa.fast.state import FastQAState


class FakeLLMService:
    pass


@pytest.mark.asyncio
async def test_rewrite_node_falls_back_to_original_query_without_llm_service():
    result = await rewrite_node(
        {
            "original_query": "广州呢",
            "sub_queries": [],
            "messages": [],
            "rewritten_query": "",
            "retrieval_results": [],
            "final_answer": "",
            "rewrite_time": None,
            "retrieval_time": None,
            "answer_time": None,
        },
        runtime=None,
    )

    assert result["sub_queries"] == [{"query_id": "sq_1", "query_text": "广州呢"}]
    assert result["rewritten_query"] == "广州呢"
    assert result["rewrite_time"] is not None


@pytest.mark.asyncio
async def test_retrieve_node_calls_public_search_once(monkeypatch):
    search = AsyncMock(return_value=[{"content": "hit"}])

    class FakeDispatcher:
        def __init__(self, knowledge_bases):
            self.knowledge_bases = knowledge_bases

        async def search_knowledge(self, query, runtime_context):
            return await search(query, runtime_context)

    monkeypatch.setattr(
        "by_qa.qa.fast.nodes.retrieve.ServiceToolDispatcher", FakeDispatcher
    )
    runtime_context = QARuntimeContext(
        retrieval=QARetrievalConfig(knowledge_bases=[]),
        llm_service=FakeLLMService(),
    )

    result = await retrieve_node(
        {
            "original_query": "原问题",
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


@pytest.mark.asyncio
async def test_answer_node_uses_answer_synthesizer(monkeypatch):
    synthesize = AsyncMock(return_value="最终答案")

    class FakeAnswerAgent:
        def __init__(self, llm_service):
            self.llm_service = llm_service

        async def answer(self, **kwargs):
            return await synthesize(**kwargs)

    monkeypatch.setattr(
        "by_qa.qa.fast.nodes.answer.RetrievedContextAnswerSynthesizerAgent",
        FakeAnswerAgent,
    )
    runtime_context = QARuntimeContext(
        retrieval=QARetrievalConfig(),
        llm_service=FakeLLMService(),
    )

    result = await answer_node(
        {
            "original_query": "原问题",
            "rewritten_query": "完整问题",
            "messages": [],
            "retrieval_results": [{"content": "hit"}],
            "final_answer": "",
            "rewrite_time": None,
            "retrieval_time": None,
            "answer_time": None,
        },
        runtime=SimpleNamespace(context=runtime_context),
    )

    synthesize.assert_awaited_once_with(
        original_query="原问题",
        rewritten_query="完整问题",
        retrieval_results=[{"content": "hit"}],
    )
    assert result["final_answer"] == "最终答案"
    assert result["messages"][0].content == "最终答案"


@pytest.mark.asyncio
async def test_rewrite_and_split_returns_two_subqueries_for_parallel_question():
    fake_llm = AsyncMock()
    fake_llm.generate = AsyncMock(return_value="广州的营收是多少\n北京的营收是多少")
    agent = StandaloneQuestionRewriterAgent(llm_service=fake_llm)
    result = await agent.rewrite_and_split("广州和北京的营收各是多少", None)
    assert result == ["广州的营收是多少", "北京的营收是多少"]


@pytest.mark.asyncio
async def test_rewrite_and_split_returns_single_item_for_simple_question():
    fake_llm = AsyncMock()
    fake_llm.generate = AsyncMock(return_value="广州2024年的营收是多少")
    agent = StandaloneQuestionRewriterAgent(llm_service=fake_llm)
    result = await agent.rewrite_and_split("广州2024年的营收是多少", None)
    assert result == ["广州2024年的营收是多少"]


@pytest.mark.asyncio
async def test_rewrite_and_split_falls_back_to_original_on_empty_response():
    fake_llm = AsyncMock()
    fake_llm.generate = AsyncMock(return_value="   ")
    agent = StandaloneQuestionRewriterAgent(llm_service=fake_llm)
    result = await agent.rewrite_and_split("原始问题", None)
    assert result == ["原始问题"]


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
async def test_rewrite_node_produces_sub_queries_for_parallel_question(monkeypatch):
    class FakeRewriter:
        def __init__(self, llm_service):
            pass

        async def rewrite_and_split(self, query, history):  # pylint: disable=unused-argument
            return ["广州的营收是多少", "北京的营收是多少"]

    monkeypatch.setattr(
        "by_qa.qa.fast.nodes.rewrite.StandaloneQuestionRewriterAgent", FakeRewriter
    )
    runtime_context = QARuntimeContext(
        retrieval=QARetrievalConfig(knowledge_bases=[]),
        llm_service=FakeLLMService(),
    )
    result = await rewrite_node(
        {
            "original_query": "广州和北京的营收各是多少",
            "sub_queries": [],
            "rewritten_query": "",
            "retrieval_results": [],
            "final_answer": "",
            "messages": [],
            "rewrite_time": None,
            "retrieval_time": None,
            "answer_time": None,
        },
        runtime=SimpleNamespace(context=runtime_context),
    )
    assert result["sub_queries"] == [
        {"query_id": "sq_1", "query_text": "广州的营收是多少"},
        {"query_id": "sq_2", "query_text": "北京的营收是多少"},
    ]
    assert result["rewritten_query"] == "广州的营收是多少"


@pytest.mark.asyncio
async def test_rewrite_node_produces_single_sub_query_for_simple_question(monkeypatch):
    class FakeRewriter:
        def __init__(self, llm_service):
            pass

        async def rewrite_and_split(self, query, history):  # pylint: disable=unused-argument
            return ["广州2024年的营收是多少"]

    monkeypatch.setattr(
        "by_qa.qa.fast.nodes.rewrite.StandaloneQuestionRewriterAgent", FakeRewriter
    )
    runtime_context = QARuntimeContext(
        retrieval=QARetrievalConfig(knowledge_bases=[]),
        llm_service=FakeLLMService(),
    )
    result = await rewrite_node(
        {
            "original_query": "广州2024年的营收是多少",
            "sub_queries": [],
            "rewritten_query": "",
            "retrieval_results": [],
            "final_answer": "",
            "messages": [],
            "rewrite_time": None,
            "retrieval_time": None,
            "answer_time": None,
        },
        runtime=SimpleNamespace(context=runtime_context),
    )
    assert result["sub_queries"] == [
        {"query_id": "sq_1", "query_text": "广州2024年的营收是多少"}
    ]
    assert result["rewritten_query"] == "广州2024年的营收是多少"
