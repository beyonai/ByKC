"""Runtime wiring helpers for knowledge base services."""

from __future__ import annotations

from os import getenv, getpid
from socket import gethostname
from typing import Any
from uuid import uuid4

from by_qa.config import Settings
from by_qa.core import logger
from by_qa.core.model_config import LLMModelProfile, ModelConfig, ModelConfigProvider
from by_qa.knowledge_base.events import (
    KnowledgeEventPublisherInvoker,
    load_knowledge_event_publisher,
)
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
from by_qa.knowledge_base.repositories.knowledge_entity_asset_repository import (
    KnowledgeEntityAssetRepository,
)
from by_qa.knowledge_base.repositories.knowledge_entity_repository import (
    KnowledgeEntityRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fetch_cache_repository import (
    KnowledgeFetchCacheRepository,
)
from by_qa.knowledge_base.repositories.knowledge_file_reference_repository import (
    KnowledgeFileReferenceRepository,
)
from by_qa.knowledge_base.repositories.knowledge_file_update_timeline_repository import (
    KnowledgeFileUpdateTimelineRepository,
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
from by_qa.knowledge_base.repositories.knowledge_semantic_processing_batch_repository import (
    KnowledgeSemanticProcessingBatchRepository,
)
from by_qa.knowledge_base.repositories.knowledge_semantic_processing_task_repository import (
    KnowledgeSemanticProcessingTaskRepository,
)
from by_qa.knowledge_base.repositories.metadata_search_repository import (
    MetadataSearchRepository,
)
from by_qa.knowledge_base.repositories.retrieval_projection_repository import (
    RetrievalProjectionRepository,
)
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
    normalize_entity_embedding_table_name,
)
from by_qa.knowledge_base.services.document_update_service import DocumentUpdateService
from by_qa.knowledge_base.services.embedding_query_service import EmbeddingQueryService
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError
from by_qa.knowledge_base.services.file_metadata_query_service import (
    FileMetadataQueryService,
)
from by_qa.knowledge_base.services.file_metadata_update_service import (
    FileMetadataUpdateService,
)
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService
from by_qa.knowledge_base.services.knowledge_entity_background_runner import (
    KnowledgeEntityBackgroundRunner,
)
from by_qa.knowledge_base.services.knowledge_entity_discovery import (
    KnowledgeEntityDiscovery,
)
from by_qa.knowledge_base.services.knowledge_entity_enrichment import (
    KnowledgeEntityEnricher,
)
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    build_discovery_llm,
    build_enrichment_llm,
)
from by_qa.knowledge_base.services.knowledge_entity_processing_service import (
    KnowledgeEntityProcessingOrchestrator,
)
from by_qa.knowledge_base.services.knowledge_entity_synonym_resolution import (
    KnowledgeEntityAssetService,
    KnowledgeEntitySynonymAdjudicator,
)
from by_qa.knowledge_base.services.knowledge_entity_task_worker import (
    KnowledgeEntityTaskWorker,
)
from by_qa.knowledge_base.services.knowledge_fetch_cache_cleanup_service import (
    KnowledgeFetchCacheCleanupService,
)
from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    KnowledgeItemIngestionService,
)
from by_qa.knowledge_base.services.knowledge_item_search_service import (
    KnowledgeItemSearchService,
)
from by_qa.knowledge_base.services.markdown_reference_resolver import (
    MarkdownReferenceResolver,
)
from by_qa.knowledge_base.services.markdown_reference_rewriter import (
    MarkdownReferenceRewriter,
)
from by_qa.knowledge_base.services.markdown_update_summary_service import (
    MarkdownUpdateSummaryService,
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
    bootstrap = await build_bootstrap_service(settings, provider=provider)
    embedding_dimension = (
        embedding_config.dimension
        if embedding_config is not None and embedding_config.dimension is not None
        else settings.embedding_dimension
    )
    return KnowledgeBaseService(
        connection_factory=build_connection_factory(settings),
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=KnowledgeFsEntryRepository(),
        knowledge_build_task_repository=KnowledgeBuildTaskRepository(),
        knowledge_item_chunk_repository=KnowledgeItemChunkRepository(
            bootstrap.embedding_table_name
        ),
        embedding_dimension=embedding_dimension,
        retrieval_projection_repository=RetrievalProjectionRepository(),
        knowledge_fetch_cache_repository=KnowledgeFetchCacheRepository(),
        storage_provider=await build_storage_provider(
            settings, embedding_config=embedding_config
        ),
        markdown_reference_resolver=MarkdownReferenceResolver(
            connection_factory=build_connection_factory(settings),
            reference_repository=KnowledgeFileReferenceRepository(),
        ),
        knowledge_file_reference_repository=KnowledgeFileReferenceRepository(),
        file_metadata_value_repository=FileMetadataValueRepository(),
        knowledge_entity_asset_repository=KnowledgeEntityAssetRepository(
            bootstrap.entity_embedding_table_name
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
    *,
    event_publisher_invoker: KnowledgeEventPublisherInvoker | None = None,
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
        knowledge_entity_asset_repository=KnowledgeEntityAssetRepository(
            bootstrap.entity_embedding_table_name
        ),
        file_metadata_value_repository=FileMetadataValueRepository(),
        knowledge_file_reference_repository=KnowledgeFileReferenceRepository(),
        markdown_reference_rewriter=MarkdownReferenceRewriter(),
        event_publisher_invoker=event_publisher_invoker
        or KnowledgeEventPublisherInvoker(
            publisher=load_knowledge_event_publisher(
                getattr(settings, "event_publisher_provider", "")
            ),
            timeout_seconds=getattr(settings, "event_publish_timeout_seconds", 5.0),
        ),
    )


async def build_document_update_service(
    settings: Settings,
    provider: ModelConfigProvider | None = None,
) -> DocumentUpdateService:
    """Build the transactional document-update service."""
    embedding_config = (
        await provider.get_config(LLMModelProfile.EMBEDDING)
        if provider is not None
        else None
    )
    validate_knowledge_base_settings(settings, embedding_config=embedding_config)
    bootstrap = await build_bootstrap_service(settings, provider=provider)
    return DocumentUpdateService(
        connection_factory=build_connection_factory(settings),
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=KnowledgeFsEntryRepository(),
        knowledge_item_chunk_repository=KnowledgeItemChunkRepository(
            bootstrap.embedding_table_name
        ),
        retrieval_projection_repository=RetrievalProjectionRepository(),
        knowledge_build_task_repository=KnowledgeBuildTaskRepository(),
        knowledge_fetch_cache_repository=KnowledgeFetchCacheRepository(),
        file_metadata_value_repository=FileMetadataValueRepository(),
        knowledge_file_reference_repository=KnowledgeFileReferenceRepository(),
        markdown_reference_rewriter=MarkdownReferenceRewriter(),
        storage_provider=await build_storage_provider(
            settings, embedding_config=embedding_config
        ),
        update_timeline_repository=KnowledgeFileUpdateTimelineRepository(),
        markdown_update_summary_service=MarkdownUpdateSummaryService(),
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
        markdown_reference_resolver=MarkdownReferenceResolver(
            connection_factory=build_connection_factory(settings),
            reference_repository=KnowledgeFileReferenceRepository(),
        ),
    )


async def build_knowledge_entity_processing_service(
    settings: Settings,
    provider: ModelConfigProvider | None = None,
    *,
    document_chunking_service: Any,
    event_publisher_invoker: KnowledgeEventPublisherInvoker | None = None,
) -> KnowledgeEntityProcessingOrchestrator:
    """Build the real discovery/enrichment orchestration service.

    The chunking service is injected by the application entrypoint so the
    ``knowledge_base`` package remains independent from ``knowledge_build``.
    """
    logger.info(
        "knowledge_entity_runtime build started: model_provider_configured=%s, document_chunking_service_type=%s",
        provider is not None,
        type(document_chunking_service).__name__,
    )
    embedding_config = (
        await provider.get_config(LLMModelProfile.EMBEDDING)
        if provider is not None
        else None
    )
    validate_knowledge_base_settings(settings, embedding_config=embedding_config)

    connection_factory = build_connection_factory(settings)
    knowledge_base_repository = KnowledgeBaseRepository()
    knowledge_entity_repository = KnowledgeEntityRepository()
    knowledge_entity_asset_repository = KnowledgeEntityAssetRepository(
        normalize_entity_embedding_table_name(
            embedding_config.model_name
            if embedding_config is not None
            else settings.embedding_model_name
        )
    )
    semantic_processing_task_repository = KnowledgeSemanticProcessingTaskRepository()
    semantic_processing_batch_repository = KnowledgeSemanticProcessingBatchRepository()
    knowledge_file_reference_repository = KnowledgeFileReferenceRepository()
    storage_provider = await build_storage_provider(
        settings, embedding_config=embedding_config
    )
    active_event_publisher_invoker = event_publisher_invoker or (
        KnowledgeEventPublisherInvoker(
            publisher=load_knowledge_event_publisher(
                getattr(settings, "event_publisher_provider", "")
            ),
            timeout_seconds=getattr(settings, "event_publish_timeout_seconds", 5.0),
        )
    )
    ingestion_service = await build_knowledge_item_ingestion_service(
        settings,
        provider=provider,
        event_publisher_invoker=active_event_publisher_invoker,
    )
    document_update_service = await build_document_update_service(
        settings, provider=provider
    )
    search_service = await build_knowledge_item_search_service(
        settings, provider=provider
    )

    # Discovery is an information-selection task. Its production model settings
    # are intentionally fixed and do not inherit a higher reasoning effort.
    discovery_llm = build_discovery_llm(provider=provider)
    # Entity pages are durable editorial artifacts. Keep generation deterministic
    # and bounded to low reasoning instead of inheriting conversational defaults.
    enrichment_llm = build_enrichment_llm(provider=provider)
    asset_service = KnowledgeEntityAssetService(
        connection_factory=connection_factory,
        knowledge_base_repository=knowledge_base_repository,
        asset_repository=knowledge_entity_asset_repository,
        fs_entry_repository=KnowledgeFsEntryRepository(),
        file_metadata_repository=FileMetadataValueRepository(),
        embedding_service=EmbeddingQueryService(provider=provider),
        adjudicator=KnowledgeEntitySynonymAdjudicator(discovery_llm),
        ingestion_service=ingestion_service,
        top_k=getattr(settings, "entity_synonym_top_k", 3),
        exact_alias_enabled=getattr(
            settings, "entity_synonym_exact_alias_enabled", True
        ),
        embedding_index_enabled=getattr(
            settings, "entity_embedding_index_enabled", True
        ),
        adjudication_enabled=getattr(
            settings, "entity_synonym_adjudication_enabled", True
        ),
    )
    worker = KnowledgeEntityTaskWorker(
        connection_factory=connection_factory,
        knowledge_entity_repository=knowledge_entity_repository,
        knowledge_file_reference_repository=knowledge_file_reference_repository,
        storage_provider=storage_provider,
        knowledge_item_ingestion_service=ingestion_service,
        document_update_service=document_update_service,
        document_chunking_service=document_chunking_service,
        knowledge_item_search_service=search_service,
        knowledge_entity_discovery=KnowledgeEntityDiscovery(discovery_llm),
        knowledge_entity_enricher=KnowledgeEntityEnricher(enrichment_llm),
        knowledge_entity_asset_service=asset_service,
    )
    service = KnowledgeEntityProcessingOrchestrator(
        connection_factory=connection_factory,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_entity_repository=knowledge_entity_repository,
        knowledge_semantic_processing_batch_repository=(
            semantic_processing_batch_repository
        ),
        knowledge_semantic_processing_task_repository=semantic_processing_task_repository,
        knowledge_file_reference_repository=knowledge_file_reference_repository,
        worker=worker,
        event_publisher_invoker=active_event_publisher_invoker,
        knowledge_entity_asset_service=asset_service,
    )
    if getattr(settings, "knowledge_entity_worker_enabled", False):
        worker_id = settings.knowledge_entity_worker_id.strip() or (
            f"{gethostname()}:{getpid()}:{uuid4().hex[:12]}"
        )
        service.background_runner = KnowledgeEntityBackgroundRunner(
            connection_factory=connection_factory,
            task_repository=semantic_processing_task_repository,
            batch_repository=semantic_processing_batch_repository,
            worker=worker,
            event_publisher_invoker=active_event_publisher_invoker,
            worker_id=worker_id,
            concurrency=settings.knowledge_entity_worker_concurrency,
            poll_seconds=settings.knowledge_entity_worker_poll_seconds,
            task_timeout_seconds=settings.knowledge_entity_task_timeout_seconds,
            lease_seconds=settings.knowledge_entity_lease_seconds,
            heartbeat_seconds=settings.knowledge_entity_heartbeat_seconds,
            reaper_seconds=settings.knowledge_entity_reaper_seconds,
            status_log_seconds=(settings.knowledge_entity_worker_status_log_seconds),
            shutdown_grace_seconds=(settings.knowledge_entity_shutdown_grace_seconds),
        )
    logger.info(
        "knowledge_entity_runtime build completed: orchestrator_type=%s, worker_type=%s, background_runner_enabled=%s",
        type(service).__name__,
        type(worker).__name__,
        service.background_runner is not None,
    )
    return service


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


async def build_file_metadata_update_service(
    settings: Settings,
) -> FileMetadataUpdateService:
    """Build the transactional file metadata update service."""
    validate_knowledge_base_settings(settings, require_embedding=False)
    return FileMetadataUpdateService(
        connection_factory=build_connection_factory(settings),
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=KnowledgeFsEntryRepository(),
        file_metadata_value_repository=FileMetadataValueRepository(),
    )
