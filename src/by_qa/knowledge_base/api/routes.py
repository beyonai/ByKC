"""Route registration for knowledge base APIs."""

import mimetypes
from pathlib import PurePosixPath
from typing import Any, Optional
from urllib.parse import quote

from fastapi import Response
from fastapi.responses import JSONResponse

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import (
    CreateDirectoryRequest,
    CreateKnowledgeBaseRequest,
    DeleteDirectoryRequest,
    DeleteKnowledgeBaseRequest,
    DeleteKnowledgeItemRequest,
    KnowledgeItemDownloadRequest,
    KnowledgeItemFetchRequest,
    KnowledgeItemGlobRequest,
    KnowledgeItemImportRequest,
    KnowledgeItemListDirRequest,
    KnowledgeItemSearchRequest,
    UpdateDirectoryRequest,
    UpdateFileRequest,
    UpdateKnowledgeBaseRequest,
    WriteFileRequest,
    WriteIndexRequest,
)
from by_qa.knowledge_base.services.errors import (
    KnowledgeBaseConfigurationError,
    KnowledgeBaseValidationError,
)


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


def _ensure_leading_slash(path: str) -> str:
    """Normalize outward-facing paths to the canonical slash-prefixed form."""
    normalized = str(path or "").strip()
    if not normalized:
        return "/"
    return normalized if normalized.startswith("/") else f"/{normalized}"


def _build_content_disposition(filename: str) -> str:
    """Build a Content-Disposition header that is safe for non-ASCII filenames."""
    normalized = PurePosixPath(filename or "download").name or "download"
    safe_ascii = normalized.encode("ascii", "ignore").decode("ascii")
    if not safe_ascii or safe_ascii.startswith("."):
        suffix = PurePosixPath(normalized).suffix
        safe_ascii = f"download{suffix}" if suffix else "download"
    safe_ascii = safe_ascii.replace('"', "")
    if safe_ascii == normalized:
        return f'attachment; filename="{safe_ascii}"'
    encoded = quote(normalized, safe="")
    return f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded}"


def _map_create_knowledge_base_validation_error(
    *,
    exc: KnowledgeBaseValidationError,
    kb_code: str,
) -> JSONResponse:
    """Map create-knowledge-base validation errors to the new protocol."""
    message = str(exc)
    if message.startswith("kb_code already exists:"):
        return _error_response(
            status_code=409,
            error_type="conflict",
            error_code="KB_CODE_CONFLICT",
            error_message=message,
            details={"kb_code": kb_code},
        )
    if message.startswith("kb_code is occupied by a soft-deleted knowledge base:"):
        return _error_response(
            status_code=409,
            error_type="conflict",
            error_code="KB_CODE_SOFT_DELETED_CONFLICT",
            error_message=message,
            details={"kb_code": kb_code},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_REQUEST_INVALID",
        error_message=message,
        details={"kb_code": kb_code},
    )


def _map_delete_knowledge_base_validation_error(
    *,
    exc: KnowledgeBaseValidationError,
    kb_code: str,
) -> JSONResponse:
    """Map delete-knowledge-base validation errors to the standardized protocol."""
    message = str(exc)
    if message.startswith("knowledge base not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_NOT_FOUND",
            error_message=message,
            details={"kb_code": kb_code},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_DELETE_KB_INVALID",
        error_message=message,
        details={"kb_code": kb_code},
    )


def _map_update_knowledge_base_validation_error(
    *,
    exc: KnowledgeBaseValidationError,
    kb_code: str,
) -> JSONResponse:
    """Map update-knowledge-base validation errors to the standardized protocol."""
    message = str(exc)
    if message.startswith("knowledge base not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_NOT_FOUND",
            error_message=message,
            details={"kb_code": kb_code},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_UPDATE_INVALID",
        error_message=message,
        details={"kb_code": kb_code},
    )


