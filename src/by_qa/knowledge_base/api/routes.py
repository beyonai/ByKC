"""Route registration for knowledge base APIs."""

import io
import json
import mimetypes
import zipfile
from inspect import isawaitable
from pathlib import PurePosixPath
from typing import Any, Callable, Optional
from urllib.parse import quote

from fastapi import BackgroundTasks, Body, File, Form, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from by_qa.core import logger
from by_qa.knowledge_base.api.knowledge_entity_schemas import (
    DeleteKnowledgeEntityAliasRequest,
    DeleteKnowledgeEntityRequest,
    EntityDiscoveryRequest,
    EntityEnrichRequest,
    KnowledgeEntityProcessingService,
    ProcessingBatchStatusRequest,
    ProcessingEligibilityRequest,
    ProcessingTaskStatusRequest,
    SemanticRelationsRequest,
)
from by_qa.knowledge_base.api.metadata_schemas import (
    GetFileMetadataRequest,
    MetadataSearchRequest,
    SearchFileRequest,
    UpdateFileMetadataRequest,
)
from by_qa.knowledge_base.api.schemas import (
    BuildResultRequest,
    CreateDirectoryRequest,
    CreateKnowledgeBaseRequest,
    DeleteDirectoryRequest,
    DeleteKnowledgeBaseRequest,
    DeleteKnowledgeItemRequest,
    DocumentUpdateRequest,
    FileBuildStatusRequest,
    FileToMarkdownIndexRequest,
    KnowledgeItemDownloadRequest,
    KnowledgeItemGlobRequest,
    KnowledgeItemListDirRequest,
    KnowledgeItemReferenceQueryRequest,
    KnowledgeItemUploadRequest,
    MoveKnowledgeItemsRequest,
    ReadFileRequest,
    SearchRequest,
    UpdateDirectoryRequest,
    UpdateKnowledgeBaseRequest,
)
from by_qa.knowledge_base.dsl.errors import DslValidationError
from by_qa.knowledge_base.events import (
    KnowledgeEvent,
    ResourceEventType,
    build_resource_event,
)
from by_qa.knowledge_base.services.errors import (
    KnowledgeBaseConfigurationError,
    KnowledgeBaseValidationError,
)
from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    convert_uploaded_file_to_markdown,
)
from by_qa.knowledge_base.services.zip_batch_import_service import ZipBatchImportService


