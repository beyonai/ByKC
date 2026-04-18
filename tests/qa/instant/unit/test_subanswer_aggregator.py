"""Tests for the instant QA sub-answer aggregator node."""

from types import SimpleNamespace

import pytest

from by_qa.qa.instant.config import InstantQARetrievalConfig
from by_qa.qa.instant.nodes.subanswer_aggregator import subanswer_aggregator_node
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext


class FakeLLMService:
    async def generate(self, *_args, **_kwargs):
        return "完整聚合答案"


@pytest.mark.asyncio
async def test_subanswer_aggregator_logs_generated_final_answer(monkeypatch):
    info_calls: list[str] = []
    monkeypatch.setattr(
        "by_qa.qa.instant.nodes.subanswer_aggregator.info",
        lambda message: info_calls.append(message),
    )
    runtime = SimpleNamespace(
        context=InstantSearchRuntimeContext(
            retrieval=InstantQARetrievalConfig(),
            llm_service=FakeLLMService(),
        )
    )

    result = await subanswer_aggregator_node(
        {
            "original_query": "复合问题",
            "sub_answers": [
                {
                    "sub_query_text": "子问题 1",
                    "query_type": "single-hop",
                    "answer": "子答案 1",
                    "confidence": 0.9,
                },
                {
                    "sub_query_text": "子问题 2",
                    "query_type": "multi-hop",
                    "answer": "子答案 2",
                    "confidence": 0.8,
                },
            ],
        },
        runtime=runtime,
    )

    assert result["final_answer"] == "完整聚合答案"
    assert any(
        message
        == (
            "[subanswer_aggregator] Aggregation generated final answer: "
            "query=复合问题, final_answer=完整聚合答案"
        )
        for message in info_calls
    )
