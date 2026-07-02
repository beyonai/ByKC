"""Runtime wiring helpers for knowledge base services."""

from __future__ import annotations

from os import getenv
from typing import Any

from by_qa.config import Settings
from by_qa.core.model_config import LLMModelProfile, ModelConfig, ModelConfigProvider
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.infrastructure.object_storage import (
    KnowledgeBaseObjectStorage,
)
from by_qa.knowledge_base.infrastructure.storage import (
    KnowledgeStorageProvider,
    load_storage_provider,
)
from by_qa.knowledge_base.infrastructure.storage_s3 import S3KnowledgeStorageProvider
from by_qa.knowledge_base.repositories.file_metadata_value_repository import (
    FileMetadataValueRepository,
)
from by_qa.knowledge_base.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from by_qa.knowledge_base.repositories.knowledge_build_task_repository import (
    KnowledgeBuildTaskRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fetch_cache_repository import (
    KnowledgeFetchCacheRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fs_entry_repository import (
    KnowledgeFsEntryRepository,
)
from by_qa.knowledge_base.repositories.knowledge_item_chunk_repository import (
    KnowledgeItemChunkRepository,
)
from by_qa.knowledge_base.repositories.knowledge_item_search_repository import (
    KnowledgeItemSearchRepository,
)
from by_qa.knowledge_base.repositories.metadata_search_repository import (
    MetadataSearchRepository,
)
from by_qa.knowledge_base.repositories.retrieval_projection_repository import (
    RetrievalProjectionRepository,
)
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
)
from by_qa.knowledge_base.services.embedding_query_service import EmbeddingQueryService
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError
from by_qa.knowledge_base.services.file_metadata_query_service import (
    FileMetadataQueryService,
)
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService
from by_qa.knowledge_base.services.knowledge_fetch_cache_cleanup_service import (
    KnowledgeFetchCacheCleanupService,
)
from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    KnowledgeItemIngestionService,
)
from by_qa.knowledge_base.services.knowledge_item_search_service import (
    KnowledgeItemSearchService,
)


def validate_knowledge_base_settings(
    settings: Settings,
    *,
    embedding_config: ModelConfig | None = None,
    require_embedding: bool = True,
) -> None:
    """Fail fast with a clear message when KB runtime settings are incomplete."""
    missing_fields: list[str] = []
    if not settings.resolved_kb_opengauss_dsn:
        missing_fields.append("DB_HOST/DB_USER/DB_PASS")

    using_custom_provider = bool(getenv("BY_QA_STORAGE_PROVIDER", "").strip())
    if not using_custom_provider:
        if not settings.kb_minio_endpoint:
            missing_fields.append("MINIO_ENDPOINT")
        if not settings.kb_minio_access_key:
            missing_fields.append("MINIO_ACCESS_KEY")
        if not settings.kb_minio_secret_key:
            missing_fields.append("MINIO_SECRET_KEY")
        if not settings.kb_minio_bucket:
            missing_fields.append("KB_MINIO_BUCKET")
        if not settings.kb_minio_markdown_bucket:
            missing_fields.append("KB_MINIO_MARKDOWN_BUCKET")
    if require_embedding:
        embedding_model_name = (
            embedding_config.model_name
            if embedding_config
            else settings.embedding_model_name
        )
        embedding_dimension = (
            embedding_config.dimension
            if embedding_config and embedding_config.dimension is not None
            else settings.embedding_dimension
        )
        if not embedding_model_name:
            missing_fields.append("EMBEDDING_MODEL_NAME")
        if embedding_dimension <= 0:
            missing_fields.append("EMBEDDING_DIMENSION")

    if missing_fields:
        raise KnowledgeBaseConfigurationError(
            "Knowledge-base runtime configuration is incomplete. "
            f"Please set: {', '.join(missing_fields)}"
        )


def build_default_s3_storage_provider(settings: Settings) -> S3KnowledgeStorageProvider:
    """Build the default MinIO/S3 storage provider without ensure_ready()."""
    from by_qa.knowledge_base.infrastructure.storage_s3 import build_s3_storage_provider

    return build_s3_storage_provider(settings)


async def build_storage_provider(
    settings: Settings,
    *,
    embedding_config: ModelConfig | None = None,
) -> KnowledgeStorageProvider:
    """Build the storage provider, performing ensure_ready() on the resolved instance."""
    validate_knowledge_base_settings(settings, embedding_config=embedding_config)
    provider = load_storage_provider()
    await provider.ensure_ready()
    return provider


async def build_object_storage(
    settings: Settings, *, embedding_config: ModelConfig | None = None
) -> KnowledgeBaseObjectStorage:
    """Build the async S3-compatible object storage service.

    Deprecated: prefer build_storage_provider() for new code.
    This remains for backward compatibility during migration.
    """
    validate_knowledge_base_settings(settings, embedding_config=embedding_config)
    import aioboto3

    scheme = "https" if settings.kb_minio_secure else "http"
    endpoint = settings.kb_minio_endpoint.removeprefix("http://").removeprefix(
        "https://"
    )
    endpoint_url = f"{scheme}://{endpoint}"

    storage = KnowledgeBaseObjectStorage(
        session=aioboto3.Session(),
        endpoint_url=endpoint_url,
        access_key=settings.kb_minio_access_key,
        secret_key=settings.kb_minio_secret_key,
        secure=settings.kb_minio_secure,
        bucket_name=settings.kb_minio_bucket,
        markdown_bucket_name=settings.kb_minio_markdown_bucket,
    )
    await storage.ensure_buckets()
    return storage


