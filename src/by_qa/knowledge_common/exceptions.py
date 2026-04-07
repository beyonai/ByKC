"""Shared exceptions for knowledge-related modules."""


class KnowledgeConfigurationError(RuntimeError):
    """Raised when required runtime configuration for a knowledge module is missing."""
