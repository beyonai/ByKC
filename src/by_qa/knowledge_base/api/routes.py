"""Route registration for knowledge base APIs."""

import json
import mimetypes
from pathlib import PurePosixPath
from typing import Any, Optional
from urllib.parse import quote

from fastapi import Body, File, Form, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import (
    CreateDirectoryRequest,
    CreateKnowledgeBaseRequest,
    DeleteDirectoryRequest,
    DeleteKnowledgeBaseRequest,
    DeleteKnowledgeItemRequest,
    FileToMarkdownIndexRequest,
    KnowledgeItemDownloadRequest,
    KnowledgeItemGlobRequest,
    KnowledgeItemListDirRequest,
    KnowledgeItemUploadRequest,
    ReadFileRequest,
    SearchRequest,
    UpdateDirectoryRequest,
    UpdateKnowledgeBaseRequest,
)
from by_qa.knowledge_base.services.errors import (
    KnowledgeBaseConfigurationError,
    KnowledgeBaseValidationError,
)


def _documented_success_response(
    *,
    result_object: dict[str, Any] | None = None,
    status_code: int = 200,
) -> JSONResponse:
    """Return the documented success envelope."""
    if status_code != 200:
        logger.info(
            "knowledge_base success response normalized to HTTP 200: business_status_code=%s",
            status_code,
        )
    return JSONResponse(
        status_code=200,
        content={
            "resultCode": "0",
            "resultMsg": "success",
            "resultObject": result_object or {},
        },
    )


