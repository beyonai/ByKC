"""Transactional service for document, chunk, and embedding ingestion."""

import asyncio
import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable

import yaml

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import (
    DeleteKnowledgeItemRequest,
    FileToMarkdownIndexRequest,
    KnowledgeItemUploadRequest,
)
from by_qa.knowledge_base.build_status import (
    BUILD_STATUS_COMPLETE,
    BUILD_STATUS_FAILED,
    BUILD_STATUS_RUNNING,
    BUILD_STATUS_UNSUPPORTED,
    BUILD_STEP_CHUNKING,
    BUILD_STEP_COMPLETE,
    BUILD_STEP_MARKDOWN,
    BUILD_STEP_VECTORIZING,
)
from by_qa.knowledge_base.infrastructure.storage import StorageLocation
from by_qa.knowledge_base.metadata_types import (
    infer_metadata_value_type,
    normalize_metadata_value,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_build.services.document_chunking_service import (
    SUPPORTED_EXTENSIONS,
)
from by_qa.knowledge_common.exceptions import UnsupportedFileTypeError


def _guess_mime_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "text/markdown"
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _parse_front_matter(content: bytes) -> dict[str, Any]:
    """Extract YAML front matter from Markdown content.

    Returns an empty dict if no valid front matter is found.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return {}
    if not text.startswith("---"):
        return {}
    end_idx = text.find("---", 3)
    if end_idx == -1:
        return {}
    yaml_block = text[3:end_idx].strip()
    if not yaml_block:
        return {}
    try:
        parsed = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _build_optional_location(
    row: dict, *, namespace_key: str, key_key: str
) -> StorageLocation | None:
    namespace = row.get(namespace_key)
    key = row.get(key_key)
    if not namespace or not key:
        return None
    return StorageLocation(namespace=str(namespace), key=str(key))


def _supported_file_to_markdown_types_text() -> str:
    return ", ".join(sorted(ext.removeprefix(".") for ext in SUPPORTED_EXTENSIONS))


async def convert_uploaded_file_to_markdown(
    *,
    file_bytes: bytes,
    filename: str,
    document_chunking_service: Any,
) -> dict[str, Any]:
    """Convert an uploaded file directly to a Markdown download payload."""
    original_name = PurePosixPath(filename or "").name
    if not original_name:
        raise KnowledgeBaseValidationError("file name is required")
    if not file_bytes:
        raise KnowledgeBaseValidationError("fileContent must not be empty")

    suffix = PurePosixPath(original_name).suffix.lower()
    if not suffix:
        raise KnowledgeBaseValidationError("file type is required")
    file_type = suffix[1:]
    if suffix not in SUPPORTED_EXTENSIONS:
        raise KnowledgeBaseValidationError(
            f"unsupported file type: {file_type}. Supported types: "
            f"{_supported_file_to_markdown_types_text()}"
        )

    logger.info(
        "knowledge_item_ingestion_service.convert_uploaded_file_to_markdown started: filename=%s, file_type=%s, file_size=%s",
        original_name,
        file_type,
        len(file_bytes),
    )
    markdown_content = await asyncio.to_thread(
        document_chunking_service.extract_text_from_file,
        file_bytes,
        file_type,
    )
    markdown_filename = f"{PurePosixPath(original_name).stem}.md"
    markdown_bytes = markdown_content.encode("utf-8")
    logger.info(
        "knowledge_item_ingestion_service.convert_uploaded_file_to_markdown finished: filename=%s, markdown_filename=%s, markdown_size=%s",
        original_name,
        markdown_filename,
        len(markdown_bytes),
    )
    return {
        "filename": markdown_filename,
        "content": markdown_bytes,
    }


@dataclass
class KnowledgeItemIngestionService:
    """Import markdown documents, chunks, and embeddings transactionally."""

    connection_factory: Callable[[], Any]
    knowledge_base_repository: Any
    knowledge_fs_entry_repository: Any
    knowledge_item_chunk_repository: Any
    retrieval_projection_repository: Any
    storage_provider: Any
    embedding_dimension: int
    knowledge_build_task_repository: Any | None = None
    knowledge_fetch_cache_repository: Any | None = None
    file_metadata_value_repository: Any | None = None

    async def convert_uploaded_file_to_markdown(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        document_chunking_service: Any,
    ) -> dict[str, Any]:
        """Convert an uploaded file directly to a Markdown download payload."""
        return await convert_uploaded_file_to_markdown(
            file_bytes=file_bytes,
            filename=filename,
            document_chunking_service=document_chunking_service,
        )

    async def upload_file(self, request: KnowledgeItemUploadRequest) -> None:
        """Upload one original file and register its storage metadata on the file entry."""
        logger.info(
            "knowledge_item_ingestion_service.upload_file started: kb_code=%s, file_path=%s, file_size=%s",
            request.kb_code,
            request.file_path,
            len(request.file_content),
        )
        normalized_file_path = request.file_path.strip()
        if not normalized_file_path:
            raise KnowledgeBaseValidationError("file_path must not be empty")
        normalized_object_path = normalized_file_path.strip("/")
        if not normalized_object_path:
            raise KnowledgeBaseValidationError("file_path must not be root")

        mime_type = _guess_mime_type(normalized_object_path)
        checksum = hashlib.sha256(request.file_content).hexdigest()

        connection = await self.connection_factory()
        stored: Any | None = None
        original_location: Any | None = None
        try:
            cursor = connection.cursor()
            kb_row = await self.knowledge_base_repository.get_by_code(
                cursor, request.kb_code
            )
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)

            try:
                file_entry_row = (
                    await self.knowledge_fs_entry_repository.create_file_entry(
                        cursor,
                        knowledge_base_id=knowledge_base_id,
                        full_path=normalized_object_path,
                        file_description=request.file_description,
                    )
                )
            except ValueError as exc:
                raise KnowledgeBaseValidationError(str(exc)) from exc

            fs_entry_id = self._row_id(file_entry_row)

            original_location = self.storage_provider.build_original_location(
                kb_code=request.kb_code,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
                file_path=normalized_object_path,
                mime_type=mime_type,
            )

            stored = await self.storage_provider.write(
                original_location,
                request.file_content,
                content_type=mime_type,
            )

            await self.knowledge_fs_entry_repository.update_file_entry_storage(
                cursor,
                fs_entry_id=fs_entry_id,
                file_description=request.file_description,
                original_location=stored.location,
                file_size=len(request.file_content),
                mime_type=mime_type,
                checksum=checksum,
            )

            if request.process_front_matter:
                await self._apply_front_matter_metadata(
                    cursor,
                    fs_entry_id=fs_entry_id,
                    knowledge_base_id=knowledge_base_id,
                    content=request.file_content,
                    file_path=normalized_file_path,
                )

            await connection.commit()
        except Exception:
            await connection.rollback()
            if original_location is not None:
                await self.storage_provider.delete_quietly(original_location)
            raise
        finally:
            await connection.close()

    async def file_exists(self, kb_code: str, full_path: str) -> bool:
        """Return True if an uploaded file exists at `full_path` in the KB."""
        normalized = (full_path or "").strip("/")
        if not normalized:
            return False
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = await self.knowledge_base_repository.get_by_code(cursor, kb_code)
            if not kb_row:
                return False
            knowledge_base_id = self._row_id(kb_row)
            file_row = await self.knowledge_fs_entry_repository.get_file_by_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=normalized,
            )
            return file_row is not None
        finally:
            await connection.close()

    async def _apply_front_matter_metadata(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        knowledge_base_id: int,
        content: bytes,
        file_path: str,
    ) -> None:
        """Parse front matter and auto-set metadata if repos are available."""
        if self.file_metadata_value_repository is None:
            return

        suffix = PurePosixPath(file_path).suffix.lower()
        if suffix not in {".md", ".markdown"}:
            return

        front_matter = _parse_front_matter(content)
        if not front_matter:
            return

        for field_name, value in front_matter.items():
            value_type = infer_metadata_value_type(value)
            await self.file_metadata_value_repository.upsert_value(
                cursor,
                fs_entry_id=fs_entry_id,
                knowledge_base_id=knowledge_base_id,
                property_name=str(field_name),
                value_type=value_type,
                value=normalize_metadata_value(value, value_type),
            )

    async def file_to_markdown_index(
        self, request: FileToMarkdownIndexRequest, *, document_chunking_service: Any
    ) -> None:
        """Synchronously create and execute one build task."""
        build_task_id = await self.create_file_to_markdown_index_task(request)
        await self.execute_file_to_markdown_index_task(
            request,
            document_chunking_service=document_chunking_service,
            build_task_id=build_task_id,
        )

    async def create_file_to_markdown_index_task(
        self, request: FileToMarkdownIndexRequest
    ) -> int:
        """Create a new build task or reject when one is already running."""
        logger.info(
            "knowledge_item_ingestion_service.create_file_to_markdown_index_task started: kb_code=%s, file_path=%s",
            request.kb_code,
            request.file_path,
        )
        normalized_file_path = request.file_path.strip("/")
        if not normalized_file_path:
            raise KnowledgeBaseValidationError("file_path must not be empty")

        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = await self.knowledge_base_repository.get_by_code(
                cursor, request.kb_code
            )
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)

            file_row = await self.knowledge_fs_entry_repository.get_file_by_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=normalized_file_path,
            )
            if file_row is None:
                raise KnowledgeBaseValidationError(
                    f"file not found: {request.file_path}"
                )
            fs_entry_id = self._row_id(file_row)
            file_object_key = file_row.get("file_object_key")
            if not file_object_key:
                raise KnowledgeBaseValidationError(
                    f"file has not been uploaded yet: {request.file_path}"
                )

            latest_task = None
            if self.knowledge_build_task_repository is not None:
                latest_task = await self.knowledge_build_task_repository.get_latest_by_fs_entry_id(
                    cursor,
                    fs_entry_id=fs_entry_id,
                )
            if latest_task is not None and latest_task.get("status") == "running":
                raise KnowledgeBaseValidationError(
                    f"build task already exists for file: {request.file_path}"
                )

            if self.knowledge_build_task_repository is None:
                await connection.commit()
                return 0

            try:
                created_task = await self.knowledge_build_task_repository.create_task(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    fs_entry_id=fs_entry_id,
                    status=BUILD_STATUS_RUNNING,
                    current_step=BUILD_STEP_MARKDOWN,
                )
            except Exception as exc:
                if self._looks_like_running_task_conflict(exc):
                    raise KnowledgeBaseValidationError(
                        f"build task already exists for file: {request.file_path}"
                    ) from exc
                raise
            await connection.commit()
            if created_task is None:
                raise KnowledgeBaseValidationError(
                    f"failed to create build task: {request.file_path}"
                )
            return self._row_id(created_task)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def execute_file_to_markdown_index_task(
        self,
        request: FileToMarkdownIndexRequest,
        *,
        document_chunking_service: Any,
        build_task_id: int,
    ) -> None:
        """Download uploaded file, parse to markdown, chunk, embed, and persist."""
        logger.info(
            "knowledge_item_ingestion_service.execute_file_to_markdown_index_task started: kb_code=%s, file_path=%s, build_task_id=%s",
            request.kb_code,
            request.file_path,
            build_task_id,
        )
        normalized_file_path = request.file_path.strip("/")
        if not normalized_file_path:
            raise KnowledgeBaseValidationError("file_path must not be empty")

        connection = await self.connection_factory()
        markdown_location: StorageLocation | None = None
        try:
            cursor = connection.cursor()

            kb_row = await self.knowledge_base_repository.get_by_code(
                cursor, request.kb_code
            )
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)

            file_row = await self.knowledge_fs_entry_repository.get_file_by_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=normalized_file_path,
            )
            if file_row is None:
                raise KnowledgeBaseValidationError(
                    f"file not found: {request.file_path}"
                )
            fs_entry_id = self._row_id(file_row)
            original_location = StorageLocation(
                namespace=str(file_row.get("file_bucket_name") or ""),
                key=str(file_row.get("file_object_key") or ""),
            )
            if not original_location.namespace or not original_location.key:
                raise KnowledgeBaseValidationError(
                    f"file has not been uploaded yet: {request.file_path}"
                )
            file_bytes = await self.storage_provider.read(original_location)

            file_type = self._derive_file_type(file_row, normalized_file_path)

            logger.info(
                "file_to_markdown_index stage started: stage=extract_text, file_type=%s, file_size=%s",
                file_type,
                len(file_bytes),
            )
            try:
                markdown_content = await asyncio.to_thread(
                    document_chunking_service.extract_text_from_file,
                    file_bytes,
                    file_type,
                )
            except UnsupportedFileTypeError as exc:
                logger.info(
                    "file_to_markdown_index unsupported file type: kb_code=%s, file_path=%s, error=%s",
                    request.kb_code,
                    normalized_file_path,
                    exc,
                )
                await self._update_build_task(
                    cursor,
                    task_id=build_task_id,
                    status=BUILD_STATUS_UNSUPPORTED,
                    current_step=BUILD_STEP_MARKDOWN,
                    error_message=str(exc) or "unsupported file type",
                    finished=True,
                )
                await connection.commit()
                return
            logger.info(
                "file_to_markdown_index stage completed: stage=extract_text, md_length=%s",
                len(markdown_content),
            )
            await self._update_build_task(
                cursor,
                task_id=build_task_id,
                status=BUILD_STATUS_RUNNING,
                current_step=BUILD_STEP_CHUNKING,
            )

            markdown_bytes = markdown_content.encode("utf-8")
            original_name = (
                file_row.get("name") or PurePosixPath(normalized_file_path).name
            )
            chunk_filename = PurePosixPath(original_name).stem + ".md"
            logger.info(
                "file_to_markdown_index stage started: stage=chunk_and_embed, filename=%s",
                chunk_filename,
            )
            chunks = await asyncio.to_thread(
                document_chunking_service.chunk_and_embed,
                markdown_bytes,
                filename=chunk_filename,
            )
            logger.info(
                "file_to_markdown_index stage completed: stage=chunk_and_embed, chunk_count=%s",
                len(chunks),
            )
            await self._update_build_task(
                cursor,
                task_id=build_task_id,
                status=BUILD_STATUS_RUNNING,
                current_step=BUILD_STEP_VECTORIZING,
            )

            self._validate_chunk_embedding_dimensions(chunks)

            markdown_location = self.storage_provider.build_markdown_location(
                kb_code=request.kb_code,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
                file_path=normalized_file_path,
            )
            stored_markdown = await self.storage_provider.write(
                markdown_location,
                markdown_bytes,
                content_type="text/markdown; charset=utf-8",
            )

            chunk_rows = (
                await self.knowledge_item_chunk_repository.replace_for_fs_entry(
                    cursor,
                    fs_entry_id=fs_entry_id,
                    chunks=[chunk.model_dump() for chunk in chunks],
                )
            )
            chunk_id_by_no = {row["chunk_no"]: self._row_id(row) for row in chunk_rows}
            await self.knowledge_item_chunk_repository.replace_embeddings(
                cursor,
                embeddings=[
                    {
                        "chunk_id": chunk_id_by_no[chunk.chunk_no],
                        "embedding": chunk.embedding,
                    }
                    for chunk in chunks
                ],
            )

            line_count = markdown_content.count("\n") + 1
            await self.knowledge_fs_entry_repository.update_markdown_metadata(
                cursor,
                fs_entry_id=fs_entry_id,
                markdown_location=stored_markdown.location,
                line_count=line_count,
            )

            await self.retrieval_projection_repository.refresh_for_fs_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
                full_path=normalized_file_path,
            )

            await self._update_build_task(
                cursor,
                task_id=build_task_id,
                status=BUILD_STATUS_COMPLETE,
                current_step=BUILD_STEP_COMPLETE,
                finished=True,
            )

            await connection.commit()

            logger.info(
                "knowledge_item_ingestion_service.file_to_markdown_index finished: kb_code=%s, file_path=%s, chunk_count=%s",
                request.kb_code,
                request.file_path,
                len(chunks),
            )
        except Exception as exc:
            await connection.rollback()
            if markdown_location is not None:
                await self.storage_provider.delete_quietly(markdown_location)
            if build_task_id is not None:
                retry_cursor = connection.cursor()
                await self._update_build_task(
                    retry_cursor,
                    task_id=build_task_id,
                    status=BUILD_STATUS_FAILED,
                    error_message=str(exc) or "internal error",
                    finished=True,
                )
                await connection.commit()
            raise
        finally:
            await connection.close()

    async def delete_knowledge_item(self, request: DeleteKnowledgeItemRequest) -> None:
        """Logically delete one file entry and clear derived artifacts."""
        logger.info(
            "knowledge_item_ingestion_service.delete_knowledge_item started: kb_code=%s, file_path=%s",
            request.kb_code,
            request.file_path,
        )
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = await self.knowledge_base_repository.get_by_code(
                cursor, request.kb_code
            )
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)
            file_row = await self.knowledge_fs_entry_repository.get_file_by_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=request.file_path.strip("/"),
            )
            if file_row is None:
                raise KnowledgeBaseValidationError(
                    f"knowledge item not found: {request.file_path}"
                )
            fs_entry_id = int(file_row["kid"])
            await self.knowledge_fs_entry_repository.soft_delete_file_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
            )
            await cursor.execute(
                """
                DELETE FROM knowledge_chunk_retrieval_mv
                WHERE knowledge_base_id = %(knowledge_base_id)s
                  AND fs_entry_id = %(fs_entry_id)s
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "fs_entry_id": fs_entry_id,
                },
            )
            if self.knowledge_fetch_cache_repository is not None:
                await self.knowledge_fetch_cache_repository.delete_cache_entries_for_fs_entry_ids(
                    cursor,
                    fs_entry_ids=[fs_entry_id],
                )
            await cursor.execute(
                """
                UPDATE knowledge_file_metadata_value
                   SET is_deleted = true, updated_at = NOW()
                 WHERE knowledge_base_id = %(knowledge_base_id)s
                   AND fs_entry_id = %(fs_entry_id)s
                   AND is_deleted = false
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "fs_entry_id": fs_entry_id,
                },
            )
            await connection.commit()
            if self.storage_provider.storage_path_bound_to_logical_path:
                original_location = _build_optional_location(
                    file_row,
                    namespace_key="file_bucket_name",
                    key_key="file_object_key",
                )
                if original_location is not None:
                    await self.storage_provider.delete_quietly(original_location)
                markdown_location = _build_optional_location(
                    file_row,
                    namespace_key="markdown_bucket_name",
                    key_key="markdown_object_key",
                )
                if markdown_location is not None:
                    await self.storage_provider.delete_quietly(markdown_location)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    def _validate_chunk_embedding_dimensions(self, chunks: list[Any]) -> None:
        """Ensure all write-index embeddings match the configured dimension."""
        for chunk in chunks:
            if len(chunk.embedding) != self.embedding_dimension:
                raise KnowledgeBaseValidationError(
                    "embedding dimension does not match EMBEDDING_DIMENSION"
                )

    def _derive_file_type(self, file_row: dict[str, Any], file_path: str) -> str:
        """Derive a file type label from mime_type or file extension."""
        mime_to_type = {
            "application/pdf": "pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
            "text/plain": "txt",
            "text/markdown": "md",
            "text/csv": "csv",
        }
        mime_type = file_row.get("mime_type")
        if mime_type and mime_type in mime_to_type:
            return mime_to_type[mime_type]
        suffix = PurePosixPath(file_path).suffix.lower()
        return suffix[1:] if suffix.startswith(".") else suffix

    def _row_id(self, row: dict[str, Any]) -> int:
        if "kid" in row:
            return int(row["kid"])
        return int(row["id"])

    def _looks_like_running_task_conflict(self, exc: Exception) -> bool:
        message = str(exc).lower()
        conflict_markers = (
            "running task already exists",
            "uq_knowledge_build_task_running_per_file",
            "duplicate key",
            "unique constraint",
            "unique violation",
        )
        return any(marker in message for marker in conflict_markers)

    async def _update_build_task(
        self,
        cursor: Any,
        *,
        task_id: int | None,
        status: str | None = None,
        current_step: str | None = None,
        error_message: str | None = None,
        finished: bool = False,
    ) -> None:
        if task_id is None or self.knowledge_build_task_repository is None:
            return
        await self.knowledge_build_task_repository.update_task(
            cursor,
            task_id=task_id,
            status=status,
            current_step=current_step,
            error_message=error_message,
            finished=finished,
        )
