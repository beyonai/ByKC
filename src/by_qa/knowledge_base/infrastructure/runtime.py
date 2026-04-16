"""Runtime wiring helpers for knowledge base services."""

from by_qa.config import Settings
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.infrastructure.object_storage import (
    KnowledgeBaseObjectStorage,
)
from by_qa.knowledge_base.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
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
from by_qa.knowledge_base.repositories.retrieval_projection_repository import (
    RetrievalProjectionRepository,
)
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
)
from by_qa.knowledge_base.services.embedding_query_service import EmbeddingQueryService
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError
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


def validate_knowledge_base_settings(settings: Settings) -> None:
    """Fail fast with a clear message when KB runtime settings are incomplete."""
    missing_fields: list[str] = []
    if not settings.resolved_kb_opengauss_dsn:
        missing_fields.append("DB_HOST/DB_USER/DB_PASS")
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
    if not settings.embedding_model_name:
        missing_fields.append("EMBEDDING_MODEL_NAME")
    if settings.embedding_dimension <= 0:
        missing_fields.append("EMBEDDING_DIMENSION")

    if missing_fields:
        raise KnowledgeBaseConfigurationError(
            "Knowledge-base runtime configuration is incomplete. "
            f"Please set: {', '.join(missing_fields)}"
        )


async def build_object_storage(settings: Settings) -> KnowledgeBaseObjectStorage:
    """Build the async S3-compatible object storage service."""
    validate_knowledge_base_settings(settings)
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


def build_bootstrap_service(settings: Settings) -> KnowledgeBaseSchemaBootstrapService:
    """Build the schema bootstrap service for the configured embedding model."""
    validate_knowledge_base_settings(settings)
    return KnowledgeBaseSchemaBootstrapService(
        embedding_model_name=settings.embedding_model_name,
        embedding_dimension=settings.embedding_dimension,
    )


async def build_knowledge_base_service(settings: Settings) -> KnowledgeBaseService:
    """Build the knowledge base metadata service."""
    validate_knowledge_base_settings(settings)
    return KnowledgeBaseService(
        connection_factory=build_connection_factory(settings),
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=KnowledgeFsEntryRepository(),
        retrieval_projection_repository=RetrievalProjectionRepository(),
        knowledge_fetch_cache_repository=KnowledgeFetchCacheRepository(),
        object_storage=await build_object_storage(settings),
        cache_root=settings.kb_cache_path,
        cache_ttl_seconds=settings.kb_fetch_cache_ttl_seconds,
    )


def build_knowledge_fetch_cache_cleanup_service(
    settings: Settings,
) -> KnowledgeFetchCacheCleanupService:
    """Build the periodic fetched-file cache cleanup service."""
    validate_knowledge_base_settings(settings)
    return KnowledgeFetchCacheCleanupService(
        connection_factory=build_connection_factory(settings),
        knowledge_fetch_cache_repository=KnowledgeFetchCacheRepository(),
        cleanup_interval_seconds=settings.kb_fetch_cache_cleanup_interval_seconds,
    )


async def build_knowledge_item_ingestion_service(
    settings: Settings,
) -> KnowledgeItemIngestionService:
    """Build the document ingestion service."""
    validate_knowledge_base_settings(settings)
    bootstrap = build_bootstrap_service(settings)
    return KnowledgeItemIngestionService(
        connection_factory=build_connection_factory(settings),
        knowledge_base_repository=KnowledgeBaseRepository(),
        knowledge_fs_entry_repository=KnowledgeFsEntryRepository(),
        knowledge_item_chunk_repository=KnowledgeItemChunkRepository(
            bootstrap.embedding_table_name
        ),
        retrieval_projection_repository=RetrievalProjectionRepository(),
        object_storage=await build_object_storage(settings),
        embedding_dimension=settings.embedding_dimension,
    )


def build_knowledge_item_search_service(
    settings: Settings,
) -> KnowledgeItemSearchService:
    """Build the knowledge-base hybrid retrieval service."""
    validate_knowledge_base_settings(settings)
    bootstrap = build_bootstrap_service(settings)
    return KnowledgeItemSearchService(
        connection_factory=build_connection_factory(settings),
        search_repository=KnowledgeItemSearchRepository(bootstrap.embedding_table_name),
        embedding_query_service=EmbeddingQueryService(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model_name=settings.embedding_model_name,
        ),
    )
