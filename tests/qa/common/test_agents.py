"""Tests for shared QA agents."""

import json
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage

from by_qa.qa.agents.answer_synthesizer import answer_entry_node
from by_qa.qa.agents.query_decomposer import (
    SYSTEM_PROMPT_WITH_HISTORY,
    _parse_decomposition_response,
    decomposer_entry_node,
)
from by_qa.qa.agents.standalone_question_rewriter import rewriter_entry_node


def _mock_settings():
    settings = type("Settings", (), {})()
    settings.decomposer_max_sub_queries = 3
    return settings


def test_query_decomposer_keeps_rich_prompt_examples():
    assert "The Only Splitting Criterion" in SYSTEM_PROMPT_WITH_HISTORY
    assert "Multi-turn Conversation Completion" in SYSTEM_PROMPT_WITH_HISTORY
    assert "single-hop and multi-hop parallel" in SYSTEM_PROMPT_WITH_HISTORY


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
