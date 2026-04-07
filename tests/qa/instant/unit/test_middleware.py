"""Tests for instant QA middleware compatibility."""

import pytest

from by_qa.qa.instant.agents.multi_hop_react import MultiHopMiddleware
from by_qa.qa.instant.agents.single_hop_react import SingleHopMiddleware


def _mock_settings():
    return type("Settings", (), {})()


@pytest.mark.asyncio
async def test_single_hop_middleware_accepts_state_only_call():
    middleware = SingleHopMiddleware(_mock_settings())
    assert await middleware.abefore_model({"messages": []}) is None


@pytest.mark.asyncio
async def test_multi_hop_middleware_accepts_state_only_call():
    middleware = MultiHopMiddleware(_mock_settings())
    assert await middleware.abefore_model({"messages": []}) is None
