"""Minimal core exports used by the knowledge-base module."""

from by_qa.core.exceptions import (
    ConfigurationError,
    DeepSearchError,
    GenerationError,
    RetrievalError,
    SearchError,
    ValidationError,
)
from by_qa.core.framework_client import post_discovered_json, request_discovered_json
from by_qa.core.logger import (
    clear_context,
    debug,
    error,
    exception,
    get_logger,
    info,
    set_message_id,
    set_session_id,
    setup_logger,
    warning,
)

__all__ = [
    "get_logger",
    "setup_logger",
    "set_session_id",
    "set_message_id",
    "clear_context",
    "debug",
    "info",
    "warning",
    "error",
    "exception",
    "DeepSearchError",
    "SearchError",
    "ValidationError",
    "ConfigurationError",
    "RetrievalError",
    "GenerationError",
    "request_discovered_json",
    "post_discovered_json",
]
