"""Route registration for knowledge build APIs."""

import base64
from typing import Any

from fastapi.responses import JSONResponse

from by_qa.core import logger
from by_qa.knowledge_build.api.schemas import (
    BuildMarkdownIndexRequest,
    BuildMarkdownIndexResponse,
    FileToMarkdownIndexRequest,
    FileToMarkdownIndexResponse,
    FileToMarkdownRequest,
    FileToMarkdownResponse,
)
from by_qa.knowledge_common.exceptions import KnowledgeConfigurationError

SUPPORTED_FILE_TYPES = {
    "pdf",
    "docx",
    "pptx",
    "xlsx",
    "txt",
    "md",
    "csv",
}


def _normalize_file_type(file_type: str) -> str:
    """Normalize request file types for case-insensitive validation."""
    normalized = file_type.strip().lower()
    if normalized == "markdown":
        return "md"
    return normalized


def _success_response(
    *,
    data: dict[str, Any],
    status_code: int = 200,
) -> JSONResponse:
    """Return the standardized success envelope."""
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": "success",
            "error": None,
            "data": data,
        },
    )


def _error_response(
    *,
    status_code: int,
    error_type: str,
    error_code: str,
    error_message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Return the standardized error envelope."""
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": "error",
            "data": None,
            "error": {
                "type": error_type,
                "error_code": error_code,
                "error_message": error_message,
                "details": details or {},
            },
        },
    )


def register_routes(
    app,
    *,
    get_document_chunking_service,
):
    """Register knowledge build API routes on the FastAPI app."""

    def _parse_file_to_markdown(b64_content: str, file_type: str) -> str | JSONResponse:
        """Decode base64 and parse a document into markdown."""
        normalized_file_type = _normalize_file_type(file_type)
        if normalized_file_type not in SUPPORTED_FILE_TYPES:
            return _error_response(
                status_code=422,
                error_type="business_validation",
                error_code="FILE_TYPE_UNSUPPORTED",
                error_message=(
                    f"unsupported file type: {file_type}. "
                    f"Supported types: {', '.join(sorted(SUPPORTED_FILE_TYPES))}"
                ),
                details={"type": file_type},
            )

        try:
            file_bytes = base64.b64decode(b64_content, validate=True)
        except Exception:
            return _error_response(
                status_code=422,
                error_type="business_validation",
                error_code="FILE_CONTENT_INVALID",
                error_message="content is not valid base64",
                details={"field": "content"},
            )

        try:
            chunking_service = get_document_chunking_service()
            if chunking_service is None:
                raise KnowledgeConfigurationError(
                    "document chunking service is not configured"
                )
            return chunking_service.extract_text_from_file(
                file_bytes, normalized_file_type
            )
        except KnowledgeConfigurationError as exc:
            logger.warning(
                "file_to_markdown configuration failed: type=%s, error=%s",
                file_type,
                exc,
            )
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"type": file_type},
            )
        except Exception as exc:
            logger.warning(
                "file_to_markdown parse failed: type=%s, error=%s", file_type, exc
            )
            return _error_response(
                status_code=422,
                error_type="business_validation",
                error_code="FILE_PARSE_FAILED",
                error_message=f"failed to parse file: {exc}",
                details={"type": file_type},
            )

    def _build_chunks(content: str) -> list | JSONResponse:
        """Chunk and embed markdown text."""
        content_bytes = content.encode("utf-8")
        if not content_bytes.strip():
            return _error_response(
                status_code=422,
                error_type="business_validation",
                error_code="CHUNK_EMPTY",
                error_message="markdown content is empty",
                details={},
            )

        try:
            chunking_service = get_document_chunking_service()
            if chunking_service is None:
                raise KnowledgeConfigurationError(
                    "document chunking service is not configured"
                )
            chunks = chunking_service.chunk_and_embed(
                content_bytes, filename="input.md"
            )
        except KnowledgeConfigurationError as exc:
            logger.warning("build_markdown_index configuration failed: error=%s", exc)
            error_message = str(exc)
            if "embedding" in error_message.lower():
                return _error_response(
                    status_code=503,
                    error_type="dependency_error",
                    error_code="EMBEDDING_SERVICE_ERROR",
                    error_message=error_message,
                    details={},
                )
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="RUNTIME_CONFIG_ERROR",
                error_message=error_message,
                details={},
            )
        except ValueError as exc:
            logger.warning("build_markdown_index validation failed: error=%s", exc)
            return _error_response(
                status_code=422,
                error_type="business_validation",
                error_code="CHUNK_EMPTY",
                error_message=str(exc),
                details={},
            )
        except Exception as exc:
            logger.exception("build_markdown_index unexpected error: error=%s", exc)
            return _error_response(
                status_code=500,
                error_type="internal_error",
                error_code="INTERNAL_ERROR",
                error_message=str(exc),
                details={},
            )

        if not chunks:
            return _error_response(
                status_code=422,
                error_type="business_validation",
                error_code="CHUNK_EMPTY",
                error_message="no chunks produced from markdown content",
                details={},
            )
        return chunks

    @app.post("/api/v1/file-to-markdown")
    async def file_to_markdown(request: FileToMarkdownRequest):
        logger.info(
            "file_to_markdown request received: type=%s, content_length=%s",
            request.type,
            len(request.content),
        )
        result = _parse_file_to_markdown(request.content, request.type)
        if isinstance(result, JSONResponse):
            return result

        logger.info(
            "file_to_markdown response ready: code=200, type=%s, md_content_length=%s",
            request.type,
            len(result),
        )
        return _success_response(
            data=FileToMarkdownResponse(md_content=result).model_dump()
        )

    @app.post("/api/v1/build-markdown-index")
    async def build_markdown_index(request: BuildMarkdownIndexRequest):
        logger.info(
            "build_markdown_index request received: content_length=%s",
            len(request.content),
        )
        result = _build_chunks(request.content)
        if isinstance(result, JSONResponse):
            return result

        logger.info(
            "build_markdown_index response ready: code=200, chunk_count=%s",
            len(result),
        )
        return _success_response(
            data=BuildMarkdownIndexResponse(chunks=result).model_dump()
        )

    @app.post("/api/v1/file-to-markdown-index")
    async def file_to_markdown_index(request: FileToMarkdownIndexRequest):
        logger.info(
            "file_to_markdown_index request received: type=%s, content_length=%s",
            request.type,
            len(request.content),
        )
        markdown_result = _parse_file_to_markdown(request.content, request.type)
        if isinstance(markdown_result, JSONResponse):
            return markdown_result

        chunks_result = _build_chunks(markdown_result)
        if isinstance(chunks_result, JSONResponse):
            return chunks_result

        logger.info(
            "file_to_markdown_index response ready: code=200, type=%s, md_content_length=%s, chunk_count=%s",
            request.type,
            len(markdown_result),
            len(chunks_result),
        )
        return _success_response(
            data=FileToMarkdownIndexResponse(
                md_content=markdown_result,
                chunks=chunks_result,
            ).model_dump()
        )
