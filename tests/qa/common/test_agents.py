"""Tests for shared QA agents."""

from unittest.mock import AsyncMock, patch

import pytest

from by_qa.core.model_config import ModelConfig
from by_qa.qa.agents.query_decomposer import QueryDecomposerAgent
from by_qa.qa.services.llm_service import LLMService


def _mock_llm_service():
    async def get_config(self, model_type: str) -> ModelConfig:  # pylint: disable=unused-argument
        return ModelConfig("m", 0.0, "http://x", "k")

    provider = type("P", (), {"get_config": get_config})()
    return LLMService(provider=provider)


def _mock_settings():
    settings = type("Settings", (), {})()
    settings.decomposer_max_sub_queries = 3
    settings.context_max_tokens = 4096
    return settings


@pytest.mark.asyncio
async def test_decompose_with_history_returns_backward_compatible_shape():
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
        "by_qa.qa.agents.query_decomposer.get_settings", return_value=_mock_settings()
    ):
        agent = QueryDecomposerAgent(llm_service=_mock_llm_service())

    with patch.object(agent, "decompose", new=AsyncMock(return_value=fake_result)):
        payload = await agent.decompose_with_history("广州呢", "用户问南京办事处营收")

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


def test_query_decomposer_keeps_rich_prompt_examples():
    with patch(
        "by_qa.qa.agents.query_decomposer.get_settings", return_value=_mock_settings()
    ):
        agent = QueryDecomposerAgent(llm_service=_mock_llm_service())

    assert "唯一拆分标准" in agent.SYSTEM_PROMPT_WITH_HISTORY
    assert "多轮对话补全" in agent.SYSTEM_PROMPT_WITH_HISTORY
    assert "single-hop 与 multi-hop 并列" in agent.SYSTEM_PROMPT_WITH_HISTORY
