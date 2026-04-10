"""Application entrypoint with dynamic API module loading."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
from json import dumps
from typing import Any, Callable

from by_qa.config import get_settings
from by_qa.core import logger

settings = get_settings()

_knowledge_base_service: Any | None = None
_knowledge_item_ingestion_service: Any | None = None
_knowledge_item_search_service: Any | None = None
_knowledge_fetch_cache_cleanup_service: Any | None = None
_document_chunking_service: Any | None = None


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
        required_packages=("fastapi", "minio", "psycopg"),
        register_kwargs_factory=lambda: {
            "get_knowledge_base_service": resolve_knowledge_base_service,
            "get_knowledge_item_ingestion_service": (
                resolve_knowledge_item_ingestion_service
            ),
            "get_knowledge_item_search_service": resolve_knowledge_item_search_service,
        },
    ),
    ApiModuleDefinition(
        name="knowledge_build",
        route_module="by_qa.knowledge_build.api.routes",
        register_function="register_routes",
        required_packages=(
            "fastapi",
            "langchain_text_splitters",
            "openpyxl",
            "fitz",
            "docx",
            "pptx",
        ),
        register_kwargs_factory=lambda: {
            "get_document_chunking_service": resolve_document_chunking_service,
        },
    ),
)


def get_adapter() -> None:
    """Compatibility placeholder for tests migrated from the source project."""
    return None


def get_instant_search_engine() -> None:
    """Compatibility placeholder for tests migrated from the source project."""
    return None


def get_knowledge_base_service():
    """Get or create the knowledge-base metadata service."""
    global _knowledge_base_service
    if _knowledge_base_service is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_knowledge_base_service,
        )

        _knowledge_base_service = build_knowledge_base_service(settings)
    return _knowledge_base_service


def get_knowledge_item_ingestion_service():
    """Get or create the knowledge-item ingestion service."""
    global _knowledge_item_ingestion_service
    if _knowledge_item_ingestion_service is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_knowledge_item_ingestion_service,
        )

        _knowledge_item_ingestion_service = build_knowledge_item_ingestion_service(
            settings
        )
    return _knowledge_item_ingestion_service


def get_knowledge_item_search_service():
    """Get or create the knowledge-item search service."""
    global _knowledge_item_search_service
    if _knowledge_item_search_service is None:
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_knowledge_item_search_service,
        )

        _knowledge_item_search_service = build_knowledge_item_search_service(settings)
    return _knowledge_item_search_service


def get_knowledge_fetch_cache_cleanup_service():
    """Get or create the fetched-file cache cleanup service."""
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


def resolve_knowledge_base_service():
    """Resolve the KB service dynamically so tests can monkeypatch the factory."""
    return get_knowledge_base_service()


def resolve_knowledge_item_ingestion_service():
    """Resolve the ingestion service dynamically so tests can monkeypatch the factory."""
    return get_knowledge_item_ingestion_service()


def resolve_knowledge_item_search_service():
    """Resolve the search service dynamically so tests can monkeypatch the factory."""
    return get_knowledge_item_search_service()


def resolve_document_chunking_service():
    """Resolve the document chunking service dynamically for optional modules."""
    return get_document_chunking_service()


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


def _initialize_knowledge_base_runtime(enabled_modules: list[str]) -> None:
    """Initialize optional runtime services for the knowledge-base module."""
    if "knowledge_base" not in enabled_modules:
        logger.info("knowledge_base lifecycle skipped: module_not_loaded")
        return

    if not settings.kb_opengauss_dsn or not settings.embedding_model_name:
        logger.info("knowledge_base lifecycle skipped: configuration_incomplete")
        return

    from by_qa.knowledge_base.infrastructure.database import build_connection_factory
    from by_qa.knowledge_base.infrastructure.runtime import build_bootstrap_service

    kb_connection = build_connection_factory(settings)()
    try:
        build_bootstrap_service(settings).apply(kb_connection)
        get_knowledge_base_service()
        get_knowledge_item_ingestion_service()
        get_knowledge_fetch_cache_cleanup_service().start()
        logger.info("knowledge_base lifecycle initialized successfully")
    finally:
        kb_connection.close()


def _shutdown_knowledge_base_runtime(enabled_modules: list[str]) -> None:
    """Stop optional runtime services when the application shuts down."""
    if (
        "knowledge_base" in enabled_modules
        and _knowledge_fetch_cache_cleanup_service is not None
    ):
        _knowledge_fetch_cache_cleanup_service.stop()
        logger.info("knowledge_base lifecycle stopped")


@asynccontextmanager
async def lifespan(application):
    """Application lifecycle hooks."""
    settings.ensure_directories()
    enabled_modules = getattr(application.state, "enabled_modules", [])
    logger.info(
        "application startup: enabled_modules=%s",
        ",".join(enabled_modules) if enabled_modules else "none",
    )
    _initialize_knowledge_base_runtime(enabled_modules)

    yield

    _shutdown_knowledge_base_runtime(enabled_modules)


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
            status_code=422,
            content={
                "code": 422,
                "message": "error",
                "data": None,
                "error": {
                    "type": "request_invalid",
                    "error_code": "REQUEST_VALIDATION_FAILED",
                    "error_message": "request validation failed",
                    "details": {"errors": errors},
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
            status_code=500,
            content={
                "code": 500,
                "message": "error",
                "data": None,
                "error": {
                    "type": "internal_error",
                    "error_code": "KB_INTERNAL_ERROR",
                    "error_message": str(exc) or "internal error",
                    "details": {},
                },
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
            "Install by-qa[knowledge], by-qa[knowledge-build], or by-qa[all]."
        ) from exc

    uvicorn.run(
        "by_qa.main:create_app",
        host=settings.host,
        port=settings.port,
        reload=True,
        factory=True,
    )


def main() -> None:
    """Run the async CLI entrypoint in a dedicated event loop."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
