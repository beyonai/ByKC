"""Service-layer exceptions for knowledge base ingestion."""

from by_qa.knowledge_common.exceptions import KnowledgeConfigurationError

KnowledgeBaseConfigurationError = KnowledgeConfigurationError


class KnowledgeBaseValidationError(ValueError):
    """Raised when knowledge base input violates business constraints."""
