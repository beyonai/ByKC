"""Tests for instant QA middleware compatibility."""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import ToolMessage

from by_qa.qa.common.config import KnowledgeBaseConfig, QARetrievalConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.operation_registry import OPERATION_REGISTRY, OperationType
from by_qa.qa.tools.knowledge_tools import DispatcherToolMiddleware


def _make_runtime_context(*ops_lists: set[OperationType]) -> QARuntimeContext:
    """Build a QARuntimeContext whose KBs collectively support the union of given ops."""
    kbs = [
        KnowledgeBaseConfig(
            kb_code=f"kb{i}", kb_name=f"kb{i}", service_name="svc", operations=ops
        )
        for i, ops in enumerate(ops_lists)
    ]
    return QARuntimeContext(retrieval=QARetrievalConfig(knowledge_bases=kbs))


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
        {"config": {"configurable": {"thread_id": "thread-1"}}, "context": None},
    )()

    class FakeToolMessage:
        content = json.dumps(result_content, ensure_ascii=False)

    return FakeRequest(), FakeToolMessage()


def _make_tool_call_request_with_args(
    tool_name: str,
    state: dict,
    args: dict,
    result_content: str = "{}",
    runtime_context: QARuntimeContext | None = None,
):
    class FakeToolCall(dict):
        pass

    tc = FakeToolCall({"name": tool_name, "id": "tc-001", "args": args})

    class FakeRequest:
        tool_call = tc

    FakeRequest.state = state
    FakeRequest.runtime = type(
        "FakeRuntime",
        (),
        {
            "config": {"configurable": {"thread_id": "thread-1"}},
            "context": runtime_context,
        },
    )()

    class FakeToolMessage:
        content = result_content

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
    assert len(messages) == 1
    tool_msg = messages[0]
    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.artifact == indexed
    llm_content = json.loads(tool_msg.content)
    assert llm_content[0] == {"index_id": "3-0-1", "content": "doc-a"}


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


# ── DSL guard tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dsl_guard_passes_through_when_no_where():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda s, st, i: f"{s}-{st}-{i}",
        follow_up_prompt="继续",
    )
    request, fake_result = _make_tool_call_request_with_args(
        "some_tool", {"messages": []}, {"query": "hello"}
    )
    handler = AsyncMock(return_value=fake_result)
    result = await middleware.awrap_tool_call(request, handler)
    assert result is fake_result


@pytest.mark.asyncio
async def test_dsl_guard_blocks_when_where_present_without_prerequisites():
    ctx = _make_runtime_context(
        {OperationType.KNOWLEDGE_SEARCH, OperationType.METADATA_FIELDS_LIST}
    )
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda s, st, i: f"{s}-{st}-{i}",
        follow_up_prompt="继续",
    )
    request, fake_result = _make_tool_call_request_with_args(
        "some_tool",
        {"messages": []},
        {"where": {"eq": {"fieldName": "status", "value": "active"}}, "query": "test"},
        runtime_context=ctx,
    )
    handler = AsyncMock(return_value=fake_result)
    result = await middleware.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    error = json.loads(result.content)
    assert error["error"] is True
    assert error["error_type"] == "DslPrerequisiteNotMet"
    assert "list_metadata_fields" in error["missing_tools"]
    assert "get_dsl_guide" in error["missing_tools"]
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_dsl_guard_blocks_when_only_metadata_fields_called():
    ctx = _make_runtime_context(
        {OperationType.KNOWLEDGE_SEARCH, OperationType.METADATA_FIELDS_LIST}
    )
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda s, st, i: f"{s}-{st}-{i}",
        follow_up_prompt="继续",
    )
    request, fake_result = _make_tool_call_request_with_args(
        "some_tool",
        {
            "messages": [
                ToolMessage(
                    content="[{}]", name="list_metadata_fields", tool_call_id="tc-old"
                )
            ]
        },
        {"where": {"eq": {"fieldName": "status", "value": "active"}}, "query": "test"},
        runtime_context=ctx,
    )
    handler = AsyncMock(return_value=fake_result)
    result = await middleware.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    error = json.loads(result.content)
    assert error["error"] is True
    assert error["missing_tools"] == ["get_dsl_guide"]
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_dsl_guard_allows_when_both_prerequisites_met():
    ctx = _make_runtime_context(
        {OperationType.KNOWLEDGE_SEARCH, OperationType.METADATA_FIELDS_LIST}
    )
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda s, st, i: f"{s}-{st}-{i}",
        follow_up_prompt="继续",
    )
    request, fake_result = _make_tool_call_request_with_args(
        "some_tool",
        {
            "messages": [
                ToolMessage(
                    content="[{}]", name="list_metadata_fields", tool_call_id="tc-1"
                ),
                ToolMessage(content="{}", name="get_dsl_guide", tool_call_id="tc-2"),
            ]
        },
        {"where": {"eq": {"fieldName": "status", "value": "active"}}, "query": "test"},
        runtime_context=ctx,
    )
    handler = AsyncMock(return_value=fake_result)
    result = await middleware.awrap_tool_call(request, handler)
    assert result is fake_result


