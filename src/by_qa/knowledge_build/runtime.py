"""Runtime wiring helpers for knowledge build services."""

from by_qa.config import Settings
from by_qa.knowledge_build.services.document_chunking_service import (
    DocumentChunkingService,
)
from by_qa.knowledge_common.exceptions import KnowledgeConfigurationError


def validate_knowledge_build_settings(settings: Settings) -> None:
    """Fail fast when knowledge-build runtime settings are incomplete."""
    missing_fields: list[str] = []
    if not settings.embedding_model_name:
        missing_fields.append("EMBEDDING_MODEL_NAME")
    if not settings.embedding_base_url:
        missing_fields.append("EMBEDDING_BASE_URL")
    if settings.embedding_dimension <= 0:
        missing_fields.append("EMBEDDING_DIMENSION")

    if missing_fields:
        raise KnowledgeConfigurationError(
            "Knowledge-build runtime configuration is incomplete. "
            f"Please set: {', '.join(missing_fields)}"
        )


def build_document_chunking_service(settings: Settings) -> DocumentChunkingService:
    """Build the server-side document chunking and embedding service."""
    validate_knowledge_build_settings(settings)
    return DocumentChunkingService(
        embedding_base_url=settings.embedding_base_url,
        embedding_api_key=settings.embedding_api_key,
        embedding_model_name=settings.embedding_model_name,
        embedding_dimension=settings.embedding_dimension,
    )
