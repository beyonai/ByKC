"""Tests for the sub-answer aggregator agent."""

import pytest

from by_qa.qa.agents.subanswer_aggregator import (
    _build_sub_answers_context,
    aggregator_entry_node,
)


def test_build_sub_answers_context_formats_correctly():
    context = _build_sub_answers_context(
        [
            {
                "sub_query_text": "子问题 1",
                "query_type": "single-hop",
                "answer": "子答案 1",
                "confidence": 0.9,
            }
        ]
    )
    assert "子问题 1" in context
    assert "子答案 1" in context
    assert "0.90" in context


def test_build_sub_answers_context_empty():
    assert _build_sub_answers_context([]) == "未找到子查询答案。"


@pytest.mark.asyncio
async def test_aggregator_entry_node_builds_human_message():
    result = await aggregator_entry_node(
        {
            "original_query": "复合问题",
            "sub_answers": [
                {"sub_query_text": "子问题 1", "answer": "子答案 1", "confidence": 0.9},
            ],
            "messages": [],
            "final_answer": "",
            "aggregation_time": None,
        }
    )
    assert len(result["messages"]) == 2  # RemoveMessage + HumanMessage
    assert "复合问题" in result["messages"][1].content
    assert "子问题 1" in result["messages"][1].content


@pytest.mark.asyncio
async def test_aggregator_entry_node_handles_empty_sub_answers():
    result = await aggregator_entry_node(
        {
            "original_query": "复合问题",
            "sub_answers": [],
            "messages": [],
            "final_answer": "",
            "aggregation_time": None,
        }
    )
    assert result["final_answer"] == "未能生成答案"
