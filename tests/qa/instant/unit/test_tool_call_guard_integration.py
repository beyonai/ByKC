"""Integration tests for ToolCallGuardMiddleware with a real create_agent graph."""

import json
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, field_validator

from by_qa.qa.common.middleware.tool_call_guard import ToolCallGuardMiddleware


class _ToolCapableFakeModel(FakeMessagesListChatModel):
    """FakeMessagesListChatModel extended with bind_tools so create_agent accepts it."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ToolCapableFakeModel":
        del tools, kwargs
        return self

    @property
    def _llm_type(self) -> str:
        return "fake-tool-capable"


def _make_model(*messages: AIMessage) -> _ToolCapableFakeModel:
    return _ToolCapableFakeModel(responses=list(messages))


class _RecordingMiddleware(AgentMiddleware):
    """Records whether awrap_tool_call was reached."""

    def __init__(self) -> None:
        self.called = False

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        self.called = True
        return await handler(request)


# ---------------------------------------------------------------------------
# Tools under test
# ---------------------------------------------------------------------------


class _GoodInput(BaseModel):
    x: int


@tool(args_schema=_GoodInput)
def good_tool(x: int) -> str:
    """Returns a simple result."""
    return f"ok:{x}"


class _StrictInput(BaseModel):
    value: int

    @field_validator("value")
    @classmethod
    def must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("value must be positive")
        return v


@tool(args_schema=_StrictInput)
def strict_tool(value: int) -> str:
    """Requires a positive integer."""
    return f"strict:{value}"


@tool
def exploding_tool(msg: str) -> str:
    """Always raises at runtime."""
    raise RuntimeError(f"boom: {msg}")


def _build_agent(model: _ToolCapableFakeModel, recorder: _RecordingMiddleware) -> Any:
    return create_agent(
        model=model,
        tools=[good_tool, strict_tool, exploding_tool],
        middleware=[ToolCallGuardMiddleware(), recorder],
    )


def _tool_messages(result: dict) -> list[ToolMessage]:
    return [m for m in result["messages"] if isinstance(m, ToolMessage)]


def _parse(msg: ToolMessage) -> dict:
    return json.loads(msg.content)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_tool_call_passes_through_to_downstream_middleware():
    recorder = _RecordingMiddleware()
    model = _make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "good_tool",
                    "args": {"x": 5},
                    "id": "tc-1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="done"),
    )
    agent = _build_agent(model, recorder)
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})

    tool_msgs = _tool_messages(result)
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "ok:5"
    assert recorder.called, "downstream middleware must be reached on success path"


@pytest.mark.asyncio
async def test_invalid_tool_name_returns_structured_error_without_calling_downstream():
    recorder = _RecordingMiddleware()
    model = _make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "nonexistent_tool",
                    "args": {},
                    "id": "tc-2",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="sorry"),
    )
    agent = _build_agent(model, recorder)
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})

    tool_msgs = _tool_messages(result)
    assert len(tool_msgs) == 1
    payload = _parse(tool_msgs[0])
    assert payload["error"] is True
    assert payload["error_type"] == "InvalidToolName"
    assert payload["tool_name"] == "nonexistent_tool"
    assert "available_tools" in payload["details"]
    assert not recorder.called, (
        "downstream middleware must NOT be called on invalid tool name"
    )


@pytest.mark.asyncio
async def test_missing_required_arg_returns_invalid_tool_args_error():
    recorder = _RecordingMiddleware()
    # good_tool requires 'x'; omit it entirely
    model = _make_model(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "good_tool", "args": {}, "id": "tc-3", "type": "tool_call"}
            ],
        ),
        AIMessage(content="sorry"),
    )
    agent = _build_agent(model, recorder)
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})

    tool_msgs = _tool_messages(result)
    assert len(tool_msgs) == 1
    payload = _parse(tool_msgs[0])
    assert payload["error"] is True
    assert payload["error_type"] == "InvalidToolArgs"
    assert "validation_errors" in payload["details"]
    assert not recorder.called, (
        "downstream middleware must NOT be called on invalid args"
    )


@pytest.mark.asyncio
async def test_wrong_arg_type_returns_invalid_tool_args_error():
    recorder = _RecordingMiddleware()
    # good_tool expects x: int; pass a non-coercible string
    model = _make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "good_tool",
                    "args": {"x": "not-a-number"},
                    "id": "tc-4",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="sorry"),
    )
    agent = _build_agent(model, recorder)
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})

    tool_msgs = _tool_messages(result)
    assert len(tool_msgs) == 1
    payload = _parse(tool_msgs[0])
    assert payload["error"] is True
    assert payload["error_type"] == "InvalidToolArgs"
    assert "validation_errors" in payload["details"]
    assert not recorder.called, (
        "downstream middleware must NOT be called on invalid args"
    )


@pytest.mark.asyncio
async def test_runtime_exception_returns_tool_execution_error():
    recorder = _RecordingMiddleware()
    model = _make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "exploding_tool",
                    "args": {"msg": "test"},
                    "id": "tc-5",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="sorry"),
    )
    agent = _build_agent(model, recorder)
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})

    tool_msgs = _tool_messages(result)
    assert len(tool_msgs) == 1
    payload = _parse(tool_msgs[0])
    assert payload["error"] is True
    assert payload["error_type"] == "ToolExecutionError"
    assert payload["details"]["exception_type"] == "RuntimeError"
    assert "boom" in payload["message"]
    # runtime exceptions happen inside handler, so downstream middleware IS called
    # before the exception propagates back to guard — this is expected middleware chain behavior
    assert recorder.called
