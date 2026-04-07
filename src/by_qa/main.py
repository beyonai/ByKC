"""FastAPI entrypoint for the open-source knowledge-base module."""

from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from by_qa.config import get_settings
from by_qa.knowledge_base.api.routes import register_routes

settings = get_settings()

_knowledge_base_service: Any | None = None
_knowledge_item_ingestion_service: Any | None = None
_knowledge_item_search_service: Any | None = None
_knowledge_fetch_cache_cleanup_service: Any | None = None


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


def resolve_knowledge_base_service():
    """Resolve the KB service dynamically so tests can monkeypatch the factory."""
    return get_knowledge_base_service()


def resolve_knowledge_item_ingestion_service():
    """Resolve the ingestion service dynamically so tests can monkeypatch the factory."""
    return get_knowledge_item_ingestion_service()


def resolve_knowledge_item_search_service():
    """Resolve the search service dynamically so tests can monkeypatch the factory."""
    return get_knowledge_item_search_service()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Application lifecycle hooks."""
    settings.ensure_directories()

    if settings.kb_opengauss_dsn and settings.embedding_model_name:
        from by_qa.knowledge_base.infrastructure.database import (
            build_connection_factory,
        )
        from by_qa.knowledge_base.infrastructure.runtime import build_bootstrap_service

        kb_connection = build_connection_factory(settings)()
        try:
            build_bootstrap_service(settings).apply(kb_connection)
            get_knowledge_base_service()
            get_knowledge_item_ingestion_service()
            get_knowledge_fetch_cache_cleanup_service().start()
        finally:
            kb_connection.close()

    yield

    if _knowledge_fetch_cache_cleanup_service is not None:
        _knowledge_fetch_cache_cleanup_service.stop()


app = FastAPI(
    title="by_qa Knowledge Base Service",
    description="Open-source knowledge-base ingestion and retrieval service.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Basic health probe."""
    return {"status": "ok"}


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return a standardized validation envelope for KB APIs."""
    if not request.url.path.startswith("/api/v1/"):
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
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
                "details": {"errors": exc.errors()},
            },
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    """Return a standardized 500 envelope for unexpected KB API failures."""
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


register_routes(
    app,
    get_knowledge_base_service=resolve_knowledge_base_service,
    get_knowledge_item_ingestion_service=resolve_knowledge_item_ingestion_service,
    get_knowledge_item_search_service=resolve_knowledge_item_search_service,
)


def main() -> None:
    """Run the local development server."""
    uvicorn.run("by_qa.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    main()
