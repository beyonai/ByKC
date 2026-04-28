"""Shared middleware for QA agents."""

from by_qa.qa.common.middleware.tool_call_guard import ToolCallGuardMiddleware

__all__ = ["ToolCallGuardMiddleware"]
