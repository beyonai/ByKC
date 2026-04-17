"""Tests for context_manager_node runtime injection."""

from types import SimpleNamespace

import pytest

from by_qa.core.model_config import ModelConfig
from by_qa.qa.instant.nodes.context_manager import context_manager_node
from by_qa.qa.services.llm_service import LLMService


def _make_runtime(max_model_len):
    async def get_config(self, model_type: str) -> ModelConfig:  # pylint: disable=unused-argument
        return ModelConfig(
            model_name="gpt-4o",
            temperature=0.0,
            base_url="http://x",
            api_key="k",
            max_model_len=max_model_len,
        )

    provider = type("P", (), {"get_config": get_config})()
    llm_service = LLMService(provider=provider)
    return SimpleNamespace(context=SimpleNamespace(llm_service=llm_service))


@pytest.mark.asyncio
async def test_context_manager_node_raises_without_runtime():
    state = {"retrieval_results": []}
    with pytest.raises(RuntimeError, match="llm_service is required"):
        await context_manager_node(state, runtime=None)


@pytest.mark.asyncio
async def test_context_manager_node_raises_when_max_model_len_is_none():
    state = {"retrieval_results": []}
    runtime = _make_runtime(max_model_len=None)
    with pytest.raises(RuntimeError, match="GENERATOR_MAX_MODEL_LEN is required"):
        await context_manager_node(state, runtime=runtime)


@pytest.mark.asyncio
async def test_context_manager_node_returns_reset_results_with_valid_config():
    state = {"retrieval_results": []}
    runtime = _make_runtime(max_model_len=8192)
    result = await context_manager_node(state, runtime=runtime)
    assert "retrieval_results" in result