@pytest.mark.asyncio
async def test_dsl_guard_passes_through_when_where_is_empty_dict():
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda s, st, i: f"{s}-{st}-{i}",
        follow_up_prompt="继续",
    )
    request, fake_result = _make_tool_call_request_with_args(
        "some_tool",
        {"messages": []},
        {"where": {}, "query": "test"},
    )
    handler = AsyncMock(return_value=fake_result)
    result = await middleware.awrap_tool_call(request, handler)
    assert result is fake_result


@pytest.mark.asyncio
async def test_dsl_guard_where_not_supported_without_metadata_fields_tool():
    """When list_metadata_fields is not available, 'where' should be rejected."""
    ctx = _make_runtime_context({OperationType.KNOWLEDGE_SEARCH})
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda s, st, i: f"{s}-{st}-{i}",
        follow_up_prompt="继续",
    )
    request, fake_result = _make_tool_call_request_with_args(
        "search_knowledge",
        {"messages": []},
        {"where": {"eq": {"fieldName": "status", "value": "active"}}, "query": "test"},
        runtime_context=ctx,
    )
    handler = AsyncMock(return_value=fake_result)
    result = await middleware.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    error = json.loads(result.content)
    assert error["error"] is True
    assert error["error_type"] == "WhereNotSupported"
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_dsl_guard_filters_unavailable_from_prerequisites():
    """When list_metadata_fields is unavailable but where is used, WhereNotSupported
    is returned before the prerequisite check."""
    ctx = _make_runtime_context({OperationType.KNOWLEDGE_SEARCH})
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda s, st, i: f"{s}-{st}-{i}",
        follow_up_prompt="继续",
    )
    request, fake_result = _make_tool_call_request_with_args(
        "search_knowledge",
        {"messages": []},
        {"where": {"eq": {"fieldName": "status", "value": "active"}}, "query": "test"},
        runtime_context=ctx,
    )
    handler = AsyncMock(return_value=fake_result)
    result = await middleware.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    error = json.loads(result.content)
    assert error["error"] is True
    assert error["error_type"] == "WhereNotSupported"


@pytest.mark.asyncio
async def test_dsl_guard_allows_where_when_available_tools_are_called():
    """When METADATA_FIELDS_LIST is available and its prerequisites are met,
    'where' should be allowed."""
    ctx = _make_runtime_context(
        {OperationType.KNOWLEDGE_SEARCH, OperationType.METADATA_FIELDS_LIST}
    )
    middleware = DispatcherToolMiddleware(
        index_id_fn=lambda s, st, i: f"{s}-{st}-{i}",
        follow_up_prompt="继续",
    )
    request, fake_result = _make_tool_call_request_with_args(
        "some_tool",
        {
            "messages": [
                ToolMessage(
                    content="[{}]", name="list_metadata_fields", tool_call_id="tc-1"
                ),
                ToolMessage(content="{}", name="get_dsl_guide", tool_call_id="tc-2"),
            ]
        },
        {"where": {"eq": {"fieldName": "status", "value": "active"}}, "query": "test"},
        runtime_context=ctx,
    )
    handler = AsyncMock(return_value=fake_result)
    result = await middleware.awrap_tool_call(request, handler)
    assert result is fake_result