def _map_create_directory_validation_error(
    *,
    exc: KnowledgeBaseValidationError,
    kb_code: str,
    directory_code: str,
    directory_path: str,
) -> JSONResponse:
    """Map create-directory validation errors to the standardized protocol."""
    message = str(exc)
    if message.startswith("knowledge base not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_NOT_FOUND",
            error_message=message,
            details={"kb_code": kb_code},
        )
    if message.startswith("parent directory not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_DIRECTORY_PARENT_NOT_FOUND",
            error_message=message,
            details={
                "kb_code": kb_code,
                "directory_code": directory_code,
                "directory_path": directory_path,
            },
        )
    if message.startswith("directory_code already exists:") or message.startswith(
        "directory_code is occupied by a soft-deleted knowledge item:"
    ):
        return _error_response(
            status_code=409,
            error_type="conflict",
            error_code="KB_DIRECTORY_CODE_CONFLICT",
            error_message=message,
            details={"kb_code": kb_code, "directory_code": directory_code},
        )
    if message.startswith("directory path already exists:"):
        return _error_response(
            status_code=409,
            error_type="conflict",
            error_code="KB_DIRECTORY_PATH_CONFLICT",
            error_message=message,
            details={
                "kb_code": kb_code,
                "directory_code": directory_code,
                "directory_path": directory_path,
            },
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_DIRECTORY_CREATE_INVALID",
        error_message=message,
        details={
            "kb_code": kb_code,
            "directory_code": directory_code,
            "directory_path": directory_path,
        },
    )


def _map_delete_directory_validation_error(
    *,
    exc: KnowledgeBaseValidationError,
    kb_code: str,
    directory_code: str,
) -> JSONResponse:
    """Map delete-directory validation errors to the standardized protocol."""
    message = str(exc)
    if message.startswith("knowledge base not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_NOT_FOUND",
            error_message=message,
            details={"kb_code": kb_code},
        )
    if message.startswith("directory not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_DIRECTORY_NOT_FOUND",
            error_message=message,
            details={"kb_code": kb_code, "directory_code": directory_code},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_DIRECTORY_DELETE_INVALID",
        error_message=message,
        details={"kb_code": kb_code, "directory_code": directory_code},
    )


def _map_update_directory_validation_error(
    *,
    exc: KnowledgeBaseValidationError,
    kb_code: str,
    directory_code: str,
) -> JSONResponse:
    """Map update-directory validation errors to the standardized protocol."""
    message = str(exc)
    if message.startswith("knowledge base not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_NOT_FOUND",
            error_message=message,
            details={"kb_code": kb_code},
        )
    if message.startswith("directory not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_DIRECTORY_NOT_FOUND",
            error_message=message,
            details={"kb_code": kb_code, "directory_code": directory_code},
        )
    if message.startswith("directory name already exists under parent:"):
        return _error_response(
            status_code=409,
            error_type="conflict",
            error_code="KB_DIRECTORY_NAME_CONFLICT",
            error_message=message,
            details={"kb_code": kb_code, "directory_code": directory_code},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_DIRECTORY_UPDATE_INVALID",
        error_message=message,
        details={"kb_code": kb_code, "directory_code": directory_code},
    )


def _map_write_file_validation_error(
    *,
    exc: KnowledgeBaseValidationError,
    file_code: str,
    version: str,
    file_path: str,
) -> JSONResponse:
    """Map write-file validation errors to the new protocol."""
    message = str(exc)
    if message.startswith("item_code/version already exists:"):
        return _error_response(
            status_code=409,
            error_type="conflict",
            error_code="KB_FILE_VERSION_CONFLICT",
            error_message=message,
            details={"file_code": file_code, "version": version},
        )
    if message.startswith("file_code is occupied by a soft-deleted knowledge item:"):
        return _error_response(
            status_code=409,
            error_type="conflict",
            error_code="KB_FILE_CODE_SOFT_DELETED_CONFLICT",
            error_message=message,
            details={"file_code": file_code, "version": version},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_WRITE_FILE_INVALID",
        error_message=message,
        details={"file_code": file_code, "version": version, "file_path": file_path},
    )


def _map_update_file_validation_error(
    *,
    exc: KnowledgeBaseValidationError,
    kb_code: str,
    file_code: str,
) -> JSONResponse:
    """Map update-file validation errors to the standardized protocol."""
    message = str(exc)
    if message.startswith("knowledge base not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_NOT_FOUND",
            error_message=message,
            details={"kb_code": kb_code},
        )
    if message.startswith("knowledge item not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_FILE_NOT_FOUND",
            error_message=message,
            details={"kb_code": kb_code, "file_code": file_code},
        )
    if message.startswith("file name already exists under parent:"):
        return _error_response(
            status_code=409,
            error_type="conflict",
            error_code="KB_FILE_NAME_CONFLICT",
            error_message=message,
            details={"kb_code": kb_code, "file_code": file_code},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_FILE_UPDATE_INVALID",
        error_message=message,
        details={"kb_code": kb_code, "file_code": file_code},
    )


