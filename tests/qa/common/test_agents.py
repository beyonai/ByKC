"""Tests for shared QA agents."""

from unittest.mock import AsyncMock, patch

import pytest

from by_qa.core.model_config import ModelConfig
from by_qa.qa.agents.answer_synthesizer import RetrievedContextAnswerSynthesizerAgent
from by_qa.qa.agents.query_decomposer import QueryDecomposerAgent
from by_qa.qa.agents.standalone_question_rewriter import StandaloneQuestionRewriterAgent
from by_qa.qa.services.llm_service import LLMService


def _mock_llm_service():
    async def get_config(self, model_type: str) -> ModelConfig:  # pylint: disable=unused-argument
        return ModelConfig("m", 0.0, "http://x", "k")

    provider = type("P", (), {"get_config": get_config})()
    return LLMService(provider=provider)


def _mock_settings():
    settings = type("Settings", (), {})()
    settings.decomposer_max_sub_queries = 3
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


@pytest.mark.asyncio
async def test_standalone_question_rewriter_uses_history_for_rewrite():
    llm_service = _mock_llm_service()
    llm_service.generate = AsyncMock(return_value="广州办事处的营收是多少")  # type: ignore[method-assign]
    agent = StandaloneQuestionRewriterAgent(llm_service=llm_service)

    rewritten = await agent.rewrite(
        query="广州呢",
        conversation_history="用户: 南京办事处的营收是多少",
    )

    assert rewritten == "广州办事处的营收是多少"
    llm_service.generate.assert_awaited_once()
    messages = llm_service.generate.await_args.kwargs["messages"]
    assert "当前用户输入：广州呢" in messages[-1]["content"]
    assert "用户: 南京办事处的营收是多少" in messages[-1]["content"]
    assert llm_service.generate.await_args.kwargs["model_type"] == "classifier"


@pytest.mark.asyncio
async def test_answer_synthesizer_answers_from_retrieval_context():
    llm_service = _mock_llm_service()
    llm_service.generate = AsyncMock(return_value="根据制度，发票需要提交审批。")  # type: ignore[method-assign]
    agent = RetrievedContextAnswerSynthesizerAgent(llm_service=llm_service)

    answer = await agent.answer(
        original_query="怎么报销发票",
        rewritten_query="怎么报销发票",
        retrieval_results=[
            {
                "content": "发票报销需要提交审批。",
                "source": "/policy.md",
                "source_type": "knowledge_base",
                "score": 0.9,
            }
        ],
    )

    assert answer == "根据制度，发票需要提交审批。"
    llm_service.generate.assert_awaited_once()
    messages = llm_service.generate.await_args.kwargs["messages"]
    assert "怎么报销发票" in messages[-1]["content"]
    assert "发票报销需要提交审批" in messages[-1]["content"]
    assert llm_service.generate.await_args.kwargs["model_type"] == "generator"
