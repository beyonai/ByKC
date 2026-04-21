"""Tests for instant QA middleware compatibility."""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import SystemMessage, ToolMessage

from by_qa.qa.instant.agents.multi_hop_react import MultiHopMiddleware
from by_qa.qa.instant.agents.single_hop_react import SingleHopMiddleware
from by_qa.qa.instant.runtime.dispatcher import DispatcherToolMiddleware
from by_qa.qa.instant.runtime.operation_registry import (
    OPERATION_REGISTRY,
    OperationType,
)


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


def _make_tool_call_request(tool_name: str, state: dict, result_content: list):
    class FakeToolCall(dict):
        pass

    tc = FakeToolCall({"name": tool_name, "id": "tc-001"})

    class FakeRequest:
        tool_call = tc

    FakeRequest.state = state

    class FakeToolMessage:
        content = json.dumps(result_content, ensure_ascii=False)

    return FakeRequest(), FakeToolMessage()


@pytest.mark.asyncio
async def test_dispatcher_middleware_passes_through_non_search_tools():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda step, i: f"r{i + 1}",
        follow_up_prompt="继续",
    )
    request, fake_result = _make_tool_call_request("next_hop", {}, [])
    handler = AsyncMock(return_value=fake_result)
    result = await middleware.awrap_tool_call(request, handler)
    assert result is fake_result


@pytest.mark.asyncio
async def test_dispatcher_middleware_injects_index_ids_for_search():
    search_tool_name = OPERATION_REGISTRY[OperationType.SEARCH].tool_name
    raw = [{"content": "doc-a", "score": 0.9}, {"content": "doc-b", "score": 0.8}]
    request, fake_result = _make_tool_call_request(
        search_tool_name, {"result_counter": 0, "current_step": 0}, raw
    )
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda step, i: f"r{i + 1}",
        follow_up_prompt="继续检索",
    )
    handler = AsyncMock(return_value=fake_result)
    cmd = await middleware.awrap_tool_call(request, handler)

    indexed = cmd.update["retrieval_results"]
    assert indexed[0]["index_id"] == "r1"
    assert indexed[1]["index_id"] == "r2"
    assert cmd.update["result_counter"] == 2

    messages = cmd.update["messages"]
    tool_msg = messages[0]
    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.artifact == raw
    llm_content = json.loads(tool_msg.content)
    assert llm_content[0] == {"index_id": "r1", "content": "doc-a"}

    sys_msg = messages[1]
    assert isinstance(sys_msg, SystemMessage)
    assert sys_msg.content == "继续检索"


@pytest.mark.asyncio
async def test_dispatcher_middleware_multi_hop_index_ids():
    search_tool_name = OPERATION_REGISTRY[OperationType.SEARCH].tool_name
    raw = [{"content": "doc-a", "score": 0.9}]
    request, fake_result = _make_tool_call_request(
        search_tool_name, {"result_counter": 2, "current_step": 1}, raw
    )
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda step, i: f"s{step}-{i + 1}",
        follow_up_prompt="继续",
    )
    handler = AsyncMock(return_value=fake_result)
    cmd = await middleware.awrap_tool_call(request, handler)
    assert cmd.update["retrieval_results"][0]["index_id"] == "s1-3"