def _map_delete_knowledge_item_validation_error(
    *,
    exc: KnowledgeBaseValidationError,
    kb_code: str,
    file_code: str,
) -> JSONResponse:
    """Map delete-knowledge-item validation errors to the standardized protocol."""
    message = str(exc)
    if message.startswith("knowledge base not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_NOT_FOUND",
            error_message=message,
            details={"kb_code": kb_code},
        )
    if message.startswith("knowledge item not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_FILE_NOT_FOUND",
            error_message=message,
            details={"kb_code": kb_code, "file_code": file_code},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_DELETE_FILE_INVALID",
        error_message=message,
        details={"kb_code": kb_code, "file_code": file_code},
    )


def _map_write_index_validation_error(
    *,
    exc: KnowledgeBaseValidationError,
    file_code: str,
    version: str,
) -> JSONResponse:
    """Map write-index validation errors to the new protocol."""
    message = str(exc)
    if message.startswith("knowledge item not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_FILE_NOT_FOUND",
            error_message=message,
            details={"file_code": file_code},
        )
    if message.startswith("knowledge item version not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_FILE_VERSION_NOT_FOUND",
            error_message=message,
            details={"file_code": file_code, "version": version},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_WRITE_INDEX_INVALID",
        error_message=message,
        details={"file_code": file_code, "version": version},
    )


def _map_import_validation_error(
    *,
    exc: KnowledgeBaseValidationError,
    file_code: str,
    version: str,
    file_path: str,
) -> JSONResponse:
    """Map combined-import validation errors to the standardized protocol."""
    message = str(exc)
    if message.startswith("item_code/version already exists:"):
        return _error_response(
            status_code=409,
            error_type="conflict",
            error_code="KB_FILE_VERSION_CONFLICT",
            error_message=message,
            details={"file_code": file_code, "version": version},
        )
    if message.startswith("file_code is occupied by a soft-deleted knowledge item:"):
        return _error_response(
            status_code=409,
            error_type="conflict",
            error_code="KB_FILE_CODE_SOFT_DELETED_CONFLICT",
            error_message=message,
            details={
                "file_code": file_code,
                "version": version,
                "file_path": file_path,
            },
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_IMPORT_INVALID",
        error_message=message,
        details={"file_code": file_code, "version": version, "file_path": file_path},
    )


def _map_search_validation_error(*, exc: KnowledgeBaseValidationError) -> JSONResponse:
    """Map search validation/runtime errors to the standardized protocol."""
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_SEARCH_INVALID",
        error_message=str(exc),
        details={},
    )


def _map_list_dir_validation_error(
    *, exc: KnowledgeBaseValidationError, path: str
) -> JSONResponse:
    """Map list-dir validation errors to the standardized protocol."""
    message = str(exc)
    if message.startswith("directory not found:"):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_DIRECTORY_NOT_FOUND",
            error_message=message,
            details={"path": path},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_LIST_DIR_INVALID",
        error_message=message,
        details={"path": path},
    )


def _map_glob_validation_error(
    *, exc: KnowledgeBaseValidationError, path: str
) -> JSONResponse:
    """Map glob validation errors to the standardized protocol."""
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_GLOB_INVALID",
        error_message=str(exc),
        details={"path": path},
    )


def _map_read_file_validation_error(
    *, exc: KnowledgeBaseValidationError, path: str, kb_codes: list[str]
) -> JSONResponse:
    """Map read-file validation errors to the standardized protocol."""
    message = str(exc)
    if message.startswith("file not found:") or message.startswith(
        "current version not found:"
    ):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_FILE_NOT_FOUND",
            error_message=message,
            details={"path": path, "kb_codes": kb_codes},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_READ_FILE_INVALID",
        error_message=message,
        details={"path": path, "kb_codes": kb_codes},
    )


