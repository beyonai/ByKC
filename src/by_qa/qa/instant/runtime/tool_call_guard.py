"""ToolCallGuardMiddleware: intercepts invalid tool calls and runtime exceptions."""

from __future__ import annotations

import json
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from pydantic import ValidationError


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
            return _error_message(
                error_type="InvalidToolName",
                tool_name=tool_name,
                message=f"Tool '{tool_name}' is not registered.",
                details={"available_tools": []},
                tool_call_id=tool_call_id,
            )

        try:
            return await handler(request)
        except ValidationError as exc:
            return _error_message(
                error_type="InvalidToolArgs",
                tool_name=tool_name,
                message="Tool argument validation failed.",
                details={"validation_errors": exc.errors()},
                tool_call_id=tool_call_id,
            )
        except Exception as exc:  # noqa: BLE001
            return _error_message(
                error_type="ToolExecutionError",
                tool_name=tool_name,
                message=str(exc),
                details={"exception_type": type(exc).__name__},
                tool_call_id=tool_call_id,
            )


__all__ = ["ToolCallGuardMiddleware"]
