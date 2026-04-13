"""Tests covering retrieval id generation and tool-call settings in instant QA."""

from unittest.mock import AsyncMock

import pytest

from by_qa.qa.instant.agents.multi_hop_react import MultiHopMiddleware
from by_qa.qa.instant.agents.multi_hop_react import (
    _build_indexed_results as build_multi_hop_indexed_results,
)
from by_qa.qa.instant.agents.single_hop_react import SingleHopMiddleware
from by_qa.qa.instant.agents.single_hop_react import (
    _build_indexed_results as build_single_hop_indexed_results,
)


def _mock_settings():
    return type("Settings", (), {})()


def _mock_request(model_settings=None):
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
    request = _mock_request({"temperature": 0})
    handler = AsyncMock(return_value="ok")

    result = await middleware.awrap_model_call(request, handler)

    assert result == "ok"
    forwarded_request = handler.await_args.args[0]
    assert forwarded_request.model_settings["parallel_tool_calls"] is False
    assert forwarded_request.model_settings["temperature"] == 0


@pytest.mark.asyncio
async def test_multi_hop_middleware_disables_parallel_tool_calls():
    middleware = MultiHopMiddleware(_mock_settings())
    request = _mock_request({"temperature": 0})
    handler = AsyncMock(return_value="ok")

    result = await middleware.awrap_model_call(request, handler)

    assert result == "ok"
    forwarded_request = handler.await_args.args[0]
    assert forwarded_request.model_settings["parallel_tool_calls"] is False
    assert forwarded_request.model_settings["temperature"] == 0


def test_multi_hop_indexed_results_keep_short_incrementing_ids():
    raw_results = [{"content": "doc-a"}, {"content": "doc-b"}]

    indexed = build_multi_hop_indexed_results(raw_results, current_step=0, counter=2)

    assert [item["index_id"] for item in indexed] == ["s0-3", "s0-4"]


def test_single_hop_indexed_results_keep_short_incrementing_ids():
    raw_results = [{"content": "doc-a"}, {"content": "doc-b"}]

    indexed = build_single_hop_indexed_results(raw_results, counter=2)

    assert [item["index_id"] for item in indexed] == ["r3", "r4"]
