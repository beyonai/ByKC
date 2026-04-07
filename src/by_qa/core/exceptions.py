"""Core exceptions for deep search engine.

This module defines the exception hierarchy used by the core engine.
All exceptions should inherit from DeepSearchError.
"""


class DeepSearchError(Exception):
    """Base exception for all deep search errors."""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class SearchError(DeepSearchError):
    """Exception raised when search execution fails."""

    pass


class ValidationError(DeepSearchError):
    """Exception raised when input validation fails."""

    pass


class ConfigurationError(DeepSearchError):
    """Exception raised when there's a configuration issue."""

    pass


class RetrievalError(DeepSearchError):
    """Exception raised when document retrieval fails."""

    pass


class GenerationError(DeepSearchError):
    """Exception raised when answer generation fails."""

    pass
