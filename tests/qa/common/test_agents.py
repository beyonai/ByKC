"""Tests for shared QA agents and compatibility helpers."""

from unittest.mock import AsyncMock, patch

import pytest

from by_qa.qa.agents.query_decomposer import (
    QueryDecomposerAgent,
    decompose_query_with_history,
)
from by_qa.qa.agents.result_aggregator import aggregate_results
from by_qa.qa.agents.subanswer_aggregator import aggregate_sub_answers


def _mock_settings():
    settings = type("Settings", (), {})()
    settings.decomposer_max_sub_queries = 3
    settings.context_max_tokens = 4096
    return settings


@pytest.mark.asyncio
async def test_decompose_query_with_history_returns_backward_compatible_shape():
    fake_result = type("Result", (), {})()
    fake_result.sub_queries = [
        type(
            "SubQuery",
            (),
            {
                "query_id": "sq_1",
                "query_text": "广州办事处的营收是多少",
                "query_type": "single-hop",
                "hop_count": 1,
                "dependencies": [],
                "reasoning_chain": [],
            },
        )()
    ]

    with patch(
        "by_qa.qa.agents.query_decomposer.QueryDecomposerAgent.decompose",
        new=AsyncMock(return_value=fake_result),
    ):
        payload = await decompose_query_with_history("广州呢", "用户问南京办事处营收")

    assert payload == [
        {
            "query_id": "sq_1",
            "query_text": "广州办事处的营收是多少",
            "query_type": "single-hop",
            "hop_count": 1,
            "dependencies": [],
            "reasoning_chain": [],
        }
    ]


@pytest.mark.asyncio
async def test_aggregate_results_uses_global_agent():
    with patch(
        "by_qa.qa.agents.result_aggregator._aggregator.aggregate",
        new=AsyncMock(return_value="final-answer"),
    ) as aggregate:
        answer = await aggregate_results("问题", [])

    aggregate.assert_awaited_once_with("问题", [], None)
    assert answer == "final-answer"


@pytest.mark.asyncio
async def test_aggregate_sub_answers_uses_global_agent():
    with patch(
        "by_qa.qa.agents.subanswer_aggregator._subanswer_aggregator.aggregate",
        new=AsyncMock(return_value="final-answer"),
    ) as aggregate:
        answer = await aggregate_sub_answers("问题", [])

    aggregate.assert_awaited_once_with("问题", [])
    assert answer == "final-answer"


def test_query_decomposer_keeps_rich_prompt_examples():
    with patch(
        "by_qa.qa.agents.query_decomposer.get_settings", return_value=_mock_settings()
    ):
        agent = QueryDecomposerAgent()

    assert "唯一拆分标准" in agent.SYSTEM_PROMPT_WITH_HISTORY
    assert "多轮对话补全" in agent.SYSTEM_PROMPT_WITH_HISTORY
    assert "single-hop 与 multi-hop 并列" in agent.SYSTEM_PROMPT_WITH_HISTORY
