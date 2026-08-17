"""Tests for centralized application logging configuration."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from by_qa.core import logger as logger_module


def _close_handlers(configured_logger: logging.Logger) -> None:
    for handler in configured_logger.handlers[:]:
        configured_logger.removeHandler(handler)
        handler.close()


def test_setup_logger_uses_configured_level_for_logger_and_handlers(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        logger_module,
        "get_settings",
        lambda: SimpleNamespace(log_level="DEBUG", logs_path=tmp_path),
    )
    configured_logger = logger_module.setup_logger(name="test-configured-log-level")
    try:
        assert configured_logger.level == logging.DEBUG
        assert configured_logger.handlers
        assert all(
            handler.level == logging.DEBUG for handler in configured_logger.handlers
        )
    finally:
        _close_handlers(configured_logger)


def test_setup_logger_keeps_explicit_integer_level_and_updates_existing_handlers(
    tmp_path,
) -> None:
    configured_logger = logger_module.setup_logger(
        name="test-explicit-log-level",
        log_dir=str(tmp_path),
        log_level=logging.DEBUG,
    )
    try:
        same_logger = logger_module.setup_logger(
            name="test-explicit-log-level",
            log_dir=str(tmp_path),
            log_level=logging.INFO,
        )

        assert same_logger is configured_logger
        assert same_logger.level == logging.INFO
        assert all(handler.level == logging.INFO for handler in same_logger.handlers)
    finally:
        _close_handlers(configured_logger)
