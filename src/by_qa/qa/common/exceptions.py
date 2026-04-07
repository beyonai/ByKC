"""Exceptions shared across the QA domain."""


class QAError(Exception):
    """Base exception for QA capability failures."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class SearchError(QAError):
    """Exception raised when QA execution fails."""


class ValidationError(QAError):
    """Exception raised when QA input validation fails."""


class ConfigurationError(QAError):
    """Exception raised when QA configuration is incomplete."""


class RetrievalError(QAError):
    """Exception raised when QA retrieval fails."""


class GenerationError(QAError):
    """Exception raised when QA answer generation fails."""
