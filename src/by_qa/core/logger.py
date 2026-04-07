"""Core logging module with file rotation and contextual information.

This module provides a centralized logging system that supports:
- File-based logging with size-based rotation
- Contextual information (session_id, message_id)
- Structured log format
"""

import logging
import logging.handlers
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

from by_qa.config import get_settings

# Context variables for session and message tracking
session_id_var: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
message_id_var: ContextVar[Optional[str]] = ContextVar("message_id", default=None)


class ContextualFormatter(logging.Formatter):
    """Custom formatter that includes session_id and message_id in log records."""

    def format(self, record: logging.LogRecord) -> str:
        # Get context variables
        session_id = session_id_var.get()
        message_id = message_id_var.get()

        # Build context string
        context_parts = []
        if session_id:
            context_parts.append(f"session_id:{session_id}")
        if message_id:
            context_parts.append(f"message_id:{message_id}")

        if context_parts:
            record.context = f"[{', '.join(context_parts)}]"
        else:
            record.context = ""

        # record.filename and record.lineno already contain the caller's info
        # because Python's logging module automatically captures them
        # when the log function is called
        record.location = f"[{record.filename}:{record.lineno}]"

        return super().format(record)


def setup_logger(
    name: str = "deepsearch",
    log_dir: Optional[str] = None,
    max_bytes: int = 50 * 1024 * 1024,  # 50MB
    backup_count: int = 8,
    log_level: int = logging.INFO,
) -> logging.Logger:
    """Set up a logger with file rotation and contextual formatting.

    Args:
        name: Logger name
        log_dir: Directory for log files (defaults to settings.logs_path)
        max_bytes: Maximum size of each log file before rotation (default: 50MB)
        backup_count: Number of backup files to keep (default: 8)
        log_level: Logging level (default: INFO)

    Returns:
        Configured logger instance
    """
    # Get or create logger
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Disable propagation to avoid duplicate logs from root logger
    logger.propagate = False

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # Determine log directory
    if log_dir is None:
        settings = get_settings()
        log_dir = settings.logs_path
    else:
        log_dir = Path(log_dir)

    # Ensure log directory exists
    log_dir.mkdir(parents=True, exist_ok=True)

    # Create log file path
    log_file = log_dir / f"{name}.log"

    # Create rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)

    # Create formatter with custom format
    # Format: 2026-03-06 05:28:22.319 [INFO] [session_id:1111,message_id:10804720] [filename:line] content
    formatter = ContextualFormatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)s] %(context)s %(location)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)

    # Add handler to logger
    logger.addHandler(file_handler)

    # Also add console handler for development
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


# Global logger instance
_logger: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    """Get the global logger instance.

    Returns:
        Logger instance
    """
    global _logger
    if _logger is None:
        _logger = setup_logger()
    return _logger


def set_session_id(session_id: str) -> None:
    """Set the session ID for the current context.

    Args:
        session_id: Session identifier
    """
    session_id_var.set(session_id)


def set_message_id(message_id: str) -> None:
    """Set the message ID for the current context.

    Args:
        message_id: Message identifier
    """
    message_id_var.set(message_id)


def clear_context() -> None:
    """Clear the contextual information (session_id and message_id)."""
    session_id_var.set(None)
    message_id_var.set(None)


# Convenience functions for logging
def debug(msg: str, *args, **kwargs) -> None:
    """Log a debug message."""
    kwargs.setdefault("stacklevel", 2)
    get_logger().debug(msg, *args, **kwargs)


def info(msg: str, *args, **kwargs) -> None:
    """Log an info message."""
    kwargs.setdefault("stacklevel", 2)
    get_logger().info(msg, *args, **kwargs)


def warning(msg: str, *args, **kwargs) -> None:
    """Log a warning message."""
    kwargs.setdefault("stacklevel", 2)
    get_logger().warning(msg, *args, **kwargs)


def error(msg: str, *args, **kwargs) -> None:
    """Log an error message."""
    kwargs.setdefault("stacklevel", 2)
    get_logger().error(msg, *args, **kwargs)


def exception(msg: str, *args, **kwargs) -> None:
    """Log an exception message with traceback."""
    kwargs.setdefault("stacklevel", 2)
    get_logger().exception(msg, *args, **kwargs)


def critical(msg: str, *args, **kwargs) -> None:
    """Log a critical message."""
    kwargs.setdefault("stacklevel", 2)
    get_logger().critical(msg, *args, **kwargs)
