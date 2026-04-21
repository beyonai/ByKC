# tests/qa/instant/unit/test_parallel_retrieval_state.py
"""Tests for index_id generation via DispatcherToolMiddleware."""

from unittest.mock import AsyncMock

import pytest

from by_qa.qa.instant.agents.multi_hop_react import MultiHopMiddleware
from by_qa.qa.instant.agents.single_hop_react import SingleHopMiddleware
from by_qa.qa.instant.runtime.dispatcher import DispatcherToolMiddleware


def _mock_settings():
    return type("Settings", (), {})()


def _mock_model_request(model_settings=None):
    class DummyRequest:
        def __init__(self, model_settings):
            self.model_settings = model_settings or {}

        def override(self, **overrides):
            next_settings = dict(self.model_settings)
            next_settings.update(overrides.get("model_settings", {}))
            return DummyRequest(next_settings)

    return DummyRequest(model_settings)


@pytest.mark.asyncio
async def test_single_hop_middleware_disables_parallel_tool_calls():
    middleware = SingleHopMiddleware(_mock_settings())
    request = _mock_model_request({"temperature": 0})
    handler = AsyncMock(return_value="ok")
    result = await middleware.awrap_model_call(request, handler)
    assert result == "ok"
    forwarded = handler.await_args.args[0]
    assert forwarded.model_settings["parallel_tool_calls"] is False
    assert forwarded.model_settings["temperature"] == 0


@pytest.mark.asyncio
async def test_multi_hop_middleware_disables_parallel_tool_calls():
    middleware = MultiHopMiddleware(_mock_settings())
    request = _mock_model_request({"temperature": 0})
    handler = AsyncMock(return_value="ok")
    result = await middleware.awrap_model_call(request, handler)
    assert result == "ok"
    forwarded = handler.await_args.args[0]
    assert forwarded.model_settings["parallel_tool_calls"] is False


def test_single_hop_index_id_fn():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda step, i: f"r{i + 1}",
        follow_up_prompt="继续",
    )
    assert middleware._index_id_fn(0, 0) == "r1"
    assert middleware._index_id_fn(0, 2) == "r3"


def test_multi_hop_index_id_fn():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda step, i: f"s{step}-{i + 1}",
        follow_up_prompt="继续",
    )
    assert middleware._index_id_fn(0, 2) == "s0-3"
    assert middleware._index_id_fn(1, 0) == "s1-1"
