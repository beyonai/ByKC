"""Application entrypoint with dynamic API module loading."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
from json import dumps
from typing import Any, Callable

from by_framework.core.discovery import ServiceRegistry
from redis.asyncio import Redis

from by_qa.config import get_settings
from by_qa.core import logger
from by_qa.core.model_config import LLMModelProfile, load_model_config_provider

settings = get_settings()
_request_model_config_provider: ContextVar[Any | None] = ContextVar(
    "request_model_config_provider", default=None
)

_knowledge_base_service: Any | None = None
_knowledge_item_ingestion_service: Any | None = None
_knowledge_item_search_service: Any | None = None
_knowledge_fetch_cache_cleanup_service: Any | None = None
_document_chunking_service: Any | None = None
_metadata_property_service: Any | None = None
_file_metadata_service: Any | None = None
_knowledge_base_schema_initialized = False
_knowledge_base_schema_lock = asyncio.Lock()


@dataclass(frozen=True)
class ApiModuleDefinition:
    """Declarative description of an optional API module."""

    name: str
    route_module: str
    register_function: str
    required_packages: tuple[str, ...]
    register_kwargs_factory: Callable[[], dict[str, Any]]


API_MODULES = (
    ApiModuleDefinition(
        name="knowledge_base",
        route_module="by_qa.knowledge_base.api.routes",
        register_function="register_routes",
        required_packages=("fastapi", "aioboto3", "psycopg"),
        register_kwargs_factory=lambda: {
            "get_knowledge_base_service": resolve_knowledge_base_service,
            "get_knowledge_item_ingestion_service": (
                resolve_knowledge_item_ingestion_service
            ),
            "get_knowledge_item_search_service": resolve_knowledge_item_search_service,
            "get_document_chunking_service": resolve_document_chunking_service,
            "get_metadata_property_service": resolve_metadata_property_service,
            "get_file_metadata_service": resolve_file_metadata_service,
        },
    ),
)


def _build_model_config_provider() -> Any:
    """Build a new model config provider instance."""
    return load_model_config_provider()


def _set_request_model_config_provider(provider: Any) -> Token[Any | None]:
    """Bind a model config provider to the current request context."""
    return _request_model_config_provider.set(provider)


def _reset_request_model_config_provider(token: Token[Any | None]) -> None:
    """Clear the current request-bound model config provider."""
    _request_model_config_provider.reset(token)


def _get_request_model_config_provider() -> Any | None:
    """Return the current request-bound model config provider, if any."""
    return _request_model_config_provider.get()


def _get_startup_configuration_summary() -> dict[str, Any]:
    """Build a safe startup configuration summary for logging."""
    return {
        "service_name": settings.service_name,
        "host": settings.host,
        "port": settings.port,
        "host_machine": settings.host_machine,
        "checkpointer_backend": settings.checkpointer_backend,
        "agent_data_path": str(settings.agent_data_path),
        "knowledge_base_configured": bool(
            _get_resolved_kb_opengauss_dsn() and settings.embedding_model_name
        ),
        "document_chunking_configured": bool(
            settings.embedding_model_name and settings.embedding_base_url
        ),
        "qa_llm_configured": bool(settings.llm_base_url and settings.llm_api_key),
    }


def _get_startup_configuration_gaps() -> list[str]:
    """Return the names of key startup settings that are currently missing."""
    missing: list[str] = []

    if not _get_resolved_kb_opengauss_dsn():
        missing.append("DB_HOST/DB_USER/DB_PASS")
    if not settings.embedding_model_name:
        missing.append("EMBEDDING_MODEL_NAME")
    if not settings.llm_api_key:
        missing.append("LLM_API_KEY")

    return missing


def _get_resolved_kb_opengauss_dsn() -> str:
    """Return the KB openGauss DSN from full Settings or lightweight test doubles."""
    resolved = getattr(settings, "resolved_kb_opengauss_dsn", None)
    if resolved:
        return resolved
    build_opengauss_dsn = getattr(settings, "build_opengauss_dsn", None)
    if callable(build_opengauss_dsn):
        return build_opengauss_dsn()
    return ""


def _log_startup_configuration() -> None:
    """Log a safe startup configuration summary and any key gaps."""
    summary = _get_startup_configuration_summary()
    logger.info(
        "application startup configuration: service_name=%s, host=%s, port=%s, "
        "host_machine=%s, checkpointer_backend=%s, agent_data_path=%s, "
        "knowledge_base_configured=%s, document_chunking_configured=%s, "
        "qa_llm_configured=%s",
        summary["service_name"],
        summary["host"],
        summary["port"],
        summary["host_machine"],
        summary["checkpointer_backend"],
        summary["agent_data_path"],
        summary["knowledge_base_configured"],
        summary["document_chunking_configured"],
        summary["qa_llm_configured"],
    )

    missing = _get_startup_configuration_gaps()
    if missing:
        logger.warning(
            "application startup configuration gaps: missing=%s",
            ",".join(missing),
        )


def _build_service_registry_client() -> Redis:
    """Build the Redis client used by the service registry."""
    redis_kwargs: dict[str, Any] = {
        "host": settings.redis_host,
        "port": settings.redis_port,
        "db": settings.redis_database,
        "password": settings.redis_password or None,
        "decode_responses": True,
    }
    if settings.redis_username:
        redis_kwargs["username"] = settings.redis_username
    return Redis(**redis_kwargs)


async def _register_service(application) -> None:
    """Register the running service instance in the service registry."""
    redis_client = _build_service_registry_client()
    registry = ServiceRegistry(redis_client=redis_client)
    metadata = {"version": "0.1.1"}
    await registry.register(
        service_name=settings.service_name,
        host=settings.host_machine,
        port=settings.port,
        weight=10,
        metadata=metadata,
    )
    application.state.service_registry = registry
    logger.info(
        "service registry registered: service_name=%s, host=%s, port=%s, metadata=%s",
        settings.service_name,
        settings.host_machine,
        settings.port,
        metadata,
    )
    logger.info(
        "service registry redis configured: host=%s, port=%s, db=%s, username_set=%s, password_set=%s",
        settings.redis_host,
        settings.redis_port,
        settings.redis_database,
        bool(settings.redis_username),
        bool(settings.redis_password),
    )


async def _unregister_service(application) -> None:
    """Unregister the running service instance from the service registry."""
    registry = getattr(application.state, "service_registry", None)
    if registry is None:
        return

    await registry.unregister()
    logger.info("service registry unregistered: service_name=%s", settings.service_name)
    application.state.service_registry = None


def get_adapter() -> None:
    """Compatibility placeholder for tests migrated from the source project."""
    return None


def get_instant_search_engine() -> None:
    """Compatibility placeholder for tests migrated from the source project."""
    return None


def get_knowledge_base_service():
    """Get or create the knowledge-base metadata service."""
    return _knowledge_base_service


async def _get_or_build_knowledge_base_service(provider: Any | None = None):
    """Get or build the knowledge-base metadata service."""
    global _knowledge_base_service
    request_provider = _get_request_model_config_provider()
    if request_provider is not None and provider is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_knowledge_base_service,
        )

        await _ensure_knowledge_base_schema_initialized(provider=request_provider)
        return await build_knowledge_base_service(settings, provider=request_provider)

    if _knowledge_base_service is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_knowledge_base_service,
        )

        active_provider = provider or _build_model_config_provider()
        await _ensure_knowledge_base_schema_initialized(provider=active_provider)
        _knowledge_base_service = await build_knowledge_base_service(
            settings, provider=active_provider
        )
    return _knowledge_base_service


def get_knowledge_item_ingestion_service():
    """Get or create the knowledge-item ingestion service."""
    return _knowledge_item_ingestion_service


async def _get_or_build_knowledge_item_ingestion_service(provider: Any | None = None):
    """Get or build the knowledge-item ingestion service."""
    global _knowledge_item_ingestion_service
    request_provider = _get_request_model_config_provider()
    if request_provider is not None and provider is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_knowledge_item_ingestion_service,
        )

        await _ensure_knowledge_base_schema_initialized(provider=request_provider)
        return await build_knowledge_item_ingestion_service(
            settings, provider=request_provider
        )

    if _knowledge_item_ingestion_service is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_knowledge_item_ingestion_service,
        )

        active_provider = provider or _build_model_config_provider()
        await _ensure_knowledge_base_schema_initialized(provider=active_provider)
        _knowledge_item_ingestion_service = (
            await build_knowledge_item_ingestion_service(
                settings, provider=active_provider
            )
        )
    return _knowledge_item_ingestion_service


def get_knowledge_item_search_service():
    """Get or create the knowledge-item search service."""
    return _knowledge_item_search_service


async def _get_or_build_knowledge_item_search_service(provider: Any | None = None):
    """Get or build the knowledge-item search service."""
    global _knowledge_item_search_service
    request_provider = _get_request_model_config_provider()
    if request_provider is not None and provider is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_knowledge_item_search_service,
        )

        await _ensure_knowledge_base_schema_initialized(provider=request_provider)
        return await build_knowledge_item_search_service(
            settings, provider=request_provider
        )

    if _knowledge_item_search_service is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_knowledge_item_search_service,
        )

        active_provider = provider or _build_model_config_provider()
        await _ensure_knowledge_base_schema_initialized(provider=active_provider)
        _knowledge_item_search_service = await build_knowledge_item_search_service(
            settings, provider=active_provider
        )
    return _knowledge_item_search_service


def get_knowledge_fetch_cache_cleanup_service():
    """Get or create the fetched-file cache cleanup service."""
    return _knowledge_fetch_cache_cleanup_service


async def _get_or_build_knowledge_fetch_cache_cleanup_service():
    """Get or build the fetched-file cache cleanup service."""
    global _knowledge_fetch_cache_cleanup_service
    if _knowledge_fetch_cache_cleanup_service is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_knowledge_fetch_cache_cleanup_service,
        )

        _knowledge_fetch_cache_cleanup_service = (
            build_knowledge_fetch_cache_cleanup_service(settings)
        )
    return _knowledge_fetch_cache_cleanup_service


def get_document_chunking_service():
    """Get or create the document chunking service."""
    global _document_chunking_service
    if _document_chunking_service is None:
        from by_qa.knowledge_build.runtime import build_document_chunking_service

        _document_chunking_service = build_document_chunking_service(settings)
    return _document_chunking_service


async def _get_or_build_document_chunking_service(provider: Any | None = None):
    """Get or build the document chunking service with configured embedding provider."""
    global _document_chunking_service
    request_provider = _get_request_model_config_provider()
    if request_provider is not None and provider is None:
        from by_qa.knowledge_build.runtime import build_document_chunking_service

        embedding_config = await request_provider.get_config(LLMModelProfile.EMBEDDING)
        return build_document_chunking_service(
            settings, embedding_config=embedding_config
        )

    if _document_chunking_service is None:
        from by_qa.knowledge_build.runtime import build_document_chunking_service

        active_provider = provider or _build_model_config_provider()
        embedding_config = await active_provider.get_config(LLMModelProfile.EMBEDDING)
        _document_chunking_service = build_document_chunking_service(
            settings, embedding_config=embedding_config
        )
    return _document_chunking_service


async def resolve_knowledge_base_service():
    """Resolve the KB service dynamically so tests can monkeypatch the factory."""
    return await _get_or_build_knowledge_base_service()


async def resolve_knowledge_item_ingestion_service():
    """Resolve the ingestion service dynamically so tests can monkeypatch the factory."""
    return await _get_or_build_knowledge_item_ingestion_service()


async def resolve_knowledge_item_search_service():
    """Resolve the search service dynamically so tests can monkeypatch the factory."""
    return await _get_or_build_knowledge_item_search_service()


async def resolve_document_chunking_service():
    """Resolve the document chunking service dynamically for optional modules."""
    return await _get_or_build_document_chunking_service()


def get_metadata_property_service():
    """Get or create the metadata property definition service."""
    return _metadata_property_service


async def _get_or_build_metadata_property_service():
    """Get or build the metadata property definition service."""
    global _metadata_property_service
    if _metadata_property_service is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_metadata_property_service,
        )

        _metadata_property_service = await build_metadata_property_service(settings)
    return _metadata_property_service


async def resolve_metadata_property_service():
    """Resolve the metadata property service dynamically so tests can monkeypatch."""
    return await _get_or_build_metadata_property_service()


def get_file_metadata_service():
    """Get or create the file metadata value service."""
    return _file_metadata_service


async def _get_or_build_file_metadata_service():
    """Get or build the file metadata value service."""
    global _file_metadata_service
    if _file_metadata_service is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_file_metadata_service,
        )

        _file_metadata_service = await build_file_metadata_service(settings)
    return _file_metadata_service


async def resolve_file_metadata_service():
    """Resolve the file metadata service dynamically so tests can monkeypatch."""
    return await _get_or_build_file_metadata_service()


def _detect_missing_packages(required_packages: tuple[str, ...]) -> list[str]:
    """Return missing optional dependency package names."""
    return [package for package in required_packages if find_spec(package) is None]


def _register_api_modules(application) -> tuple[list[str], dict[str, list[str]]]:
    """Register optional API modules whose dependencies are installed."""
    loaded_modules: list[str] = []
    skipped_modules: dict[str, list[str]] = {}

    for module_definition in API_MODULES:
        missing_packages = _detect_missing_packages(module_definition.required_packages)
        if missing_packages:
            skipped_modules[module_definition.name] = missing_packages
            logger.warning(
                "api module skipped: module=%s, missing_packages=%s",
                module_definition.name,
                ",".join(missing_packages),
            )
            continue

        register_module = import_module(module_definition.route_module)
        register_routes = getattr(register_module, module_definition.register_function)
        register_routes(application, **module_definition.register_kwargs_factory())
        loaded_modules.append(module_definition.name)
        logger.info(
            "api module registered: module=%s, route_module=%s",
            module_definition.name,
            module_definition.route_module,
        )

    return loaded_modules, skipped_modules


async def _initialize_knowledge_base_runtime(enabled_modules: list[str]) -> None:
    """Initialize optional runtime services for the knowledge-base module."""
    if "knowledge_base" not in enabled_modules:
        logger.info("knowledge_base lifecycle skipped: module_not_loaded")
        return

    logger.info("knowledge_base lifecycle initialized lazily")


async def _ensure_knowledge_base_schema_initialized(
    provider: Any | None = None,
) -> None:
    """Apply KB schema bootstrap on the first runtime request that needs it."""
    global _knowledge_base_schema_initialized
    if _knowledge_base_schema_initialized:
        return

    async with _knowledge_base_schema_lock:
        if _knowledge_base_schema_initialized:
            return

        active_provider = provider or _build_model_config_provider()
        from by_qa.knowledge_base.infrastructure.database import (
            build_connection_factory,
        )
        from by_qa.knowledge_base.infrastructure.runtime import build_bootstrap_service

        bootstrap = await build_bootstrap_service(settings, provider=active_provider)
        kb_connection = await build_connection_factory(settings)()
        try:
            await bootstrap.apply(kb_connection)
        finally:
            await kb_connection.close()

        cleanup = await _get_or_build_knowledge_fetch_cache_cleanup_service()
        await cleanup.start()
        _knowledge_base_schema_initialized = True
        logger.info("knowledge_base schema initialized lazily")


async def _shutdown_knowledge_base_runtime(enabled_modules: list[str]) -> None:
    """Stop optional runtime services when the application shuts down."""
    if (
        "knowledge_base" in enabled_modules
        and _knowledge_fetch_cache_cleanup_service is not None
    ):
        await _knowledge_fetch_cache_cleanup_service.stop()
        logger.info("knowledge_base lifecycle stopped")


@asynccontextmanager
async def lifespan(application):
    """Application lifecycle hooks."""
    settings.ensure_directories()
    enabled_modules = getattr(application.state, "enabled_modules", [])
    _log_startup_configuration()
    await _register_service(application)
    logger.info(
        "application startup: enabled_modules=%s",
        ",".join(enabled_modules) if enabled_modules else "none",
    )

    yield

    await _shutdown_knowledge_base_runtime(enabled_modules)
    await _unregister_service(application)


def create_app():
    """Create the FastAPI application with optional module registration."""
    from fastapi import FastAPI, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    application = FastAPI(
        title="by_qa Service",
        description="Open-source modular knowledge and QA service.",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def bind_api_request_model_config_provider(
        request: Request, call_next
    ) -> Any:
        """Create one model config provider for each API request."""
        if not request.url.path.startswith("/api/v1/"):
            return await call_next(request)

        provider = _build_model_config_provider()
        token = _set_request_model_config_provider(provider)
        try:
            return await call_next(request)
        finally:
            _reset_request_model_config_provider(token)

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Return a standardized validation envelope for API requests."""

        def _json_safe(value: Any) -> Any:
            if isinstance(value, dict):
                return {str(key): _json_safe(item) for key, item in value.items()}
            if isinstance(value, list):
                return [_json_safe(item) for item in value]
            if isinstance(value, tuple):
                return [_json_safe(item) for item in value]
            try:
                dumps(value)
            except TypeError:
                return str(value)
            return value

        errors = _json_safe(exc.errors())
        if not request.url.path.startswith("/api/v1/"):
            return JSONResponse(status_code=422, content={"detail": errors})
        return JSONResponse(
            status_code=200,
            content={
                "resultCode": "-1",
                "resultMsg": "request validation failed",
                "resultObject": {
                    "errors": errors,
                },
            },
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_exception(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Return a standardized 500 envelope for unexpected API failures."""
        if not request.url.path.startswith("/api/v1/"):
            return JSONResponse(
                status_code=500, content={"detail": str(exc) or "internal error"}
            )
        return JSONResponse(
            status_code=200,
            content={
                "resultCode": "-1",
                "resultMsg": str(exc) or "internal error",
                "resultObject": {},
            },
        )

    loaded_modules, skipped_modules = _register_api_modules(application)
    application.state.enabled_modules = loaded_modules
    application.state.skipped_modules = skipped_modules

    @application.get("/health")
    async def health() -> dict[str, Any]:
        """Basic health probe with module-loading diagnostics."""
        return {
            "status": "ok",
            "enabled_modules": loaded_modules,
            "skipped_modules": skipped_modules,
        }

    return application


try:
    app = create_app()
except ImportError:
    app = None


async def async_main() -> None:
    """Run the local development server."""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "uvicorn is required to run the API server. "
            "Install by-qa[knowledge] or by-qa[all]."
        ) from exc

    config = uvicorn.Config(
        "by_qa.main:create_app",
        host=settings.host,
        port=settings.port,
        reload=False,
        factory=True,
    )
    server = uvicorn.Server(config)
    await server.serve()


def main() -> None:
    """Run the async CLI entrypoint in a dedicated event loop."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
