"""Transactional service for document, chunk, and embedding ingestion."""

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import (
    DeleteKnowledgeItemRequest,
    DeleteKnowledgeItemResponse,
    FileToMarkdownIndexRequest,
    KnowledgeItemUploadRequest,
    KnowledgeItemUploadResponse,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError


@dataclass
class KnowledgeItemIngestionService:
    """Import markdown documents, chunks, and embeddings transactionally."""

    connection_factory: Callable[[], Any]
    knowledge_base_repository: Any
    knowledge_fs_entry_repository: Any
    knowledge_item_chunk_repository: Any
    retrieval_projection_repository: Any
    object_storage: Any
    embedding_dimension: int

    def upload_file(
        self, request: KnowledgeItemUploadRequest
    ) -> KnowledgeItemUploadResponse:
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

        mime_type = (
            request.content_type
            or mimetypes.guess_type(normalized_object_path)[0]
            or "application/octet-stream"
        )
        checksum = hashlib.sha256(request.file_content).hexdigest()

        connection = self.connection_factory()
        temp_object_key: str | None = None
        try:
            cursor = connection.cursor()
            kb_row = self.knowledge_base_repository.get_by_code(cursor, request.kb_code)
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)

            try:
                file_entry_row = self.knowledge_fs_entry_repository.create_file_entry(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    full_path=normalized_object_path,
                    file_description=request.file_description,
                )
            except ValueError as exc:
                raise KnowledgeBaseValidationError(str(exc)) from exc

            fs_entry_id = self._row_id(file_entry_row)
            temp_object_key = self.object_storage.upload_temp_object(
                f"upload-{knowledge_base_id}-{fs_entry_id}",
                request.file_content,
                content_type=mime_type,
                bucket_name=self.object_storage.bucket_name,
            )
            suffix = PurePosixPath(normalized_object_path).suffix
            final_object_key = (
                f"kb/{knowledge_base_id}/fs-entry/{fs_entry_id}/original{suffix}"
            )
            self.knowledge_fs_entry_repository.update_file_entry_storage(
                cursor,
                fs_entry_id=fs_entry_id,
                file_description=request.file_description,
                file_bucket_name=self.object_storage.bucket_name,
                file_object_key=final_object_key,
                file_size=len(request.file_content),
                mime_type=mime_type,
                checksum=checksum,
            )
            connection.commit()
            self.object_storage.promote_temp_object(
                temp_object_key,
                final_object_key,
                bucket_name=self.object_storage.bucket_name,
            )
            return KnowledgeItemUploadResponse(
                kb_code=request.kb_code,
                file_path=normalized_file_path,
                file_description=request.file_description,
            )
        except Exception:
            connection.rollback()
            if temp_object_key is not None:
                self.object_storage.delete_object_quietly(
                    temp_object_key,
                    bucket_name=self.object_storage.bucket_name,
                )
            raise
        finally:
            connection.close()

    def file_to_markdown_index(
        self, request: FileToMarkdownIndexRequest, *, document_chunking_service: Any
    ) -> None:
        """Download uploaded file, parse to markdown, chunk, embed, and persist."""
        logger.info(
            "knowledge_item_ingestion_service.file_to_markdown_index started: kb_code=%s, file_path=%s",
            request.kb_code,
            request.file_path,
        )
        normalized_file_path = request.file_path.strip("/")
        if not normalized_file_path:
            raise KnowledgeBaseValidationError("file_path must not be empty")

        connection = self.connection_factory()
        markdown_temp_object_key: str | None = None
        try:
            cursor = connection.cursor()

            kb_row = self.knowledge_base_repository.get_by_code(cursor, request.kb_code)
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)

            file_row = self.knowledge_fs_entry_repository.get_file_by_path(
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
            file_bucket_name = (
                file_row.get("file_bucket_name") or self.object_storage.bucket_name
            )

            file_bytes = self.object_storage.download_object(
                file_object_key, bucket_name=file_bucket_name
            )

            file_type = self._derive_file_type(file_row, normalized_file_path)

            logger.info(
                "file_to_markdown_index stage started: stage=extract_text, file_type=%s, file_size=%s",
                file_type,
                len(file_bytes),
            )
            markdown_content = document_chunking_service.extract_text_from_file(
                file_bytes, file_type
            )
            logger.info(
                "file_to_markdown_index stage completed: stage=extract_text, md_length=%s",
                len(markdown_content),
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
            chunks = document_chunking_service.chunk_and_embed(
                markdown_bytes, filename=chunk_filename
            )
            logger.info(
                "file_to_markdown_index stage completed: stage=chunk_and_embed, chunk_count=%s",
                len(chunks),
            )

            self._validate_chunk_embedding_dimensions(chunks)

            markdown_object_key = (
                f"kb/{knowledge_base_id}/fs-entry/{fs_entry_id}/markdown.md"
            )
            markdown_temp_object_key = self.object_storage.upload_temp_object(
                f"ftmi-{knowledge_base_id}-{fs_entry_id}",
                markdown_bytes,
                content_type="text/markdown; charset=utf-8",
                bucket_name=self.object_storage.markdown_bucket_name,
            )

            chunk_rows = self.knowledge_item_chunk_repository.replace_for_fs_entry(
                cursor,
                fs_entry_id=fs_entry_id,
                chunks=[chunk.model_dump() for chunk in chunks],
            )
            chunk_id_by_no = {row["chunk_no"]: self._row_id(row) for row in chunk_rows}
            self.knowledge_item_chunk_repository.replace_embeddings(
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
            self.knowledge_fs_entry_repository.update_markdown_metadata(
                cursor,
                fs_entry_id=fs_entry_id,
                markdown_bucket_name=self.object_storage.markdown_bucket_name,
                markdown_object_key=markdown_object_key,
                line_count=line_count,
            )

            self.retrieval_projection_repository.refresh_for_fs_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
                full_path=normalized_file_path,
            )

            connection.commit()

            self.object_storage.promote_temp_object(
                markdown_temp_object_key,
                markdown_object_key,
                bucket_name=self.object_storage.markdown_bucket_name,
            )

            logger.info(
                "knowledge_item_ingestion_service.file_to_markdown_index finished: kb_code=%s, file_path=%s, chunk_count=%s",
                request.kb_code,
                request.file_path,
                len(chunks),
            )
        except Exception:
            connection.rollback()
            if markdown_temp_object_key is not None:
                self.object_storage.delete_object_quietly(
                    markdown_temp_object_key,
                    bucket_name=self.object_storage.markdown_bucket_name,
                )
            raise
        finally:
            connection.close()

    def delete_knowledge_item(
        self, request: DeleteKnowledgeItemRequest
    ) -> DeleteKnowledgeItemResponse:
        """Logically delete one file entry and clear derived artifacts."""
        logger.info(
            "knowledge_item_ingestion_service.delete_knowledge_item started: kb_code=%s, file_path=%s",
            request.kb_code,
            request.file_path,
        )
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = self.knowledge_base_repository.get_by_code(cursor, request.kb_code)
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)
            file_row = self.knowledge_fs_entry_repository.get_file_by_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=request.file_path.strip("/"),
            )
            if file_row is None:
                raise KnowledgeBaseValidationError(
                    f"knowledge item not found: {request.file_path}"
                )
            fs_entry_id = int(file_row["kid"])
            self.knowledge_fs_entry_repository.soft_delete_file_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
            )
            cursor.execute(
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
            cursor.execute(
                """
                DELETE FROM knowledge_fetch_cache_index
                WHERE knowledge_base_id = %(knowledge_base_id)s
                  AND fs_entry_id = %(fs_entry_id)s
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "fs_entry_id": fs_entry_id,
                },
            )
            connection.commit()
            file_bucket_name = file_row.get("file_bucket_name")
            file_object_key = file_row.get("file_object_key")
            markdown_bucket_name = file_row.get("markdown_bucket_name")
            markdown_object_key = file_row.get("markdown_object_key")
            if file_object_key:
                self.object_storage.delete_object_quietly(
                    file_object_key,
                    bucket_name=file_bucket_name or self.object_storage.bucket_name,
                )
            if markdown_object_key:
                self.object_storage.delete_object_quietly(
                    markdown_object_key,
                    bucket_name=markdown_bucket_name
                    or self.object_storage.markdown_bucket_name,
                )
            return DeleteKnowledgeItemResponse(
                kb_code=request.kb_code,
                file_path=request.file_path,
                is_deleted=True,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

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
