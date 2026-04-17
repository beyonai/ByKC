"""Runtime wiring helpers for knowledge build services."""

from by_qa.config import Settings
from by_qa.core.model_config import ModelConfig
from by_qa.knowledge_build.services.document_chunking_service import (
    DocumentChunkingService,
)
from by_qa.knowledge_common.exceptions import KnowledgeConfigurationError


def validate_knowledge_build_settings(
    settings: Settings, embedding_config: ModelConfig | None = None
) -> None:
    """Fail fast when knowledge-build runtime settings are incomplete."""
    missing_fields: list[str] = []
    embedding_model_name = (
        embedding_config.model_name
        if embedding_config
        else settings.embedding_model_name
    )
    embedding_base_url = (
        embedding_config.base_url if embedding_config else settings.embedding_base_url
    )
    embedding_dimension = (
        embedding_config.dimension
        if embedding_config and embedding_config.dimension is not None
        else settings.embedding_dimension
    )
    if not embedding_model_name:
        missing_fields.append("EMBEDDING_MODEL_NAME")
    if not embedding_base_url:
        missing_fields.append("EMBEDDING_BASE_URL")
    if embedding_dimension <= 0:
        missing_fields.append("EMBEDDING_DIMENSION")

    if missing_fields:
        raise KnowledgeConfigurationError(
            "Knowledge-build runtime configuration is incomplete. "
            f"Please set: {', '.join(missing_fields)}"
        )


def build_document_chunking_service(
    settings: Settings, embedding_config: ModelConfig | None = None
) -> DocumentChunkingService:
    """Build the server-side document chunking and embedding service."""
    validate_knowledge_build_settings(settings, embedding_config=embedding_config)
    embedding_model_name = (
        embedding_config.model_name
        if embedding_config
        else settings.embedding_model_name
    )
    embedding_base_url = (
        embedding_config.base_url if embedding_config else settings.embedding_base_url
    )
    embedding_api_key = (
        embedding_config.api_key if embedding_config else settings.embedding_api_key
    )
    embedding_dimension = (
        embedding_config.dimension
        if embedding_config and embedding_config.dimension is not None
        else settings.embedding_dimension
    )
    embedding_batch_max_texts = (
        embedding_config.batch_max_texts
        if embedding_config and embedding_config.batch_max_texts is not None
        else settings.embedding_batch_max_texts
    )
    return DocumentChunkingService(
        embedding_base_url=embedding_base_url,
        embedding_api_key=embedding_api_key,
        embedding_model_name=embedding_model_name,
        embedding_dimension=embedding_dimension,
        embedding_batch_max_texts=embedding_batch_max_texts,
    )