async def build_bootstrap_service(
    settings: Settings,
    provider: ModelConfigProvider | None = None,
) -> KnowledgeBaseSchemaBootstrapService:
    """Build the schema bootstrap service for the configured embedding model."""
    if provider is not None:
        embedding_config = await provider.get_config(LLMModelProfile.EMBEDDING)
        model_name = embedding_config.model_name
        dimension = embedding_config.dimension or settings.embedding_dimension
    else:
        embedding_config = None
        model_name = settings.embedding_model_name
        dimension = settings.embedding_dimension
    validate_knowledge_base_settings(settings, embedding_config=embedding_config)
    return KnowledgeBaseSchemaBootstrapService(
        embedding_model_name=model_name,
        embedding_dimension=dimension,
    )


async def build_knowledge_base_service(
    settings: Settings,
    provider: ModelConfigProvider | None = None,
) -> KnowledgeBaseService:
    """Build the knowledge base metadata service."""
    embedding_config = (
        await provider.get_config(LLMModelProfile.EMBEDDING)
        if provider is not None
        else None
    )
    validate_knowledge_base_settings(settings, embedding_config=embedding_config)
    return KnowledgeBaseService(
        connection_factory=build_connection_factory(settings),
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=KnowledgeFsEntryRepository(),
        knowledge_build_task_repository=KnowledgeBuildTaskRepository(),
        retrieval_projection_repository=RetrievalProjectionRepository(),
        knowledge_fetch_cache_repository=KnowledgeFetchCacheRepository(),
        storage_provider=await build_storage_provider(
            settings, embedding_config=embedding_config
        ),
        cache_root=settings.kb_cache_path,
        cache_ttl_seconds=settings.kb_fetch_cache_ttl_seconds,
    )


def build_knowledge_fetch_cache_cleanup_service(
    settings: Settings,
) -> KnowledgeFetchCacheCleanupService:
    """Build the periodic fetched-file cache cleanup service."""
    validate_knowledge_base_settings(settings, require_embedding=False)
    return KnowledgeFetchCacheCleanupService(
        connection_factory=build_connection_factory(settings),
        knowledge_fetch_cache_repository=KnowledgeFetchCacheRepository(),
        cleanup_interval_seconds=settings.kb_fetch_cache_cleanup_interval_seconds,
    )


async def build_knowledge_item_ingestion_service(
    settings: Settings,
    provider: ModelConfigProvider | None = None,
) -> KnowledgeItemIngestionService:
    """Build the document ingestion service."""
    if provider is not None:
        embedding_config = await provider.get_config(LLMModelProfile.EMBEDDING)
        dimension = embedding_config.dimension or settings.embedding_dimension
    else:
        embedding_config = None
        dimension = settings.embedding_dimension
    validate_knowledge_base_settings(settings, embedding_config=embedding_config)
    bootstrap = await build_bootstrap_service(settings, provider=provider)
    return KnowledgeItemIngestionService(
        connection_factory=build_connection_factory(settings),
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=KnowledgeFsEntryRepository(),
        knowledge_build_task_repository=KnowledgeBuildTaskRepository(),
        knowledge_item_chunk_repository=KnowledgeItemChunkRepository(
            bootstrap.embedding_table_name
        ),
        retrieval_projection_repository=RetrievalProjectionRepository(),
        storage_provider=await build_storage_provider(
            settings, embedding_config=embedding_config
        ),
        embedding_dimension=dimension,
        knowledge_fetch_cache_repository=KnowledgeFetchCacheRepository(),
        file_metadata_value_repository=FileMetadataValueRepository(),
    )


async def build_knowledge_item_search_service(
    settings: Settings,
    provider: ModelConfigProvider | None = None,
) -> KnowledgeItemSearchService:
    """Build the knowledge-base hybrid retrieval service."""
    embedding_config = (
        await provider.get_config(LLMModelProfile.EMBEDDING)
        if provider is not None
        else None
    )
    validate_knowledge_base_settings(settings, embedding_config=embedding_config)
    bootstrap = await build_bootstrap_service(settings, provider=provider)
    return KnowledgeItemSearchService(
        connection_factory=build_connection_factory(settings),
        search_repository=KnowledgeItemSearchRepository(bootstrap.embedding_table_name),
        embedding_query_service=EmbeddingQueryService(provider=provider),
        metadata_search_repository=MetadataSearchRepository(),
    )


async def build_metadata_search_service(
    settings: Settings,
) -> Any:
    """Build the pure metadata search service."""
    from by_qa.knowledge_base.services.metadata_search_service import (
        MetadataSearchService,
    )

    validate_knowledge_base_settings(settings, require_embedding=False)
    return MetadataSearchService(
        connection_factory=build_connection_factory(settings),
        knowledge_base_repository=KnowledgeBaseRepository(),
        metadata_search_repository=MetadataSearchRepository(),
    )


async def build_file_metadata_query_service(
    settings: Settings,
) -> FileMetadataQueryService:
    """Build the read-only file metadata query service."""
    validate_knowledge_base_settings(settings, require_embedding=False)
    return FileMetadataQueryService(
        connection_factory=build_connection_factory(settings),
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=KnowledgeFsEntryRepository(),
        file_metadata_value_repository=FileMetadataValueRepository(),
    )