def _documented_success_response(
    *,
    result_object: dict[str, Any] | None = None,
    result_msg: str = "success",
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
                "resultMsg": result_msg,
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


def _parse_optional_metadata_form(value: str | None) -> dict[str, Any] | None:
    """Parse an optional multipart JSON object used as entry metadata."""
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("metadata must be a valid JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError("metadata must be a JSON object")
    return parsed


async def _resolve_maybe_async(factory):
    """Resolve a dependency factory that may be synchronous or asynchronous."""
    result = factory()
    if isawaitable(result):
        return await result
    return result


def _serialize_knowledge_entity_result(
    result: BaseModel | dict[str, Any],
) -> dict[str, Any]:
    """Convert a KnowledgeEntity service result into the public camel-case shape."""
    if isinstance(result, BaseModel):
        return result.model_dump(by_alias=True, exclude_none=True)
    if isinstance(result, dict):
        return result
    raise TypeError("knowledge entity service must return a Pydantic model or dict")


def _knowledge_entity_error_object(exc: Exception) -> dict[str, Any]:
    """Preserve an optional structured service error code in the common envelope."""
    error_code = getattr(exc, "error_code", None)
    return {"errorCode": str(error_code)} if error_code else {}


async def _backfill_markdown_update_timeline_summary(
    *,
    markdown_update_summary_service,
    connection_factory,
    update_timeline_repository,
    timeline_id: int,
    old_markdown_context: str | None,
    new_markdown_context: str | None,
) -> None:
    """Replace a rule-based timeline summary when an LLM produces a safe one."""
    try:
        summary = await markdown_update_summary_service.generate_llm_summary(
            old_markdown_context or "", new_markdown_context or ""
        )
    except Exception:
        logger.exception(
            "document update timeline LLM summary failed: timeline_id=%s", timeline_id
        )
        return
    if not summary:
        logger.info(
            "document update timeline LLM summary unavailable; retaining fallback: timeline_id=%s",
            timeline_id,
        )
        return

    connection = None
    try:
        connection = await connection_factory()
        cursor = connection.cursor()
        await update_timeline_repository.update_summary_from_llm(
            cursor, timeline_id=timeline_id, summary=summary
        )
        await connection.commit()
    except Exception:
        if connection is not None:
            try:
                await connection.rollback()
            except Exception:
                logger.exception(
                    "document update timeline backfill rollback failed: timeline_id=%s",
                    timeline_id,
                )
        logger.exception(
            "document update timeline LLM summary backfill failed: timeline_id=%s",
            timeline_id,
        )
    finally:
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                logger.exception(
                    "document update timeline backfill connection close failed: timeline_id=%s",
                    timeline_id,
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
    get_document_update_service=None,
    get_document_chunking_service,
    get_metadata_search_service,
    get_file_metadata_query_service,
    get_file_metadata_update_service=None,
    get_knowledge_entity_processing_service=None,
    get_knowledge_event_publisher_invoker=None,
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

    async def _get_knowledge_entity_service() -> KnowledgeEntityProcessingService:
        if get_knowledge_entity_processing_service is None:
            raise KnowledgeBaseConfigurationError(
                "knowledge entity processing service is not configured"
            )
        service = await _resolve_maybe_async(get_knowledge_entity_processing_service)
        if service is None:
            raise KnowledgeBaseConfigurationError(
                "knowledge entity processing service is not configured"
            )
        return service

    async def _try_schedule_event(
        background_tasks: BackgroundTasks,
        event_factory: Callable[[], KnowledgeEvent],
    ) -> None:
        if get_knowledge_event_publisher_invoker is None:
            return
        try:
            event = event_factory()
            invoker = await _resolve_maybe_async(get_knowledge_event_publisher_invoker)
            background_tasks.add_task(invoker.publish, event)
        except Exception as exc:
            logger.warning(
                "knowledge event scheduling failed: error_type=%s",
                type(exc).__name__,
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
    async def create_directory(
        background_tasks: BackgroundTasks,
        body: dict[str, Any] = Body(...),
    ):
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
        await _try_schedule_event(
            background_tasks,
            lambda: build_resource_event(
                event_type=ResourceEventType.DIRECTORY_CREATED,
                kb_code=request.kb_code,
                source_path=None,
                target_path=_ensure_leading_slash(request.directory_path),
            ),
        )
        return _documented_success_response(result_object={})

    @app.post("/api/v1/directories/delete")
    async def delete_directory(
        background_tasks: BackgroundTasks,
        body: dict[str, Any] = Body(...),
    ):
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
        await _try_schedule_event(
            background_tasks,
            lambda: build_resource_event(
                event_type=ResourceEventType.DIRECTORY_DELETED,
                kb_code=request.kb_code,
                source_path=_ensure_leading_slash(request.directory_path),
                target_path=None,
            ),
        )
        return _documented_success_response(result_object={})

    @app.post("/api/v1/directories/update")
    async def update_directory(
        background_tasks: BackgroundTasks,
        body: dict[str, Any] = Body(...),
    ):
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
        source_path = _ensure_leading_slash(request.directory_path)
        parent_path = str(PurePosixPath(source_path).parent)
        target_path = (
            f"/{request.directory_name}"
            if parent_path == "/"
            else f"{parent_path}/{request.directory_name}"
        )
        await _try_schedule_event(
            background_tasks,
            lambda: build_resource_event(
                event_type=ResourceEventType.DIRECTORY_UPDATED,
                kb_code=request.kb_code,
                source_path=source_path,
                target_path=target_path,
            ),
        )
        return _documented_success_response(result_object={})

    @app.post("/api/v1/knowledgeItems/import")
    @app.post("/api/v1/knowledge-items/import")
    async def upload_file(
        background_tasks: BackgroundTasks,
        kn_code: str | None = Form(None, alias="knCode"),
        file_path: str | None = Form(None, alias="filePath"),
        file_description: str | None = Body(None, alias="fileDescription"),
        file_content: UploadFile | None = File(None, alias="fileContent"),
        process_front_matter: bool = Form(True, alias="processFrontMatter"),
        skip_if_duplicate: bool = Form(False, alias="skipIfDuplicate"),
        metadata: str | None = Form(None, alias="metadata"),
    ):
        try:
            payload = await file_content.read() if file_content is not None else None
            request = KnowledgeItemUploadRequest.model_validate(
                {
                    "knCode": kn_code,
                    "filePath": file_path,
                    "fileDescription": file_description,
                    "fileContent": payload,
                    "mimeType": (
                        file_content.content_type if file_content is not None else None
                    ),
                    "processFrontMatter": process_front_matter,
                    "skipIfDuplicate": skip_if_duplicate,
                    "metadata": _parse_optional_metadata_form(metadata),
                }
            )
        except (ValidationError, ValueError) as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object=(
                    {"errors": json.loads(exc.json())}
                    if isinstance(exc, ValidationError)
                    else {"errors": [{"msg": str(exc)}]}
                ),
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
                    skip_if_duplicate=request.skip_if_duplicate,
                    metadata=request.metadata,
                )
                result_object = {
                    "data": [item.model_dump(by_alias=True) for item in result.data],
                    "summary": result.summary.model_dump(by_alias=True),
                }
                if result.post_process_errors:
                    result_object["postProcessErrors"] = result.post_process_errors
                if result.summary.succeeded > 0:
                    await _try_schedule_event(
                        background_tasks,
                        lambda: build_resource_event(
                            event_type=ResourceEventType.FILE_IMPORTED,
                            kb_code=request.kb_code,
                            source_path=None,
                            target_path=_ensure_leading_slash(request.file_path),
                            items=[
                                {
                                    "sourcePath": item.file_path,
                                    "targetPath": (
                                        item.file_path if item.success else None
                                    ),
                                    "resourceType": "file",
                                    "success": item.success,
                                    "error": item.error,
                                }
                                for item in result.data
                            ],
                            result=result.summary.model_dump(),
                        ),
                    )
                return _documented_success_response(result_object=result_object)
            # single file
            file_path_norm = "/" + request.file_path.strip("/")
            segments = [s for s in file_path_norm.split("/") if s]
            if any(s == ".." for s in segments):
                return _documented_error_response(
                    result_msg="unsafe path", result_object={}, status_code=422
                )
            await service.upload_file(request)
            result_object = {
                "data": [
                    {
                        "filePath": file_path_norm,
                        "success": True,
                        "error": None,
                    }
                ],
                "summary": {"total": 1, "succeeded": 1, "failed": 0},
            }
            await _try_schedule_event(
                background_tasks,
                lambda: build_resource_event(
                    event_type=ResourceEventType.FILE_IMPORTED,
                    kb_code=request.kb_code,
                    source_path=None,
                    target_path=file_path_norm,
                    result=result_object["summary"],
                ),
            )
            return _documented_success_response(result_object=result_object)
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

    @app.post("/api/v1/knowledgeItems/update")
    @app.post("/api/v1/knowledge-items/update")
    async def update_document(
        request: Request,
        background_tasks: BackgroundTasks,
        kn_code: str | None = Form(None, alias="knCode"),
        file_path: str | None = Form(None, alias="filePath"),
        file_description: str | None = Form(None, alias="fileDescription"),
        file_content: UploadFile | None = File(None, alias="fileContent"),
        process_front_matter: bool = Form(True, alias="processFrontMatter"),
        skip_if_duplicate: bool = Form(False, alias="skipIfDuplicate"),
        refer_signature: str | None = Form(None, alias="referSignature"),
        metadata: str | None = Form(None, alias="metadata"),
    ):
        payload = await file_content.read() if file_content is not None else None
        request_data = {
            "knCode": kn_code,
            "filePath": file_path,
            "fileContent": payload,
            "processFrontMatter": process_front_matter,
            "skipIfDuplicate": skip_if_duplicate,
            "referSignature": refer_signature,
        }
        # FastAPI converts an empty optional Form value to its default (None).
        # The parsed multipart form is cached, so inspect only field presence to
        # retain the API's omitted-versus-explicit-empty contract.
        if "fileDescription" in await request.form():
            request_data["fileDescription"] = file_description or ""

        try:
            request_data["metadata"] = _parse_optional_metadata_form(metadata)
            document_request = DocumentUpdateRequest.model_validate(request_data)
        except (ValidationError, ValueError) as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object=(
                    {"errors": json.loads(exc.json())}
                    if isinstance(exc, ValidationError)
                    else {"errors": [{"msg": str(exc)}]}
                ),
                status_code=422,
            )
        filename = file_content.filename if file_content is not None else ""
        upload_suffix = PurePosixPath(filename or "").suffix.lower()
        target_suffix = PurePosixPath(document_request.file_path).suffix.lower()
        if upload_suffix == ".zip":
            return _documented_error_response(
                result_msg="zip uploads are not supported for document update",
                result_object={},
                status_code=422,
            )
        if upload_suffix != target_suffix:
            return _documented_error_response(
                result_msg="uploaded filename suffix must match filePath suffix",
                result_object={},
                status_code=422,
            )

        logger.info(
            "update_document request received: kb_code=%s, file_path=%s, has_description=%s, process_front_matter=%s",
            document_request.kb_code,
            document_request.file_path,
            "file_description" in document_request.model_fields_set,
            document_request.process_front_matter,
        )
        try:
            if get_document_update_service is None:
                raise KnowledgeBaseConfigurationError(
                    "document update service is not configured"
                )
            service = await _resolve_maybe_async(get_document_update_service)
            update_result = await service.update_file(document_request)
            if update_result is not None and update_result.is_markdown:
                background_tasks.add_task(
                    _backfill_markdown_update_timeline_summary,
                    markdown_update_summary_service=service.markdown_update_summary_service,
                    connection_factory=service.connection_factory,
                    update_timeline_repository=service.update_timeline_repository,
                    timeline_id=update_result.timeline_id,
                    old_markdown_context=update_result.old_markdown_context,
                    new_markdown_context=update_result.new_markdown_context,
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
                "update_document unexpected error: kb_code=%s, file_path=%s, error=%s",
                document_request.kb_code,
                document_request.file_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )

        result_object = {
            "data": [
                {
                    "knCode": document_request.kb_code,
                    "filePath": document_request.file_path,
                    "success": True,
                    "error": None,
                }
            ]
        }
        event_file_path = (
            update_result.file_path
            if update_result is not None and update_result.file_path
            else document_request.file_path
        )
        await _try_schedule_event(
            background_tasks,
            lambda: build_resource_event(
                event_type=ResourceEventType.FILE_UPDATED,
                kb_code=document_request.kb_code,
                source_path=event_file_path,
                target_path=event_file_path,
                result={"success": True},
            ),
        )
        return _documented_success_response(result_object=result_object)

    @app.post("/api/v1/knowledgeItems/delete")
    @app.post("/api/v1/knowledge-items/delete")
    async def delete_knowledge_item(
        background_tasks: BackgroundTasks,
        body: dict[str, Any] = Body(...),
    ):
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
        await _try_schedule_event(
            background_tasks,
            lambda: build_resource_event(
                event_type=ResourceEventType.FILE_DELETED,
                kb_code=request.kb_code,
                source_path=_ensure_leading_slash(request.file_path),
                target_path=None,
            ),
        )
        return _documented_success_response(result_object={})

    @app.post("/api/v1/knowledgeItems/move")
    @app.post("/api/v1/knowledge-items/move")
    async def move_knowledge_items(
        background_tasks: BackgroundTasks,
        body: dict[str, Any] = Body(...),
    ):
        try:
            request = MoveKnowledgeItemsRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "move_knowledge_items request received: kb_code=%s, source_count=%s",
            request.kb_code,
            len(request.source_path),
        )
        try:
            service = await get_knowledge_base_service()
            result = await service.move_knowledge_items(request)
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
                "move_knowledge_items unexpected error: kb_code=%s, error=%s",
                request.kb_code,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )

        result_object = (
            result.model_dump(by_alias=True)
            if hasattr(result, "model_dump")
            else result
        )
        if result.summary.succeeded > 0:
            successful_targets = [
                item.target_path for item in result.data if item.success
            ]
            await _try_schedule_event(
                background_tasks,
                lambda: build_resource_event(
                    event_type=ResourceEventType.RESOURCE_MOVED,
                    kb_code=request.kb_code,
                    source_path=(
                        request.source_path[0]
                        if len(request.source_path) == 1
                        else None
                    ),
                    target_path=(
                        successful_targets[0]
                        if len(successful_targets) == 1
                        else request.target_directory_path
                    ),
                    items=[item.model_dump(by_alias=True) for item in result.data],
                    result=result.summary.model_dump(),
                ),
            )
        return _documented_success_response(result_object=result_object)

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

    @app.post("/api/v1/buildResult")
    async def build_result(body: dict[str, Any] = Body(...)):
        try:
            request = BuildResultRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "build_result request received: kb_code=%s, file_path=%s, chunk_page=%s, chunk_page_size=%s, include_markdown=%s",
            request.kb_code,
            request.file_path,
            request.chunk_page,
            request.chunk_page_size,
            request.include_markdown,
        )
        try:
            service = await get_knowledge_base_service()
            result = await service.build_result(request)
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "build_result configuration failed: file_path=%s, error=%s",
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
                "build_result validation failed: file_path=%s, error=%s",
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
                "build_result unexpected error: kb_code=%s, file_path=%s, error=%s",
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
            "build_result response ready: code=200, file_path=%s, status=%s, chunk_count=%s",
            request.file_path,
            result.get("build", {}).get("status"),
            result.get("chunks", {}).get("total"),
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

    @app.post("/api/v1/knowledgeItems/references")
    @app.post("/api/v1/knowledge-items/references")
    async def list_inbound_references(body: dict[str, Any] = Body(...)):
        try:
            request = KnowledgeItemReferenceQueryRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "list_inbound_references request received: kb_code=%s, file_path=%s, direction=%s",
            request.kb_code,
            request.file_path,
            request.direction,
        )
        try:
            service = await get_knowledge_base_service()
            result = await service.list_inbound_references(request)
        except KnowledgeBaseConfigurationError as exc:
            logger.warning(
                "list_inbound_references configuration failed: file_path=%s, error=%s",
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
                "list_inbound_references validation failed: file_path=%s, error=%s",
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
                "list_inbound_references unexpected error: kb_code=%s, file_path=%s, error=%s",
                request.kb_code,
                request.file_path,
                exc,
            )
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object={},
                status_code=500,
            )

        return _documented_success_response(
            result_object={
                "inbound": [item.model_dump(by_alias=True) for item in result.inbound],
                "outbound": [
                    item.model_dump(by_alias=True) for item in result.outbound
                ],
            }
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
        result_object = {
            "data": [item.model_dump(by_alias=True) for item in result.data]
        }
        if result.page_size is not None:
            result_object.update(
                {
                    "total": result.total,
                    "pageNum": result.page_num,
                    "pageSize": result.page_size,
                }
            )
        return _documented_success_response(result_object=result_object)

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
        result_object = {
            "data": [item.model_dump(by_alias=True) for item in result.data]
        }
        if result.page_size is not None:
            result_object.update(
                {
                    "total": result.total,
                    "pageNum": result.page_num,
                    "pageSize": result.page_size,
                }
            )
        return _documented_success_response(result_object=result_object)

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

    @app.post("/api/v1/knowledgeItems/processingEligibility")
    async def processing_eligibility(body: dict[str, Any] = Body(...)):
        try:
            request = ProcessingEligibilityRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "processing_eligibility request received: kb_code=%s, file_path=%s, capability=%s",
            request.kb_code,
            request.file_path,
            request.capability,
        )
        try:
            service = await _get_knowledge_entity_service()
            result = await service.evaluate_processing_eligibility(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=422,
            )
        except Exception as exc:
            logger.exception("processing_eligibility error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object=_knowledge_entity_error_object(exc),
                status_code=500,
            )
        return _documented_success_response(
            result_object=_serialize_knowledge_entity_result(result)
        )

    @app.post("/api/v1/knowledgeItems/entityDiscovery")
    async def entity_discovery(body: dict[str, Any] = Body(...)):
        try:
            request = EntityDiscoveryRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "entity_discovery request received: kb_code=%s, file_path=%s, directory_path=%s, force=%s",
            request.kb_code,
            request.file_path,
            request.directory_path,
            request.force,
        )
        try:
            service = await _get_knowledge_entity_service()
            result = await service.discover_knowledge_entities(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=422,
            )
        except Exception as exc:
            logger.exception("entity_discovery error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object=_knowledge_entity_error_object(exc),
                status_code=500,
            )
        return _documented_success_response(
            result_object=_serialize_knowledge_entity_result(result),
            result_msg="accepted",
        )

    @app.post("/api/v1/knowledgeEntities/delete")
    @app.post("/api/v1/knowledge-entities/delete")
    async def delete_knowledge_entity(body: dict[str, Any] = Body(...)):
        try:
            request = DeleteKnowledgeEntityRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        try:
            service = await _get_knowledge_entity_service()
            result = await service.delete_knowledge_entity(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=422,
            )
        except Exception as exc:
            logger.exception("delete_knowledge_entity error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object=_knowledge_entity_error_object(exc),
                status_code=500,
            )
        return _documented_success_response(
            result_object=_serialize_knowledge_entity_result(result)
        )

    @app.post("/api/v1/knowledgeEntities/aliases/delete")
    @app.post("/api/v1/knowledge-entities/aliases/delete")
    async def delete_knowledge_entity_alias(body: dict[str, Any] = Body(...)):
        try:
            request = DeleteKnowledgeEntityAliasRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        try:
            service = await _get_knowledge_entity_service()
            result = await service.delete_knowledge_entity_alias(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=422,
            )
        except Exception as exc:
            logger.exception("delete_knowledge_entity_alias error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object=_knowledge_entity_error_object(exc),
                status_code=500,
            )
        return _documented_success_response(
            result_object=_serialize_knowledge_entity_result(result)
        )

    @app.post("/api/v1/knowledgeItems/entityEnrich")
    async def entity_enrich(body: dict[str, Any] = Body(...)):
        try:
            request = EntityEnrichRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "entity_enrich request received: kb_code=%s, file_path=%s, force=%s",
            request.kb_code,
            request.file_path,
            request.force,
        )
        try:
            service = await _get_knowledge_entity_service()
            result = await service.enrich_knowledge_entities(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=422,
            )
        except Exception as exc:
            logger.exception("entity_enrich error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object=_knowledge_entity_error_object(exc),
                status_code=500,
            )
        return _documented_success_response(
            result_object=_serialize_knowledge_entity_result(result),
            result_msg="accepted",
        )

    @app.post("/api/v1/knowledgeItems/processingTaskStatus")
    async def processing_task_status(body: dict[str, Any] = Body(...)):
        try:
            request = ProcessingTaskStatusRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "processing_task_status request received: kb_code=%s, file_path=%s, batch_id=%s",
            request.kb_code,
            request.file_path,
            request.batch_id,
        )
        try:
            service = await _get_knowledge_entity_service()
            result = await service.get_processing_task_status(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=422,
            )
        except Exception as exc:
            logger.exception("processing_task_status error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object=_knowledge_entity_error_object(exc),
                status_code=500,
            )
        return _documented_success_response(
            result_object=_serialize_knowledge_entity_result(result)
        )

    @app.post("/api/v1/knowledgeItems/processingBatchStatus")
    async def processing_batch_status(body: dict[str, Any] = Body(...)):
        try:
            request = ProcessingBatchStatusRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "processing_batch_status request received: kb_code=%s, batch_id=%s",
            request.kb_code,
            request.batch_id,
        )
        try:
            service = await _get_knowledge_entity_service()
            result = await service.get_processing_batch_status(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=422,
            )
        except Exception as exc:
            logger.exception("processing_batch_status error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object=_knowledge_entity_error_object(exc),
                status_code=500,
            )
        return _documented_success_response(
            result_object=_serialize_knowledge_entity_result(result)
        )

    @app.post("/api/v1/knowledgeItems/semanticRelations")
    async def semantic_relations(body: dict[str, Any] = Body(...)):
        try:
            request = SemanticRelationsRequest.model_validate(body)
        except ValidationError as exc:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={"errors": json.loads(exc.json())},
                status_code=422,
            )
        logger.info(
            "semantic_relations request received: kb_code=%s, file_path=%s, direction=%s",
            request.kb_code,
            request.file_path,
            request.direction,
        )
        try:
            service = await _get_knowledge_entity_service()
            result = await service.get_semantic_relations(request)
        except KnowledgeBaseConfigurationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=503,
            )
        except KnowledgeBaseValidationError as exc:
            return _documented_error_response(
                result_msg=str(exc),
                result_object=_knowledge_entity_error_object(exc),
                status_code=422,
            )
        except Exception as exc:
            logger.exception("semantic_relations error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error",
                result_object=_knowledge_entity_error_object(exc),
                status_code=500,
            )
        return _documented_success_response(
            result_object=_serialize_knowledge_entity_result(result)
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
            "metadata_search request received: kb_code_count=%s, page_num=%s, page_size=%s, where=%s",
            len(request.kb_code_list) if request.kb_code_list else 0,
            request.page_num,
            request.effective_page_size,
            json.dumps(request.where, ensure_ascii=False),
        )
        try:
            service = await get_metadata_search_service()
            page = await service.search(request)
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
            result_object={
                "data": [r.model_dump(by_alias=True) for r in page.data],
                "total": page.total,
                "pageNum": page.page_num,
                "pageSize": page.page_size,
            }
        )

    @app.post("/api/v1/knowledgeItems/metadata/update")
    async def update_file_metadata(body: dict[str, Any] = Body(...)):
        try:
            request = UpdateFileMetadataRequest.model_validate(body)
        except ValidationError:
            return _documented_error_response(
                result_msg="request validation failed",
                result_object={},
                status_code=422,
            )
        logger.info(
            "update_file_metadata request received: kb_code=%s, file_path=%s, operation_count=%s",
            request.kb_code,
            request.file_path,
            len(request.operation_list),
        )
        try:
            if get_file_metadata_update_service is None:
                raise KnowledgeBaseConfigurationError(
                    "file metadata update service is not configured"
                )
            service = await _resolve_maybe_async(get_file_metadata_update_service)
            await service.update_metadata(request)
        except (KnowledgeBaseConfigurationError, KnowledgeBaseValidationError) as exc:
            return _documented_error_response(result_msg=str(exc), result_object={})
        except Exception as exc:
            logger.exception("update_file_metadata error: %s", exc)
            return _documented_error_response(
                result_msg=str(exc) or "internal error", result_object={}
            )
        return _documented_success_response(result_object={})

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
