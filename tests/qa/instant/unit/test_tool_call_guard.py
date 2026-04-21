"""Unit tests for ToolCallGuardMiddleware."""

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from by_qa.qa.instant.runtime.tool_call_guard import ToolCallGuardMiddleware


def _make_request(tool_name: str, tool_obj=None, state: dict | None = None):
    @dataclass
    class FakeRequest:
        tool_call: dict
        tool: Any
        state: dict
        runtime: Any = None

    return FakeRequest(
        tool_call={"name": tool_name, "id": "tc-001", "args": {}},
        tool=tool_obj,
        state=state or {},
    )


def _parse_error(msg: ToolMessage) -> dict:
    return json.loads(msg.content)


@pytest.mark.asyncio
async def test_invalid_tool_name_returns_error_tool_message():
    middleware = ToolCallGuardMiddleware()
    request = _make_request("nonexistent_tool", tool_obj=None)
    handler = AsyncMock()

    result = await middleware.awrap_tool_call(request, handler)

    handler.assert_not_called()
    assert isinstance(result, ToolMessage)
    payload = _parse_error(result)
    assert payload["error"] is True
    assert payload["error_type"] == "InvalidToolName"
    assert payload["tool_name"] == "nonexistent_tool"
    assert "available_tools" in payload["details"]


@pytest.mark.asyncio
async def test_valid_tool_passes_through_to_handler():
    middleware = ToolCallGuardMiddleware()
    fake_tool = MagicMock()
    fake_tool.name = "search_knowledge"
    request = _make_request("search_knowledge", tool_obj=fake_tool)
    expected = ToolMessage(content="ok", name="search_knowledge", tool_call_id="tc-001")
    handler = AsyncMock(return_value=expected)

    result = await middleware.awrap_tool_call(request, handler)

    handler.assert_called_once_with(request)
    assert result is expected


@pytest.mark.asyncio
async def test_validation_error_returns_invalid_tool_args():
    from pydantic import BaseModel

    middleware = ToolCallGuardMiddleware()
    fake_tool = MagicMock()
    fake_tool.name = "search_knowledge"
    request = _make_request("search_knowledge", tool_obj=fake_tool)

    class M(BaseModel):
        x: int

    val_error: ValidationError | None = None
    try:
        M.model_validate({"x": "not-an-int"})
    except ValidationError as e:
        val_error = e

    assert val_error is not None
    handler = AsyncMock(side_effect=val_error)

    result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    payload = _parse_error(result)
    assert payload["error"] is True
    assert payload["error_type"] == "InvalidToolArgs"
    assert "validation_errors" in payload["details"]


@pytest.mark.asyncio
async def test_runtime_exception_returns_tool_execution_error():
    middleware = ToolCallGuardMiddleware()
    fake_tool = MagicMock()
    fake_tool.name = "search_knowledge"
    request = _make_request("search_knowledge", tool_obj=fake_tool)
    handler = AsyncMock(side_effect=RuntimeError("connection refused"))

    result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    payload = _parse_error(result)
    assert payload["error"] is True
    assert payload["error_type"] == "ToolExecutionError"
    assert payload["details"]["exception_type"] == "RuntimeError"
    assert "connection refused" in payload["message"]


@pytest.mark.asyncio
async def test_downstream_handler_not_called_on_invalid_name():
    middleware = ToolCallGuardMiddleware()
    request = _make_request("ghost_tool", tool_obj=None)
    handler = AsyncMock()

    await middleware.awrap_tool_call(request, handler)

    handler.assert_not_called()
