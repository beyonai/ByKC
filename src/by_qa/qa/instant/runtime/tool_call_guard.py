"""ToolCallGuardMiddleware: intercepts invalid tool calls and runtime exceptions."""

from __future__ import annotations

import json
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools.base import InjectedToolCallId
from langgraph.prebuilt.tool_node import InjectedState
from pydantic import ValidationError

from by_qa.core.logger import error, warning


def _is_injected(field_info: Any) -> bool:
    for m in field_info.metadata:
        if isinstance(m, (InjectedState, InjectedToolCallId)):
            return True
        if isinstance(m, type) and issubclass(m, (InjectedState, InjectedToolCallId)):
            return True
    return False


def _validate_args(tool: Any, args: dict[str, Any]) -> ValidationError | None:
    schema = tool.args_schema
    if schema is None:
        return None
    injected = {
        name for name, field in schema.model_fields.items() if _is_injected(field)
    }
    try:
        schema.model_validate(args)
    except ValidationError as exc:
        # Filter out errors caused by missing injected fields (state, tool_call_id, etc.)
        real_errors = [
            e for e in exc.errors() if e.get("loc", ("",))[0] not in injected
        ]
        if real_errors:
            return exc
    return None


def _error_message(
    *,
    error_type: str,
    tool_name: str,
    message: str,
    details: dict[str, Any],
    tool_call_id: str,
) -> ToolMessage:
    content = json.dumps(
        {
            "error": True,
            "error_type": error_type,
            "tool_name": tool_name,
            "message": message,
            "details": details,
        },
        ensure_ascii=False,
    )
    return ToolMessage(content=content, name=tool_name, tool_call_id=tool_call_id)


class ToolCallGuardMiddleware(AgentMiddleware):
    """First-in-chain middleware that converts tool call failures into structured ToolMessage errors."""

    async def awrap_tool_call(self, request: ToolCallRequest, handler: Callable) -> Any:
        tool_name: str = request.tool_call["name"]
        tool_call_id: str = request.tool_call["id"]

        if request.tool is None:
            warning(
                "[tool_call_guard] InvalidToolName: tool='%s' tool_call_id='%s'",
                tool_name,
                tool_call_id,
            )
            return _error_message(
                error_type="InvalidToolName",
                tool_name=tool_name,
                message=f"Tool '{tool_name}' is not registered.",
                details={"available_tools": []},
                tool_call_id=tool_call_id,
            )

        val_error = _validate_args(request.tool, request.tool_call.get("args", {}))
        if val_error is not None:
            warning(
                "[tool_call_guard] InvalidToolArgs: tool='%s' tool_call_id='%s' errors=%s",
                tool_name,
                tool_call_id,
                val_error.errors(),
            )
            return _error_message(
                error_type="InvalidToolArgs",
                tool_name=tool_name,
                message="Tool argument validation failed.",
                details={"validation_errors": val_error.errors()},
                tool_call_id=tool_call_id,
            )

        try:
            return await handler(request)
        except Exception as exc:  # noqa: BLE001
            error(
                "[tool_call_guard] ToolExecutionError: tool='%s' tool_call_id='%s' exc=%s",
                tool_name,
                tool_call_id,
                exc,
            )
            return _error_message(
                error_type="ToolExecutionError",
                tool_name=tool_name,
                message=str(exc),
                details={"exception_type": type(exc).__name__},
                tool_call_id=tool_call_id,
            )


__all__ = ["ToolCallGuardMiddleware"]
