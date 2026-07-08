"""Route registration for knowledge base APIs."""

import io
import json
import mimetypes
import zipfile
from inspect import isawaitable
from pathlib import PurePosixPath
from typing import Any, Optional
from urllib.parse import quote

from fastapi import BackgroundTasks, Body, File, Form, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from by_qa.core import logger
from by_qa.knowledge_base.api.metadata_schemas import (
    GetFileMetadataRequest,
    MetadataSearchRequest,
    SearchFileRequest,
)
from by_qa.knowledge_base.api.schemas import (
    CreateDirectoryRequest,
    CreateKnowledgeBaseRequest,
    DeleteDirectoryRequest,
    DeleteKnowledgeBaseRequest,
    DeleteKnowledgeItemRequest,
    FileBuildStatusRequest,
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
from by_qa.knowledge_base.dsl.errors import DslValidationError
from by_qa.knowledge_base.services.errors import (
    KnowledgeBaseConfigurationError,
    KnowledgeBaseValidationError,
)
from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    convert_uploaded_file_to_markdown,
)
from by_qa.knowledge_base.services.markdown_reference_rewriter import (
    MarkdownReferenceRewriter,
)
from by_qa.knowledge_base.services.zip_batch_import_service import ZipBatchImportService


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
        content=jsonable_encoder(
            {
                "resultCode": "0",
                "resultMsg": "success",
                "resultObject": result_object or {},
            }
        ),
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
        content=jsonable_encoder(
            {
                "resultCode": "-1",
                "resultMsg": result_msg,
                "resultObject": result_object or {},
            }
        ),
    )