def _map_download_file_validation_error(
    *, exc: KnowledgeBaseValidationError, path: str, kb_codes: list[str]
) -> JSONResponse:
    """Map download-file validation errors to the standardized protocol."""
    message = str(exc)
    if message.startswith("file not found:") or message.startswith(
        "current version not found:"
    ):
        return _error_response(
            status_code=404,
            error_type="not_found",
            error_code="KB_FILE_NOT_FOUND",
            error_message=message,
            details={"path": path, "kb_codes": kb_codes},
        )
    return _error_response(
        status_code=422,
        error_type="business_validation",
        error_code="KB_DOWNLOAD_FILE_INVALID",
        error_message=message,
        details={"path": path, "kb_codes": kb_codes},
    )


def register_routes(
    app,
    *,
    get_knowledge_base_service,
    get_knowledge_item_ingestion_service,
    get_knowledge_item_search_service,
):
    """Register knowledge base API routes on the FastAPI app."""

    @app.post("/api/v1/knowledge-bases/create")
    async def create_knowledge_base(request: CreateKnowledgeBaseRequest):
        logger.info(
            "create_knowledge_base request received: kb_code=%s, kb_name=%s, status=%s, has_metadata=%s",
            request.kb_code,
            request.kb_name,
            request.status,
            request.metadata is not None,
        )
        try:
            service = get_knowledge_base_service()
            logger.info(
                "create_knowledge_base resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.create_knowledge_base(request)
            logger.info(
                "create_knowledge_base service call succeeded: kb_code=%s, status=%s",
                result.kb_code,
                result.status,
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "create_knowledge_base configuration failed: kb_code=%s, error=%s",
                request.kb_code,
                exc,
            )
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"kb_code": request.kb_code},
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "create_knowledge_base validation failed: kb_code=%s, error=%s",
                request.kb_code,
                exc,
            )
            return _map_create_knowledge_base_validation_error(
                exc=exc,
                kb_code=request.kb_code,
            )

        logger.info(
            "create_knowledge_base response ready: code=200, kb_code=%s, status=%s",
            result.kb_code,
            result.status,
        )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/knowledge-bases/delete")
    async def delete_knowledge_base(request: DeleteKnowledgeBaseRequest):
        logger.info(
            "delete_knowledge_base request received: kb_code=%s", request.kb_code
        )
        try:
            service = get_knowledge_base_service()
            result = service.delete_knowledge_base(request)
        except KnowledgeBaseConfigurationError as exc:
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"kb_code": request.kb_code},
            )
        except KnowledgeBaseValidationError as exc:
            return _map_delete_knowledge_base_validation_error(
                exc=exc, kb_code=request.kb_code
            )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/knowledge-bases/update")
    async def update_knowledge_base(request: UpdateKnowledgeBaseRequest):
        logger.info(
            "update_knowledge_base request received: kb_code=%s, has_kb_name=%s, has_description=%s, has_metadata=%s",
            request.kb_code,
            "kb_name" in request.model_fields_set,
            "kb_description" in request.model_fields_set,
            "metadata" in request.model_fields_set,
        )
        try:
            service = get_knowledge_base_service()
            result = service.update_knowledge_base(request)
        except KnowledgeBaseConfigurationError as exc:
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"kb_code": request.kb_code},
            )
        except KnowledgeBaseValidationError as exc:
            return _map_update_knowledge_base_validation_error(
                exc=exc,
                kb_code=request.kb_code,
            )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/directories/create")
    async def create_directory(request: CreateDirectoryRequest):
        logger.info(
            "create_directory request received: kb_code=%s, directory_code=%s, directory_path=%s, status=%s, has_metadata=%s",
            request.kb_code,
            request.directory_code,
            request.directory_path,
            request.status,
            request.metadata is not None,
        )
        try:
            service = get_knowledge_base_service()
            result = service.create_directory(request)
        except KnowledgeBaseConfigurationError as exc:
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={
                    "kb_code": request.kb_code,
                    "directory_code": request.directory_code,
                    "directory_path": request.directory_path,
                },
            )
        except KnowledgeBaseValidationError as exc:
            return _map_create_directory_validation_error(
                exc=exc,
                kb_code=request.kb_code,
                directory_code=request.directory_code,
                directory_path=request.directory_path,
            )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/directories/delete")
    async def delete_directory(request: DeleteDirectoryRequest):
        logger.info(
            "delete_directory request received: kb_code=%s, directory_code=%s",
            request.kb_code,
            request.directory_code,
        )
        try:
            service = get_knowledge_base_service()
            result = service.delete_directory(request)
        except KnowledgeBaseConfigurationError as exc:
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={
                    "kb_code": request.kb_code,
                    "directory_code": request.directory_code,
                },
            )
        except KnowledgeBaseValidationError as exc:
            return _map_delete_directory_validation_error(
                exc=exc,
                kb_code=request.kb_code,
                directory_code=request.directory_code,
            )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/directories/update")
    async def update_directory(request: UpdateDirectoryRequest):
        logger.info(
            "update_directory request received: kb_code=%s, directory_code=%s, has_name=%s, has_description=%s, has_metadata=%s",
            request.kb_code,
            request.directory_code,
            "directory_name" in request.model_fields_set,
            "directory_description" in request.model_fields_set,
            "metadata" in request.model_fields_set,
        )
        try:
            service = get_knowledge_base_service()
            result = service.update_directory(request)
        except KnowledgeBaseConfigurationError as exc:
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={
                    "kb_code": request.kb_code,
                    "directory_code": request.directory_code,
                },
            )
        except KnowledgeBaseValidationError as exc:
            return _map_update_directory_validation_error(
                exc=exc,
                kb_code=request.kb_code,
                directory_code=request.directory_code,
            )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/knowledge-items/update")
    async def update_file(request: UpdateFileRequest):
        logger.info(
            "update_file request received: kb_code=%s, file_code=%s, has_name=%s, has_description=%s, has_metadata=%s",
            request.kb_code,
            request.file_code,
            "file_name" in request.model_fields_set,
            "file_description" in request.model_fields_set,
            "metadata" in request.model_fields_set,
        )
        try:
            service = get_knowledge_base_service()
            result = service.update_file(request)
        except KnowledgeBaseConfigurationError as exc:
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"kb_code": request.kb_code, "file_code": request.file_code},
            )
        except KnowledgeBaseValidationError as exc:
            return _map_update_file_validation_error(
                exc=exc,
                kb_code=request.kb_code,
                file_code=request.file_code,
            )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/knowledge-items/import")
    async def import_knowledge_item(request: KnowledgeItemImportRequest):
        logger.info(
            "import_knowledge_item request received: kb_code=%s, file_code=%s, file_path=%s, version=%s, chunk_count=%s",
            request.kb_code,
            request.file_code,
            request.file_path,
            request.version,
            len(request.chunks),
        )
        try:
            service = get_knowledge_item_ingestion_service()
            logger.info(
                "import_knowledge_item resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.import_knowledge_item(request)
            logger.info(
                "import_knowledge_item service call succeeded: kb_code=%s, file_code=%s, version=%s, chunk_count=%s",
                result.kb_code,
                result.file_code,
                result.version,
                result.chunks.count,
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "import_knowledge_item configuration failed: kb_code=%s, file_code=%s, error=%s",
                request.kb_code,
                request.file_code,
                exc,
            )
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"kb_code": request.kb_code, "file_code": request.file_code},
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "import_knowledge_item validation failed: kb_code=%s, file_code=%s, error=%s",
                request.kb_code,
                request.file_code,
                exc,
            )
            return _map_import_validation_error(
                exc=exc,
                file_code=request.file_code,
                version=request.version,
                file_path=request.file_path,
            )

        logger.info(
            "import_knowledge_item response ready: code=200, kb_code=%s, file_code=%s, version=%s, chunk_count=%s",
            result.kb_code,
            result.file_code,
            result.version,
            result.chunks.count,
        )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/knowledge-items/delete")
    async def delete_knowledge_item(request: DeleteKnowledgeItemRequest):
        logger.info(
            "delete_knowledge_item request received: kb_code=%s, file_code=%s",
            request.kb_code,
            request.file_code,
        )
        try:
            service = get_knowledge_item_ingestion_service()
            result = service.delete_knowledge_item(request)
        except KnowledgeBaseConfigurationError as exc:
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"kb_code": request.kb_code, "file_code": request.file_code},
            )
        except KnowledgeBaseValidationError as exc:
            return _map_delete_knowledge_item_validation_error(
                exc=exc,
                kb_code=request.kb_code,
                file_code=request.file_code,
            )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/write-file")
    async def write_file(request: WriteFileRequest):
        logger.info(
            "write_file request received: kb_code=%s, file_code=%s, file_path=%s, version=%s",
            request.kb_code,
            request.file_code,
            request.file_path,
            request.version,
        )
        try:
            service = get_knowledge_item_ingestion_service()
            logger.info(
                "write_file resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.write_file(request)
            logger.info(
                "write_file service call succeeded: kb_code=%s, file_code=%s, file_path=%s, version=%s",
                result.kb_code,
                result.file_code,
                result.file_path,
                result.version,
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "write_file configuration failed: kb_code=%s, file_code=%s, error=%s",
                request.kb_code,
                request.file_code,
                exc,
            )
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"kb_code": request.kb_code, "file_code": request.file_code},
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "write_file validation failed: kb_code=%s, file_code=%s, error=%s",
                request.kb_code,
                request.file_code,
                exc,
            )
            return _map_write_file_validation_error(
                exc=exc,
                file_code=request.file_code,
                version=request.version,
                file_path=request.file_path,
            )

        logger.info(
            "write_file response ready: code=200, kb_code=%s, file_code=%s, version=%s",
            result.kb_code,
            result.file_code,
            result.version,
        )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/write-index")
    async def write_index(request: WriteIndexRequest):
        logger.info(
            "write_index request received: kb_code=%s, file_code=%s, version=%s, chunk_count=%s",
            request.kb_code,
            request.file_code,
            request.version,
            len(request.chunks),
        )
        try:
            service = get_knowledge_item_ingestion_service()
            logger.info(
                "write_index resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.write_index(request)
            logger.info(
                "write_index service call succeeded: kb_code=%s, file_code=%s, version=%s, chunk_count=%s",
                result.kb_code,
                result.file_code,
                result.version,
                result.chunks.count,
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "write_index configuration failed: kb_code=%s, file_code=%s, error=%s",
                request.kb_code,
                request.file_code,
                exc,
            )
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"kb_code": request.kb_code, "file_code": request.file_code},
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "write_index validation failed: kb_code=%s, file_code=%s, error=%s",
                request.kb_code,
                request.file_code,
                exc,
            )
            return _map_write_index_validation_error(
                exc=exc,
                file_code=request.file_code,
                version=request.version,
            )

        logger.info(
            "write_index response ready: code=200, kb_code=%s, file_code=%s, version=%s, chunk_count=%s",
            result.kb_code,
            result.file_code,
            result.version,
            result.chunks.count,
        )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/knowledge-items/search")
    async def search_knowledge_items(request: KnowledgeItemSearchRequest):
        logger.info(
            "search_knowledge_items request received: query=%s, kb_code_count=%s, top_k=%s, vector_top_k=%s, text_top_k=%s, source_code_count=%s, type_code_count=%s",
            request.query,
            len(request.kb_codes),
            request.top_k,
            request.vector_top_k,
            request.text_top_k,
            len(request.source_codes or []),
            len(request.type_codes or []),
        )
        try:
            service = get_knowledge_item_search_service()
            logger.info(
                "search_knowledge_items resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.search(request)
            logger.info(
                "search_knowledge_items service call succeeded: returned_count=%s, top_k=%s",
                result.meta.returned_count,
                result.meta.top_k,
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning("search_knowledge_items configuration failed: error=%s", exc)
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={},
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning("search_knowledge_items validation failed: error=%s", exc)
            return _map_search_validation_error(exc=exc)

        logger.info(
            "search_knowledge_items response ready: code=200, returned_count=%s",
            result.meta.returned_count,
        )
        return _success_response(data=result.model_dump())

    @app.post("/api/v1/list_dir")
    async def list_dir(request: KnowledgeItemListDirRequest):
        logger.info("list_dir request received: path=%s", request.path)
        try:
            service = get_knowledge_base_service()
            logger.info(
                "list_dir resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.list_dir(request)
            logger.info(
                "list_dir service call succeeded: path=%s, item_count=%s",
                request.path,
                len(result.items),
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "list_dir configuration failed: path=%s, error=%s", request.path, exc
            )
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"path": request.path},
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "list_dir validation failed: path=%s, error=%s", request.path, exc
            )
            return _map_list_dir_validation_error(exc=exc, path=request.path)

        logger.info(
            "list_dir response ready: code=200, item_count=%s",
            len(result.items),
        )
        return {"code": 200, "message": "success", "data": result.model_dump()["items"]}

    @app.post("/api/v1/glob")
    async def glob(request: KnowledgeItemGlobRequest):
        logger.info("glob request received: path=%s", request.path)
        try:
            service = get_knowledge_base_service()
            logger.info(
                "glob resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.glob(request)
            logger.info(
                "glob service call succeeded: path=%s, item_count=%s",
                request.path,
                len(result.items),
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "glob configuration failed: path=%s, error=%s", request.path, exc
            )
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"path": request.path},
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "glob validation failed: path=%s, error=%s", request.path, exc
            )
            return _map_glob_validation_error(exc=exc, path=request.path)

        logger.info(
            "glob response ready: code=200, item_count=%s",
            len(result.items),
        )
        return {"code": 200, "message": "success", "data": result.model_dump()["items"]}

    @app.post("/api/v1/read-file")
    async def read_file(request: KnowledgeItemFetchRequest):
        logger.info(
            "read_file request received: path=%s, kb_code_count=%s, content_type=%s, start_line=%s, end_line=%s",
            request.path,
            len(request.kb_codes),
            request.content_type,
            request.start_line,
            request.end_line,
        )
        try:
            service = get_knowledge_base_service()
            logger.info(
                "read_file resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.fetch(request)
            if result.content_type == "original":
                logger.info(
                    "read_file service call succeeded: path=%s, mode=original_url",
                    request.path,
                )
            else:
                logger.info(
                    "read_file service call succeeded: path=%s, returned_bytes=%s",
                    request.path,
                    len((result.data or "").encode("utf-8")),
                )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "read_file configuration failed: path=%s, error=%s",
                request.path,
                exc,
            )
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"path": request.path, "kb_codes": request.kb_codes},
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "read_file validation failed: path=%s, error=%s",
                request.path,
                exc,
            )
            return _map_read_file_validation_error(
                exc=exc,
                path=request.path,
                kb_codes=request.kb_codes,
            )

        if result.content_type == "original":
            logger.info(
                "read_file response ready: code=200, path=%s, mode=original_url",
                request.path,
            )
        else:
            logger.info(
                "read_file response ready: code=200, path=%s, returned_bytes=%s",
                request.path,
                len((result.data or "").encode("utf-8")),
            )
        payload = result.model_dump(exclude_none=True)
        payload["path"] = _ensure_leading_slash(str(payload.get("path", "")))
        return _success_response(data=payload)

    @app.post("/api/v1/download-file")
    async def download_file(request: KnowledgeItemDownloadRequest):
        logger.info(
            "download_file request received: path=%s, kb_code_count=%s",
            request.path,
            len(request.kb_codes),
        )
        try:
            service = get_knowledge_base_service()
            logger.info(
                "download_file resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.download_file(request)
            logger.info(
                "download_file service call succeeded: path=%s, returned_bytes=%s",
                request.path,
                len(result["content"]),
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "download_file configuration failed: path=%s, error=%s",
                request.path,
                exc,
            )
            return _error_response(
                status_code=503,
                error_type="configuration_error",
                error_code="KB_RUNTIME_CONFIG_ERROR",
                error_message=str(exc),
                details={"path": request.path, "kb_codes": request.kb_codes},
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "download_file validation failed: path=%s, error=%s",
                request.path,
                exc,
            )
            return _map_download_file_validation_error(
                exc=exc,
                path=request.path,
                kb_codes=request.kb_codes,
            )

        logger.info(
            "download_file response ready: code=200, path=%s, filename=%s, returned_bytes=%s",
            request.path,
            result["filename"],
            len(result["content"]),
        )
        quoted_filename = PurePosixPath(result["filename"]).name.replace('"', "")
        media_type = result["media_type"] or mimetypes.guess_type(quoted_filename)[0]
        return Response(
            content=result["content"],
            media_type=media_type or "application/octet-stream",
            headers={
                "Content-Disposition": _build_content_disposition(quoted_filename)
            },
        )


def _require_form_value(form, key: str) -> str:
    value = form.get(key)
    if value is None or str(value) == "":
        raise ValueError(f"{key} is required")
    return str(value)


def _optional_form_value(form, key: str) -> Optional[str]:
    value = form.get(key)
    if value in (None, ""):
        return None
    return str(value)
