"""Tests for instant QA middleware compatibility."""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import SystemMessage, ToolMessage

from by_qa.qa.common.operation_registry import OPERATION_REGISTRY, OperationType
from by_qa.qa.tools.knowledge_tools import DispatcherToolMiddleware


def _make_tool_call_request(tool_name: str, state: dict, result_content: list):
    class FakeToolCall(dict):
        pass

    tc = FakeToolCall({"name": tool_name, "id": "tc-001"})

    class FakeRequest:
        tool_call = tc

    FakeRequest.state = state
    FakeRequest.runtime = type(
        "FakeRuntime",
        (),
        {"config": {"configurable": {"thread_id": "thread-1"}}},
    )()

    class FakeToolMessage:
        content = json.dumps(result_content, ensure_ascii=False)

    return FakeRequest(), FakeToolMessage()


@pytest.mark.asyncio
async def test_dispatcher_middleware_passes_through_non_search_tools():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda sub_query_idx, step, item_id: (
            f"{sub_query_idx}-{step}-{item_id}"
        ),
        follow_up_prompt="继续",
    )
    request, fake_result = _make_tool_call_request("next_hop", {}, [])
    handler = AsyncMock(return_value=fake_result)
    result = await middleware.awrap_tool_call(request, handler)
    assert result is fake_result


@pytest.mark.asyncio
async def test_dispatcher_middleware_injects_index_ids_for_search():
    search_tool_name = OPERATION_REGISTRY[OperationType.KNOWLEDGE_SEARCH].tool_name
    raw = [{"content": "doc-a", "score": 0.9}, {"content": "doc-b", "score": 0.8}]
    request, fake_result = _make_tool_call_request(
        search_tool_name, {"sub_query_idx": 3, "current_step": 0}, raw
    )
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda sub_query_idx, step, item_id: (
            f"{sub_query_idx}-{step}-{item_id}"
        ),
        follow_up_prompt="继续检索",
    )
    handler = AsyncMock(return_value=fake_result)
    cmd = await middleware.awrap_tool_call(request, handler)

    indexed = cmd.update["retrieval_results"]
    assert indexed[0]["index_id"] == "3-0-1"
    assert indexed[1]["index_id"] == "3-0-2"

    messages = cmd.update["messages"]
    tool_msg = messages[0]
    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.artifact == indexed
    llm_content = json.loads(tool_msg.content)
    assert llm_content[0] == {"index_id": "3-0-1", "content": "doc-a"}

    sys_msg = messages[1]
    assert isinstance(sys_msg, SystemMessage)
    assert sys_msg.content == "继续检索"


@pytest.mark.asyncio
async def test_dispatcher_middleware_multi_hop_index_ids():
    search_tool_name = OPERATION_REGISTRY[OperationType.KNOWLEDGE_SEARCH].tool_name
    raw = [{"content": "doc-a", "score": 0.9}]
    request, fake_result = _make_tool_call_request(
        search_tool_name, {"sub_query_idx": 1, "current_step": 1}, raw
    )
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda sub_query_idx, step, item_id: (
            f"{sub_query_idx}-{step}-{item_id}"
        ),
        follow_up_prompt="继续",
    )
    handler = AsyncMock(return_value=fake_result)
    cmd = await middleware.awrap_tool_call(request, handler)
    assert cmd.update["retrieval_results"][0]["index_id"] == "1-1-1"
