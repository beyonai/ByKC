"""Service-layer exceptions for knowledge base ingestion."""


class KnowledgeBaseConfigurationError(RuntimeError):
    """Raised when required knowledge-base runtime configuration is missing."""


class KnowledgeBaseValidationError(ValueError):
    """Raised when knowledge base input violates business constraints."""