async def _resolve_maybe_async(factory):
    """Resolve a dependency factory that may be synchronous or asynchronous."""
    result = factory()
    if isawaitable(result):
        return await result
    return result


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
    get_metadata_search_service,
    get_file_metadata_query_service,
):
    """Register knowledge base API routes on the FastAPI app."""

    async def _run_file_to_markdown_index_task(service, request, *, build_task_id: int):
        """Resolve heavy dependencies inside the background task itself."""
        chunking_service = await _resolve_maybe_async(get_document_chunking_service)
        await service.execute_file_to_markdown_index_task(
            request,
            document_chunking_service=chunking_service,
            build_task_id=build_task_id,
        )

    @app.post("/api/v1/fileToMarkdown")
    async def file_to_markdown(
        file_content: UploadFile | None = File(None, alias="fileContent"),
    ):
        if file_content is None:
            return _documented_error_response(
                result_msg="request validation failed",
                status_code=422,
            )
        filename = file_content.filename or ""
        logger.info(
            "file_to_markdown request received: filename=%s",
            filename,
        )
        try:
            chunking_service = await _resolve_maybe_async(get_document_chunking_service)
            file_bytes = await file_content.read()
            result = await convert_uploaded_file_to_markdown(
                file_bytes=file_bytes,
                filename=filename,
                document_chunking_service=chunking_service,
            )
        except KnowledgeBaseConfigurationError as exc:
            logger.warning("file_to_markdown configuration failed: error=%s", exc)
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            logger.warning(
                "file_to_markdown validation failed: filename=%s, error=%s",
                filename,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc),
                result_object={},
                status_code=422,
            )
        except Exception as exc:
            logger.exception(
                "file_to_markdown unexpected error: filename=%s, error=%s",
                filename,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )

        quoted_filename = PurePosixPath(result["filename"]).name.replace('"', "")
        logger.info(
            "file_to_markdown response ready: code=200, filename=%s, returned_bytes=%s",
            quoted_filename,
            len(result["content"]),
        )
        return Response(
            content=result["content"],
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": _build_content_disposition(quoted_filename)
            },
        )

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
            service = await get_knowledge_base_service()
            logger.info(
                "create_knowledge_base resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = await service.create_knowledge_base(request)
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
            service = await get_knowledge_base_service()
            await service.delete_knowledge_base(request)
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
            service = await get_knowledge_base_service()
            await service.update_knowledge_base(request)
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
            service = await get_knowledge_base_service()
            await service.create_directory(request)
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
            service = await get_knowledge_base_service()
            await service.delete_directory(request)
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
            service = await get_knowledge_base_service()
            await service.update_directory(request)
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
        process_front_matter: bool = Form(True, alias="processFrontMatter"),
    ):
        try:
            payload = await file_content.read() if file_content is not None else None
            request = KnowledgeItemUploadRequest.model_validate(
                {
                    "knCode": kn_code,
                    "filePath": file_path,
                    "fileDescription": file_description,
                    "fileContent": payload,
                    "processFrontMatter": process_front_matter,
                }
            )
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "upload_file request received: kb_code=%s, file_path=%s, has_description=%s, process_front_matter=%s",
            request.kb_code,
            request.file_path,
            request.file_description is not None,
            request.process_front_matter,
        )
        filename = (file_content.filename or "") if file_content is not None else ""
        try:
            service = await get_knowledge_item_ingestion_service()
            if filename.lower().endswith(".zip"):
                if not zipfile.is_zipfile(io.BytesIO(payload or b"")):
                    return _documented_error_response(
                        result_msg="invalid zip file",
                        result_object={},
                        status_code=422,
                    )
                batch_service = ZipBatchImportService(ingestion_service=service)
                result = await batch_service.import_zip(
                    kb_code=request.kb_code,
                    target_dir=request.file_path,
                    zip_bytes=payload,
                    process_front_matter=request.process_front_matter,
                    file_description=request.file_description,
                )
                return _documented_success_response(
                    result_object={
                        "data": [
                            item.model_dump(by_alias=True) for item in result.data
                        ],
                        "summary": result.summary.model_dump(by_alias=True),
                    }
                )
            # single file
            file_path_norm = "/" + request.file_path.strip("/")
            segments = [s for s in file_path_norm.split("/") if s]
            if any(s == ".." for s in segments):
                return _documented_error_response(
                    result_msg="unsafe path", result_object={}, status_code=422
                )
            content = request.file_content
            if file_path_norm.lower().endswith((".md", ".markdown")):
                rewriter = MarkdownReferenceRewriter(exists_check=service.files_exist)
                current_dir = "/".join(file_path_norm.split("/")[:-1]) or "/"
                rewritten = await rewriter.rewrite(
                    content.decode("utf-8"), current_dir, request.kb_code
                )
                content = rewritten.encode("utf-8")
            single_request = request.model_copy(update={"file_content": content})
            await service.upload_file(single_request)
            return _documented_success_response(
                result_object={
                    "data": [
                        {
                            "filePath": file_path_norm,
                            "success": True,
                            "error": None,
                        }
                    ],
                    "summary": {"total": 1, "succeeded": 1, "failed": 0},
                }
            )
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
            service = await get_knowledge_item_ingestion_service()
            await service.delete_knowledge_item(request)
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
    async def file_to_markdown_index(
        background_tasks: BackgroundTasks, body: dict[str, Any] = Body(...)
    ):
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
            service = await get_knowledge_item_ingestion_service()
            build_task_id = await service.create_file_to_markdown_index_task(request)
            background_tasks.add_task(
                _run_file_to_markdown_index_task,
                service,
                request,
                build_task_id=build_task_id,
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

    @app.post("/api/v1/fileBuildStatus")
    async def file_build_status(body: dict[str, Any] = Body(...)):
        try:
            request = FileBuildStatusRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "file_build_status request received: kb_code=%s, file_path=%s",
            request.kb_code,
            request.file_path,
        )
        try:
            service = await get_knowledge_base_service()
            result = await service.file_build_status(request)
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "file_build_status configuration failed: file_path=%s, error=%s",
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
                "file_build_status validation failed: file_path=%s, error=%s",
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
                "file_build_status unexpected error: kb_code=%s, file_path=%s, error=%s",
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
            "file_build_status response ready: code=200, file_path=%s, status=%s",
            request.file_path,
            result.get("status"),
        )
        return _documented_success_response(result_object=result)

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
            "search_knowledge_items request received: query=%s, kb_code_count=%s, top_k=%s, search_mode=%s, where=%s",
            request.query,
            len(request.kb_code_list),
            request.top_k,
            request.search_mode,
            json.dumps(request.where, ensure_ascii=False) if request.where else None,
        )
        try:
            service = await get_knowledge_item_search_service()
            items = await service.search(request)
            logger.info(
                "search_knowledge_items service call succeeded: returned_count=%s, top_k=%s",
                len(items),
                request.top_k,
            )
        except DslValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=exc.to_result_object(),
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
            service = await get_knowledge_base_service()
            logger.info(
                "list_dir resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = await service.list_dir(request)
            logger.info(
                "list_dir service call succeeded: directory_path=%s, item_count=%s",
                request.directory_path,
                len(result.data),
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
            len(result.data),
        )
        return _documented_success_response(
            result_object={
                "data": [item.model_dump(by_alias=True) for item in result.data]
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
            service = await get_knowledge_base_service()
            logger.info(
                "glob resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = await service.glob(request)
            logger.info(
                "glob service call succeeded: path_rule=%s, item_count=%s",
                request.path_rule,
                len(result.data),
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
            len(result.data),
        )
        return _documented_success_response(
            result_object={
                "data": [item.model_dump(by_alias=True) for item in result.data]
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
            service = await get_knowledge_base_service()
            result = await service.read_file(request)
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
            service = await get_knowledge_base_service()
            logger.info(
                "download_file resolved service: service_class=%s",
                service.__class__.__name__,
            )
            result = await service.download_file(request)
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

    @app.post("/api/v1/knowledgeItems/metadataSearch")
    async def metadata_search(body: dict[str, Any] = Body(...)):
        try:
            request = MetadataSearchRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "metadata_search request received: kb_code_count=%s, top_k=%s, where=%s",
            len(request.kb_code_list) if request.kb_code_list else 0,
            request.top_k,
            json.dumps(request.where, ensure_ascii=False),
        )
        try:
            service = await get_metadata_search_service()
            results = await service.search(request)
        except DslValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=exc.to_result_object(),
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(result_msg=str(exc), result_object={})
        except Exception as exc:
            logger.exception("metadata_search error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error", result_object={}
            )
        return _documented_success_response(
            result_object={"data": [r.model_dump(by_alias=True) for r in results]}
        )

    @app.post("/api/v1/knowledgeItems/metadata/get")
    async def get_file_metadata(body: dict[str, Any] = Body(...)):
        try:
            request = GetFileMetadataRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "get_file_metadata request received: kb_code=%s, file_path=%s, field_count=%s",
            request.kb_code,
            request.file_path,
            len(request.metadata_field_list) if request.metadata_field_list else 0,
        )
        try:
            service = await get_file_metadata_query_service()
            metadata = await service.get_metadata(request)
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(result_msg=str(exc), result_object={})
        except Exception as exc:
            logger.exception("get_file_metadata error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error", result_object={}
            )
        return _documented_success_response(result_object={"metadata": metadata})

    @app.post("/api/v1/knowledgeItems/searchFile")
    async def search_file(body: dict[str, Any] = Body(...)):
        try:
            request = SearchFileRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        try:
            service = await get_knowledge_item_search_service()
            results = await service.search_file_with_dsl(request)
        except DslValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=exc.to_result_object(),
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(result_msg=str(exc), result_object={})
        except Exception as exc:
            logger.exception("search_file error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error", result_object={}
            )
        return _documented_success_response(
            result_object={"data": [r.model_dump(by_alias=True) for r in results]}
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
