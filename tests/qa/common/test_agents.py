"""Tests for shared QA agents."""

import importlib
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from by_qa.qa.agents.answer_synthesizer import answer_entry_node
from by_qa.qa.agents.multi_hop_react import build_multi_hop_agent_graph
from by_qa.qa.agents.query_decomposer import (
    _parse_decomposition_response,
    decomposer_entry_node,
)
from by_qa.qa.agents.single_hop_react import build_single_hop_agent_graph
from by_qa.qa.agents.standalone_question_rewriter import rewriter_entry_node
from by_qa.qa.common.config import AgentOverride
from by_qa.qa.common.middleware.tool_call_guard import ToolCallGuardMiddleware
from by_qa.qa.tools.knowledge_tools import DispatcherToolMiddleware


class _FakeModel:
    def bind(self, **kwargs):
        del kwargs
        return self


class _FakeLLMService:
    async def _get_streaming_model(self, model_type: str):
        del model_type
        return _FakeModel()


class _ProbeMiddleware(AgentMiddleware):
    pass


def _mock_settings():
    settings = type("Settings", (), {})()
    settings.decomposer_max_sub_queries = 3
    return settings


def test_parse_decomposition_response_returns_sub_queries():
    response = json.dumps(
        {
            "sub_queries": [
                {
                    "query_id": "sq_1",
                    "query_text": "广州办事处的营收是多少",
                    "query_type": "single-hop",
                    "hop_count": 1,
                    "dependencies": [],
                    "reasoning_chain": [],
                }
            ],
            "reasoning": "单一问题不拆分",
        }
    )
    result = _parse_decomposition_response(response, "广州呢", max_sub_queries=3)
    assert len(result.sub_queries) == 1
    assert result.sub_queries[0].query_text == "广州办事处的营收是多少"
    assert result.metadata["total_sub_queries"] == 1


def test_parse_decomposition_response_falls_back_on_invalid_json():
    result = _parse_decomposition_response("not json", "原始问题", max_sub_queries=3)
    assert len(result.sub_queries) == 1
    assert result.sub_queries[0].query_text == "原始问题"


def test_agent_override_normalizes_none_collections():
    override = AgentOverride(middleware=None, tools=None)
    assert override.middleware == []
    assert override.tools == []


@pytest.mark.asyncio
async def test_decomposer_entry_node_clears_messages_and_builds_human_message():
    with patch(
        "by_qa.qa.agents.query_decomposer.get_settings",
        return_value=_mock_settings(),
    ):
        result = await decomposer_entry_node(
            {
                "original_query": "广州呢",
                "messages": [
                    HumanMessage(content="南京营收"),
                    HumanMessage(content="广州呢"),
                ],
                "sub_queries": [],
                "decomposition_metadata": None,
                "decomposition_time": None,
            }
        )
    assert len(result["messages"]) == 2  # RemoveMessage + HumanMessage
    assert isinstance(result["messages"][1], HumanMessage)
    assert "广州呢" in result["messages"][1].content


@pytest.mark.asyncio
async def test_rewriter_entry_node_uses_history():
    result = await rewriter_entry_node(
        {
            "original_query": "广州呢",
            "messages": [
                HumanMessage(content="南京办事处的营收是多少"),
                HumanMessage(content="广州呢"),
            ],
            "sub_queries": [],
            "rewritten_query": "",
            "rewrite_time": None,
        }
    )
    human_msg = result["messages"][1]
    assert "Current user input: 广州呢" in human_msg.content
    assert "南京办事处的营收是多少" in human_msg.content


@pytest.mark.asyncio
async def test_answer_entry_node_builds_context_from_retrieval_results():
    result = await answer_entry_node(
        {
            "original_query": "怎么报销发票",
            "sub_queries": [{"query_id": "sq_1", "query_text": "怎么报销发票"}],
            "retrieval_results": [
                {
                    "content": "发票报销需要提交审批。",
                    "source": "/policy.md",
                    "source_type": "knowledge_base",
                    "score": 0.9,
                },
            ],
            "messages": [],
            "final_answer": "",
            "answer_time": None,
        }
    )
    human_msg = result["messages"][1]
    assert "怎么报销发票" in human_msg.content
    assert "发票报销需要提交审批" in human_msg.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "builder", "extra_patches"),
    [
        (
            "by_qa.qa.agents.answer_synthesizer",
            "build_answer_synthesizer_subgraph",
            (),
        ),
        (
            "by_qa.qa.agents.query_decomposer",
            "build_decomposer_subgraph",
            (
                patch(
                    "by_qa.qa.agents.query_decomposer.get_settings",
                    return_value=_mock_settings(),
                ),
            ),
        ),
        (
            "by_qa.qa.agents.standalone_question_rewriter",
            "build_rewriter_subgraph",
            (),
        ),
        (
            "by_qa.qa.agents.subanswer_aggregator",
            "build_aggregator_subgraph",
            (),
        ),
        (
            "by_qa.qa.agents.multi_hop_summarizer",
            "build_multi_hop_summary_subgraph",
            (),
        ),
    ],
)
async def test_builder_appends_override_middleware_for_agents_without_defaults(
    module_name: str,
    builder: str,
    extra_patches,
):
    module = importlib.import_module(module_name)
    override_middleware = [_ProbeMiddleware()]
    captured = {}

    async def _fake_agent_graph(*args, **kwargs):
        del args, kwargs
        return {}

    def _fake_create_agent(**kwargs):
        captured.update(kwargs)
        return _fake_agent_graph

    patchers = [
        patch(f"{module_name}.create_agent", side_effect=_fake_create_agent),
        *extra_patches,
    ]

    with ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        await getattr(module, builder)(
            llm_service=_FakeLLMService(),
            override=AgentOverride(middleware=override_middleware),
            checkpointer=None,
        )

    assert captured["middleware"] == override_middleware


@pytest.mark.asyncio
async def test_single_hop_agent_graph_preserves_defaults_and_appends_override_middleware():
    captured = {}
    extra = _ProbeMiddleware()

    async def _fake_agent_graph(*args, **kwargs):
        del args, kwargs
        return {}

    def _fake_create_agent(**kwargs):
        captured.update(kwargs)
        return _fake_agent_graph

    with patch(
        "by_qa.qa.agents.single_hop_react.create_agent",
        side_effect=_fake_create_agent,
    ):
        await build_single_hop_agent_graph(
            llm_service=_FakeLLMService(),
            override=AgentOverride(middleware=[extra]),
            checkpointer=None,
        )

    middleware = captured["middleware"]
    assert len(middleware) == 3
    assert isinstance(middleware[0], ToolCallGuardMiddleware)
    assert isinstance(middleware[1], DispatcherToolMiddleware)
    assert middleware[2] is extra


@pytest.mark.asyncio
async def test_multi_hop_agent_graph_preserves_defaults_and_appends_override_middleware():
    captured = {}
    extra = _ProbeMiddleware()

    async def _fake_agent_graph(*args, **kwargs):
        del args, kwargs
        return {}

    def _fake_create_agent(**kwargs):
        captured.update(kwargs)
        return _fake_agent_graph

    with patch(
        "by_qa.qa.agents.multi_hop_react.create_agent",
        side_effect=_fake_create_agent,
    ):
        await build_multi_hop_agent_graph(
            llm_service=_FakeLLMService(),
            override=AgentOverride(middleware=[extra]),
            checkpointer=None,
        )

    middleware = captured["middleware"]
    assert len(middleware) == 3
    assert isinstance(middleware[0], ToolCallGuardMiddleware)
    assert isinstance(middleware[1], DispatcherToolMiddleware)
    assert middleware[2] is extra