def _documented_error_response(
    *,
    result_msg: str,
    result_object: dict[str, Any] | None = None,
    status_code: int = 422,
) -> JSONResponse:
    """Return the documented error envelope with HTTP status normalized to 200."""
    logger.info(
        "knowledge_base error response normalized to HTTP 200: business_status_code=%s, result_msg=%s",
        status_code,
        result_msg,
    )
    return JSONResponse(
        status_code=200,
        content={
            "resultCode": "-1",
            "resultMsg": result_msg,
            "resultObject": result_object or {},
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


def register_routes(
    app,
    *,
    get_knowledge_base_service,
    get_knowledge_item_ingestion_service,
    get_knowledge_item_search_service,
    get_document_chunking_service,
):
    """Register knowledge base API routes on the FastAPI app."""

    @app.post("/api/v1/knowledgeBases/create")
    async def create_knowledge_base(body: dict[str, Any] = Body(...)):
        try:
            request = CreateKnowledgeBaseRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )

        logger.info(
            "create_knowledge_base request received: kb_name=%s, has_description=%s",
            request.kb_name,
            request.kb_description is not None,
        )
        try:
            service = get_knowledge_base_service()
            logger.info(
                "create_knowledge_base resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.create_knowledge_base(request)
            logger.info(
                "create_knowledge_base service call succeeded: kb_code=%s",
                result.kb_code,
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "create_knowledge_base configuration failed: kb_name=%s, error=%s",
                request.kb_name,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "create_knowledge_base validation failed: kb_name=%s, error=%s",
                request.kb_name,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=409,
            )
        except Exception as exc:
            logger.exception(
                "create_knowledge_base unexpected error: kb_name=%s, error=%s",
                request.kb_name,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )

        logger.info(
            "create_knowledge_base response ready: code=200, kb_code=%s",
            result.kb_code,
        )
        return _documented_success_response(
            result_object=result.model_dump(by_alias=True)
        )

    @app.post("/api/v1/knowledgeBases/delete")
    async def delete_knowledge_base(body: dict[str, Any] = Body(...)):
        try:
            request = DeleteKnowledgeBaseRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "delete_knowledge_base request received: kb_code=%s", request.kb_code
        )
        try:
            service = get_knowledge_base_service()
            service.delete_knowledge_base(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=404
                if str(exc).startswith("knowledge base not found:")
                else 422,
            )
        except Exception as exc:
            logger.exception(
                "delete_knowledge_base unexpected error: kb_code=%s, error=%s",
                request.kb_code,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )
        return _documented_success_response(result_object={})

    @app.post("/api/v1/knowledgeBases/update")
    async def update_knowledge_base(body: dict[str, Any] = Body(...)):
        try:
            request = UpdateKnowledgeBaseRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "update_knowledge_base request received: kb_code=%s, has_kb_name=%s, has_description=%s",
            request.kb_code,
            "kb_name" in request.model_fields_set,
            "kb_description" in request.model_fields_set,
        )
        try:
            service = get_knowledge_base_service()
            service.update_knowledge_base(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=409 if "already exists:" in str(exc) else 422,
            )
        except Exception as exc:
            logger.exception(
                "update_knowledge_base unexpected error: kb_code=%s, error=%s",
                request.kb_code,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )
        return _documented_success_response(result_object={})

    @app.post("/api/v1/directories/create")
    async def create_directory(body: dict[str, Any] = Body(...)):
        try:
            request = CreateDirectoryRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "create_directory request received: kb_code=%s, directory_path=%s, has_description=%s",
            request.kb_code,
            request.directory_path,
            request.directory_description is not None,
        )
        try:
            service = get_knowledge_base_service()
            service.create_directory(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            message = str(exc)
            return _documented_error_response(
                result_msg=message,
                result_object={},
                status_code=404
                if message.startswith("parent directory not found:")
                else 422,
            )
        except Exception as exc:
            logger.exception(
                "create_directory unexpected error: kb_code=%s, directory_path=%s, error=%s",
                request.kb_code,
                request.directory_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )
        return _documented_success_response(result_object={})

    @app.post("/api/v1/directories/delete")
    async def delete_directory(body: dict[str, Any] = Body(...)):
        try:
            request = DeleteDirectoryRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "delete_directory request received: kb_code=%s, directory_path=%s",
            request.kb_code,
            request.directory_path,
        )
        try:
            service = get_knowledge_base_service()
            service.delete_directory(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            message = str(exc)
            return _documented_error_response(
                result_msg=message,
                result_object={},
                status_code=422,
            )
        except Exception as exc:
            logger.exception(
                "delete_directory unexpected error: kb_code=%s, directory_path=%s, error=%s",
                request.kb_code,
                request.directory_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )
        return _documented_success_response(result_object={})

    @app.post("/api/v1/directories/update")
    async def update_directory(body: dict[str, Any] = Body(...)):
        try:
            request = UpdateDirectoryRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "update_directory request received: kb_code=%s, directory_path=%s, directory_name=%s",
            request.kb_code,
            request.directory_path,
            request.directory_name,
        )
        try:
            service = get_knowledge_base_service()
            service.update_directory(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            message = str(exc)
            return _documented_error_response(
                result_msg=message,
                result_object={},
                status_code=409
                if message.startswith("directory name already exists under parent:")
                else 422,
            )
        except Exception as exc:
            logger.exception(
                "update_directory unexpected error: kb_code=%s, directory_path=%s, error=%s",
                request.kb_code,
                request.directory_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )
        return _documented_success_response(result_object={})

    @app.post("/api/v1/knowledgeItems/import")
    @app.post("/api/v1/knowledge-items/import")
    async def upload_file(
        kn_code: str | None = Form(None, alias="knCode"),
        file_path: str | None = Form(None, alias="filePath"),
        file_description: str | None = Form(None, alias="fileDescription"),
        file_content: UploadFile | None = File(None, alias="fileContent"),
    ):
        try:
            payload = await file_content.read() if file_content is not None else None
            request = KnowledgeItemUploadRequest.model_validate(
                {
                    "knCode": kn_code,
                    "filePath": file_path,
                    "fileDescription": file_description,
                    "fileContent": payload,
                    "fileName": file_content.filename
                    if file_content is not None
                    else None,
                    "contentType": (
                        file_content.content_type if file_content is not None else None
                    ),
                }
            )
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "upload_file request received: kb_code=%s, file_path=%s, has_description=%s, file_name=%s",
            request.kb_code,
            request.file_path,
            request.file_description is not None,
            request.file_name,
        )
        try:
            service = get_knowledge_item_ingestion_service()
            service.upload_file(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=422,
            )
        except Exception as exc:
            logger.exception(
                "upload_file unexpected error: kb_code=%s, file_path=%s, error=%s",
                request.kb_code,
                request.file_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )
        return _documented_success_response(result_object={})

    @app.post("/api/v1/knowledgeItems/delete")
    @app.post("/api/v1/knowledge-items/delete")
    async def delete_knowledge_item(body: dict[str, Any] = Body(...)):
        try:
            request = DeleteKnowledgeItemRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "delete_knowledge_item request received: kb_code=%s, file_path=%s",
            request.kb_code,
            request.file_path,
        )
        try:
            service = get_knowledge_item_ingestion_service()
            service.delete_knowledge_item(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=422,
            )
        except Exception as exc:
            logger.exception(
                "delete_knowledge_item unexpected error: kb_code=%s, file_path=%s, error=%s",
                request.kb_code,
                request.file_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )
        return _documented_success_response(result_object={})

    @app.post("/api/v1/fileToMarkdownIndex")
    async def file_to_markdown_index(body: dict[str, Any] = Body(...)):
        logger.info(
            "file_to_markdown_index request received: body_keys=%s",
            list(body.keys()),
        )
        try:
            request = FileToMarkdownIndexRequest.model_validate(body)
        except ValidationError as exc:
            logger.warning("file_to_markdown_index validation failed: error=%s", exc)
            return _documented_error_response(
                result_msg="request validation failed",
            )

        try:
            service = get_knowledge_item_ingestion_service()
            chunking_service = get_document_chunking_service()
            service.file_to_markdown_index(
                request, document_chunking_service=chunking_service
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning("file_to_markdown_index configuration failed: error=%s", exc)
            return _documented_error_response(
                result_msg=str(exc),
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "file_to_markdown_index validation failed: kb_code=%s, file_path=%s, error=%s",
                request.kb_code,
                request.file_path,
                exc,
            )
            return _documented_error_response(result_msg=str(exc))

        logger.info(
            "file_to_markdown_index response ready: code=200, kb_code=%s, file_path=%s",
            request.kb_code,
            request.file_path,
        )
        return _documented_success_response()

    @app.post("/api/v1/knowledgeItems/search")
    @app.post("/api/v1/knowledge-items/search")
    async def search_knowledge_items(body: dict[str, Any] = Body(...)):
        try:
            request = SearchRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "search_knowledge_items request received: query=%s, kb_code_count=%s, top_k=%s, search_mode=%s",
            request.query,
            len(request.kb_codes),
            request.top_k,
            request.search_mode,
        )
        try:
            service = get_knowledge_item_search_service()
            items = service.search_v2(request)
            logger.info(
                "search_knowledge_items service call succeeded: returned_count=%s, top_k=%s",
                len(items),
                request.top_k,
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning("search_knowledge_items configuration failed: error=%s", exc)
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning("search_knowledge_items validation failed: error=%s", exc)
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=422,
            )

        logger.info(
            "search_knowledge_items response ready: code=200, returned_count=%s",
            len(items),
        )
        return _documented_success_response(
            result_object={"data": [item.model_dump(by_alias=True) for item in items]}
        )

    @app.post("/api/v1/listDir")
    async def list_dir(body: dict[str, Any] = Body(...)):
        try:
            request = KnowledgeItemListDirRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "list_dir request received: kb_code=%s, directory_path=%s",
            request.kb_code,
            request.directory_path,
        )
        try:
            service = get_knowledge_base_service()
            logger.info(
                "list_dir resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.list_dir(request)
            logger.info(
                "list_dir service call succeeded: directory_path=%s, item_count=%s",
                request.directory_path,
                len(result.items),
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "list_dir configuration failed: directory_path=%s, error=%s",
                request.directory_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "list_dir validation failed: directory_path=%s, error=%s",
                request.directory_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=422,
            )
        except Exception as exc:
            logger.exception(
                "list_dir unexpected error: kb_code=%s, directory_path=%s, error=%s",
                request.kb_code,
                request.directory_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )

        logger.info(
            "list_dir response ready: code=200, item_count=%s",
            len(result.items),
        )
        return _documented_success_response(
            result_object={
                "data": [item.model_dump(by_alias=True) for item in result.items]
            }
        )

    @app.post("/api/v1/glob")
    async def glob(body: dict[str, Any] = Body(...)):
        try:
            request = KnowledgeItemGlobRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "glob request received: kb_code=%s, path_rule=%s",
            request.kb_code,
            request.path_rule,
        )
        try:
            service = get_knowledge_base_service()
            logger.info(
                "glob resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.glob(request)
            logger.info(
                "glob service call succeeded: path_rule=%s, item_count=%s",
                request.path_rule,
                len(result.items),
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "glob configuration failed: path_rule=%s, error=%s",
                request.path_rule,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "glob validation failed: path_rule=%s, error=%s",
                request.path_rule,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=422,
            )
        except Exception as exc:
            logger.exception(
                "glob unexpected error: kb_code=%s, path_rule=%s, error=%s",
                request.kb_code,
                request.path_rule,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )

        logger.info(
            "glob response ready: code=200, item_count=%s",
            len(result.items),
        )
        return _documented_success_response(
            result_object={
                "data": [item.model_dump(by_alias=True) for item in result.items]
            }
        )

    @app.post("/api/v1/readFile")
    async def read_file(body: dict[str, Any] = Body(...)):
        try:
            request = ReadFileRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "read_file request received: kb_code=%s, file_path=%s, start_line=%s, end_line=%s",
            request.kb_code,
            request.file_path,
            request.start_line,
            request.end_line,
        )
        try:
            service = get_knowledge_base_service()
            result = service.read_file(request)
            logger.info(
                "read_file service call succeeded: file_path=%s, returned_bytes=%s",
                request.file_path,
                len((result.get("data") or "").encode("utf-8")),
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "read_file configuration failed: file_path=%s, error=%s",
                request.file_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "read_file validation failed: file_path=%s, error=%s",
                request.file_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=422,
            )

        result_object = {k: v for k, v in result.items() if v is not None}
        logger.info(
            "read_file response ready: code=200, file_path=%s",
            request.file_path,
        )
        return _documented_success_response(result_object=result_object)

    @app.post("/api/v1/downloadFile")
    async def download_file(body: dict[str, Any] = Body(...)):
        try:
            request = KnowledgeItemDownloadRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "download_file request received: kb_code=%s, file_path=%s",
            request.kb_code,
            request.file_path,
        )
        try:
            service = get_knowledge_base_service()
            logger.info(
                "download_file resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = service.download_file(request)
            logger.info(
                "download_file service call succeeded: file_path=%s, returned_bytes=%s",
                request.file_path,
                len(result["content"]),
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "download_file configuration failed: file_path=%s, error=%s",
                request.file_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "download_file validation failed: file_path=%s, error=%s",
                request.file_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=422,
            )
        except Exception as exc:
            logger.exception(
                "download_file unexpected error: kb_code=%s, file_path=%s, error=%s",
                request.kb_code,
                request.file_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )

        logger.info(
            "download_file response ready: code=200, file_path=%s, filename=%s, returned_bytes=%s",
            request.file_path,
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
