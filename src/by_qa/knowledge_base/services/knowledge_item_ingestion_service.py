"""Transactional service for document, chunk, and embedding ingestion."""

import base64
import binascii
import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import (
    DeleteKnowledgeItemRequest,
    DeleteKnowledgeItemResponse,
    KnowledgeItemImportFileResponse,
    KnowledgeItemImportManifest,
    KnowledgeItemImportRequest,
    KnowledgeItemImportResponse,
    WriteFileRequest,
    WriteFileResponse,
    WriteIndexRequest,
    WriteIndexResponse,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError


@dataclass
class KnowledgeItemIngestionService:
    """Import markdown documents, chunks, and embeddings transactionally."""

    connection_factory: Callable[[], Any]
    knowledge_base_repository: Any
    knowledge_fs_entry_repository: Any
    knowledge_item_repository: Any
    knowledge_item_version_repository: Any
    knowledge_item_chunk_repository: Any
    retrieval_projection_repository: Any
    object_storage: Any
    embedding_dimension: int

    def delete_knowledge_item(
        self, request: DeleteKnowledgeItemRequest
    ) -> DeleteKnowledgeItemResponse:
        """Logically delete one knowledge item and its file-tree entry."""
        logger.info(
            "knowledge_item_ingestion_service.delete_knowledge_item started: kb_code=%s, file_code=%s",
            request.kb_code,
            request.file_code,
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
            item_row = self.knowledge_item_repository.get_by_item_code(
                cursor,
                knowledge_base_id=knowledge_base_id,
                item_code=request.file_code,
            )
            if item_row is None:
                raise KnowledgeBaseValidationError(
                    f"knowledge item not found: {request.file_code}"
                )
            self.knowledge_item_repository.soft_delete_by_item_code(
                cursor,
                knowledge_base_id=knowledge_base_id,
                item_code=request.file_code,
            )
            self.knowledge_fs_entry_repository.soft_delete_file_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=int(item_row["fs_entry_id"]),
            )
            self.retrieval_projection_repository.delete_for_item(
                cursor,
                knowledge_item_id=self._row_id(item_row),
            )
            connection.commit()
            return DeleteKnowledgeItemResponse(
                kb_code=request.kb_code,
                file_code=request.file_code,
                is_deleted=True,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def import_knowledge_item(
        self, request: KnowledgeItemImportRequest
    ) -> KnowledgeItemImportFileResponse:
        """Atomically import the original file, markdown sidecar, and chunk indexes."""
        logger.info(
            "knowledge_item_ingestion_service.import_knowledge_item started: kb_code=%s, file_code=%s, file_path=%s, version=%s, chunk_count=%s",
            request.kb_code,
            request.file_code,
            request.file_path,
            request.version,
            len(request.chunks),
        )
        normalized_file_path = request.file_path.strip()
        if not normalized_file_path:
            raise KnowledgeBaseValidationError("file_path must not be empty")
        normalized_object_path = normalized_file_path.strip("/")
        if not normalized_object_path:
            raise KnowledgeBaseValidationError("file_path must not be root")

        self._validate_chunk_embedding_dimensions(request.chunks)
        original_bytes = self._decode_file_content(request)
        original_checksum = hashlib.sha256(original_bytes).hexdigest()
        markdown_bytes = request.markdown_content.encode("utf-8")
        markdown_checksum = hashlib.sha256(markdown_bytes).hexdigest()
        type_code = self._derive_type_code(normalized_object_path)

        original_temp_object_key = self.object_storage.upload_temp_object(
            f"import-{request.file_code}-{request.version}-original",
            original_bytes,
            content_type="application/octet-stream",
            bucket_name=self.object_storage.bucket_name,
        )
        markdown_temp_object_key = self.object_storage.upload_temp_object(
            f"import-{request.file_code}-{request.version}-markdown",
            markdown_bytes,
            content_type="text/markdown; charset=utf-8",
            bucket_name=self.object_storage.markdown_bucket_name,
        )
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = self.knowledge_base_repository.get_by_code(cursor, request.kb_code)
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            if kb_row["status"] != "ACTIVE":
                raise KnowledgeBaseValidationError(
                    f"knowledge base is not ACTIVE: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)
            deleted_item_by_code = self.knowledge_item_repository.get_any_by_item_code(
                cursor,
                knowledge_base_id=knowledge_base_id,
                item_code=request.file_code,
            )
            if deleted_item_by_code and deleted_item_by_code.get("is_deleted") is True:
                raise KnowledgeBaseValidationError(
                    f"file_code is occupied by a soft-deleted knowledge item: {request.file_code}"
                )
            root_entry_row = self.knowledge_fs_entry_repository.ensure_root_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                kb_name=kb_row["kb_name"],
            )
            root_entry_id = self._row_id(root_entry_row)
            try:
                file_entry_row = self.knowledge_fs_entry_repository.ensure_file_entry(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    root_entry_id=root_entry_id,
                    full_path=normalized_object_path,
                )
            except ValueError as exc:
                raise KnowledgeBaseValidationError(str(exc)) from exc
            fs_entry_id = self._row_id(file_entry_row)
            existing_item = self.knowledge_item_repository.get_by_fs_entry_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
            )
            if existing_item and str(existing_item["item_code"]) != request.file_code:
                raise KnowledgeBaseValidationError(
                    f"file_path already bound to another file_code: {normalized_file_path}"
                )
            existing_version = None
            if existing_item:
                existing_version = (
                    self.knowledge_item_version_repository.get_by_item_and_version(
                        cursor,
                        knowledge_item_id=self._row_id(existing_item),
                        version=request.version,
                    )
                )
                if existing_version:
                    raise KnowledgeBaseValidationError(
                        f"item_code/version already exists: {request.file_code}/{request.version}"
                    )
            metadata = dict(request.metadata or {})
            if request.file_description is not None:
                metadata["file_description"] = request.file_description
            item_row = self.knowledge_item_repository.upsert(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
                item_code=request.file_code,
                item_kind="FILE",
                description=request.file_description,
                status=request.status,
                source_code=request.source_code,
                type_code=type_code,
                metadata=metadata,
            )
            knowledge_item_id = self._row_id(item_row)
            original_object_key = self.object_storage.build_original_object_key(
                knowledge_base_id=knowledge_base_id,
                knowledge_item_id=knowledge_item_id,
                version=request.version,
            )
            markdown_object_key = self.object_storage.build_markdown_object_key(
                knowledge_base_id=knowledge_base_id,
                knowledge_item_id=knowledge_item_id,
                version=request.version,
            )
            version_row = self.knowledge_item_version_repository.upsert(
                cursor,
                knowledge_item_id=knowledge_item_id,
                fs_entry_id=fs_entry_id,
                version=request.version,
                bucket_name=self.object_storage.bucket_name,
                object_key=original_object_key,
                markdown_bucket_name=self.object_storage.markdown_bucket_name,
                markdown_object_key=markdown_object_key,
                markdown_file_size=len(markdown_bytes),
                markdown_checksum=markdown_checksum,
                file_size=len(original_bytes),
                checksum=original_checksum,
            )
            chunk_rows = self.knowledge_item_chunk_repository.replace_for_version(
                cursor,
                knowledge_item_id=self._row_id(item_row),
                knowledge_item_version_id=self._row_id(version_row),
                chunks=[chunk.model_dump() for chunk in request.chunks],
            )
            chunk_id_by_no = {row["chunk_no"]: self._row_id(row) for row in chunk_rows}
            self.knowledge_item_chunk_repository.replace_embeddings(
                cursor,
                embeddings=[
                    {
                        "chunk_id": chunk_id_by_no[chunk.chunk_no],
                        "embedding": chunk.embedding,
                    }
                    for chunk in request.chunks
                ],
            )
            self.knowledge_item_repository.update_current_version(
                cursor,
                knowledge_item_id=knowledge_item_id,
                version_id=self._row_id(version_row),
            )
            self.retrieval_projection_repository.refresh_for_item(
                cursor, knowledge_item_id=knowledge_item_id
            )
            connection.commit()
            self.object_storage.promote_temp_object(
                original_temp_object_key,
                original_object_key,
                bucket_name=self.object_storage.bucket_name,
            )
            self.object_storage.promote_temp_object(
                markdown_temp_object_key,
                markdown_object_key,
                bucket_name=self.object_storage.markdown_bucket_name,
            )
            logger.info(
                "knowledge_item_ingestion_service.import_knowledge_item finished: kb_code=%s, file_code=%s, file_path=%s, version=%s, chunk_count=%s",
                request.kb_code,
                request.file_code,
                normalized_file_path,
                request.version,
                len(request.chunks),
            )
            return KnowledgeItemImportFileResponse(
                kb_code=request.kb_code,
                file_code=request.file_code,
                type_code=type_code,
                file_path=normalized_file_path,
                file_description=request.file_description,
                version=request.version,
                status=request.status,
                metadata=request.metadata,
                chunks={"count": len(request.chunks)},
            )
        except Exception:
            connection.rollback()
            self.object_storage.delete_object_quietly(
                original_temp_object_key,
                bucket_name=self.object_storage.bucket_name,
            )
            self.object_storage.delete_object_quietly(
                markdown_temp_object_key,
                bucket_name=self.object_storage.markdown_bucket_name,
            )
            raise
        finally:
            connection.close()

    def write_index(self, request: WriteIndexRequest) -> WriteIndexResponse:
        """Write chunk indexes for an existing file version."""
        logger.info(
            "knowledge_item_ingestion_service.write_index started: kb_code=%s, file_code=%s, version=%s, chunk_count=%s",
            request.kb_code,
            request.file_code,
            request.version,
            len(request.chunks),
        )
        self._validate_chunk_embedding_dimensions(request.chunks)
        markdown_bytes = request.markdown_content.encode("utf-8")
        markdown_checksum = hashlib.sha256(markdown_bytes).hexdigest()
        temp_object_key = self.object_storage.upload_temp_object(
            f"index-{request.file_code}-{request.version}",
            markdown_bytes,
            content_type="text/markdown; charset=utf-8",
            bucket_name=self.object_storage.markdown_bucket_name,
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
            item_row = self.knowledge_item_repository.get_by_item_code(
                cursor,
                knowledge_base_id=knowledge_base_id,
                item_code=request.file_code,
            )
            if item_row is None:
                raise KnowledgeBaseValidationError(
                    f"knowledge item not found: {request.file_code}"
                )
            version_row = (
                self.knowledge_item_version_repository.get_by_item_and_version(
                    cursor,
                    knowledge_item_id=self._row_id(item_row),
                    version=request.version,
                )
            )
            if version_row is None:
                raise KnowledgeBaseValidationError(
                    f"knowledge item version not found: {request.file_code}/{request.version}"
                )
            markdown_object_key = self.object_storage.build_markdown_object_key(
                knowledge_base_id=knowledge_base_id,
                knowledge_item_id=self._row_id(item_row),
                version=request.version,
            )
            self.knowledge_item_version_repository.upsert(
                cursor,
                knowledge_item_id=self._row_id(item_row),
                fs_entry_id=int(item_row["fs_entry_id"]),
                version=request.version,
                bucket_name=str(version_row["bucket_name"]),
                object_key=str(version_row["object_key"]),
                markdown_bucket_name=self.object_storage.markdown_bucket_name,
                markdown_object_key=markdown_object_key,
                markdown_file_size=len(markdown_bytes),
                markdown_checksum=markdown_checksum,
                file_size=int(version_row.get("file_size") or 0),
                checksum=version_row.get("checksum"),
            )
            chunk_rows = self.knowledge_item_chunk_repository.replace_for_version(
                cursor,
                knowledge_item_id=self._row_id(item_row),
                knowledge_item_version_id=self._row_id(version_row),
                chunks=[chunk.model_dump() for chunk in request.chunks],
            )
            chunk_id_by_no = {row["chunk_no"]: self._row_id(row) for row in chunk_rows}
            self.knowledge_item_chunk_repository.replace_embeddings(
                cursor,
                embeddings=[
                    {
                        "chunk_id": chunk_id_by_no[chunk.chunk_no],
                        "embedding": chunk.embedding,
                    }
                    for chunk in request.chunks
                ],
            )
            self.knowledge_item_repository.update_current_version(
                cursor,
                knowledge_item_id=self._row_id(item_row),
                version_id=self._row_id(version_row),
            )
            self.retrieval_projection_repository.refresh_for_item(
                cursor, knowledge_item_id=self._row_id(item_row)
            )
            connection.commit()
            self.object_storage.promote_temp_object(
                temp_object_key,
                markdown_object_key,
                bucket_name=self.object_storage.markdown_bucket_name,
            )
            logger.info(
                "knowledge_item_ingestion_service.write_index finished: kb_code=%s, file_code=%s, version=%s, chunk_count=%s",
                request.kb_code,
                request.file_code,
                request.version,
                len(request.chunks),
            )
            return WriteIndexResponse(
                kb_code=request.kb_code,
                file_code=request.file_code,
                version=request.version,
                chunks={"count": len(request.chunks)},
            )
        except Exception:
            connection.rollback()
            self.object_storage.delete_object_quietly(
                temp_object_key,
                bucket_name=self.object_storage.markdown_bucket_name,
            )
            raise
        finally:
            connection.close()

    def write_file(self, request: WriteFileRequest) -> WriteFileResponse:
        """Write one file version into the knowledge base without indexing it yet."""
        logger.info(
            "knowledge_item_ingestion_service.write_file started: kb_code=%s, file_code=%s, file_path=%s, version=%s",
            request.kb_code,
            request.file_code,
            request.file_path,
            request.version,
        )
        normalized_file_path = request.file_path.strip()
        if not normalized_file_path:
            raise KnowledgeBaseValidationError("file_path must not be empty")
        normalized_object_path = normalized_file_path.strip("/")
        if not normalized_object_path:
            raise KnowledgeBaseValidationError("file_path must not be root")

        content_bytes = self._decode_file_content(request)
        checksum = hashlib.sha256(content_bytes).hexdigest()
        type_code = self._derive_type_code(normalized_object_path)
        import_request_id = f"write-{request.file_code}-{request.version}"
        temp_object_key = self.object_storage.upload_temp_object(
            import_request_id,
            content_bytes,
            content_type="application/octet-stream",
            bucket_name=self.object_storage.bucket_name,
        )
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = self.knowledge_base_repository.get_by_code(cursor, request.kb_code)
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            if kb_row["status"] != "ACTIVE":
                raise KnowledgeBaseValidationError(
                    f"knowledge base is not ACTIVE: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)
            deleted_item_by_code = self.knowledge_item_repository.get_any_by_item_code(
                cursor,
                knowledge_base_id=knowledge_base_id,
                item_code=request.file_code,
            )
            if deleted_item_by_code and deleted_item_by_code.get("is_deleted") is True:
                raise KnowledgeBaseValidationError(
                    f"file_code is occupied by a soft-deleted knowledge item: {request.file_code}"
                )
            root_entry_row = self.knowledge_fs_entry_repository.ensure_root_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                kb_name=kb_row["kb_name"],
            )
            root_entry_id = self._row_id(root_entry_row)
            try:
                file_entry_row = self.knowledge_fs_entry_repository.ensure_file_entry(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    root_entry_id=root_entry_id,
                    full_path=normalized_object_path,
                )
            except ValueError as exc:
                raise KnowledgeBaseValidationError(str(exc)) from exc
            fs_entry_id = self._row_id(file_entry_row)
            existing_item = self.knowledge_item_repository.get_by_fs_entry_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
            )
            if existing_item and str(existing_item["item_code"]) != request.file_code:
                raise KnowledgeBaseValidationError(
                    f"file_path already bound to another file_code: {normalized_file_path}"
                )
            existing_version = None
            if existing_item:
                existing_version = (
                    self.knowledge_item_version_repository.get_by_item_and_version(
                        cursor,
                        knowledge_item_id=self._row_id(existing_item),
                        version=request.version,
                    )
                )
                if existing_version:
                    raise KnowledgeBaseValidationError(
                        f"item_code/version already exists: {request.file_code}/{request.version}"
                    )
            metadata = dict(request.metadata or {})
            if request.file_description is not None:
                metadata["file_description"] = request.file_description
            item_row = self.knowledge_item_repository.upsert(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
                item_code=request.file_code,
                item_kind="FILE",
                description=request.file_description,
                status=request.status,
                source_code=request.source_code,
                type_code=type_code,
                metadata=metadata,
            )
            knowledge_item_id = self._row_id(item_row)
            final_object_key = self.object_storage.build_original_object_key(
                knowledge_base_id=knowledge_base_id,
                knowledge_item_id=knowledge_item_id,
                version=request.version,
            )
            version_row = self.knowledge_item_version_repository.upsert(
                cursor,
                knowledge_item_id=knowledge_item_id,
                fs_entry_id=fs_entry_id,
                version=request.version,
                bucket_name=self.object_storage.bucket_name,
                object_key=final_object_key,
                markdown_bucket_name=None,
                markdown_object_key=None,
                markdown_file_size=None,
                markdown_checksum=None,
                file_size=len(content_bytes),
                checksum=checksum,
            )
            self.knowledge_item_repository.update_current_version(
                cursor,
                knowledge_item_id=knowledge_item_id,
                version_id=self._row_id(version_row),
            )
            connection.commit()
            self.object_storage.promote_temp_object(
                temp_object_key,
                final_object_key,
                bucket_name=self.object_storage.bucket_name,
            )
            logger.info(
                "knowledge_item_ingestion_service.write_file finished: kb_code=%s, file_code=%s, file_path=%s, version=%s, type_code=%s",
                request.kb_code,
                request.file_code,
                normalized_file_path,
                request.version,
                type_code,
            )
            return WriteFileResponse(
                kb_code=request.kb_code,
                file_code=request.file_code,
                type_code=type_code,
                file_path=normalized_file_path,
                file_description=request.file_description,
                version=request.version,
                status=request.status,
                metadata=request.metadata,
            )
        except Exception:
            connection.rollback()
            self.object_storage.delete_object_quietly(
                temp_object_key,
                bucket_name=self.object_storage.bucket_name,
            )
            raise
        finally:
            connection.close()

    def import_document(
        self, *, markdown_bytes: bytes, manifest: KnowledgeItemImportManifest
    ) -> KnowledgeItemImportResponse:
        """Import one markdown document, its chunks, and its embeddings."""
        logger.info(
            "knowledge_item_ingestion_service.import_document started: kb_code=%s, item_code=%s, version=%s, chunk_count=%s, content_bytes=%s",
            manifest.kb_code,
            manifest.document.item_code,
            manifest.document.version,
            len(manifest.chunks),
            len(markdown_bytes),
        )
        self._validate_embedding_dimensions(manifest)
        logger.info(
            "knowledge_item_ingestion_service embedding validation finished: item_code=%s, expected_dimension=%s, chunk_count=%s",
            manifest.document.item_code,
            self.embedding_dimension,
            len(manifest.chunks),
        )
        checksum = hashlib.sha256(markdown_bytes).hexdigest()
        import_request_id = (
            f"import-{manifest.document.item_code}-{manifest.document.version}"
        )
        temp_object_key = self.object_storage.upload_temp_object(
            import_request_id,
            markdown_bytes,
            content_type="text/markdown; charset=utf-8",
            bucket_name=self.object_storage.bucket_name,
        )
        markdown_temp_object_key = self.object_storage.upload_temp_object(
            f"{import_request_id}-markdown",
            markdown_bytes,
            content_type="text/markdown; charset=utf-8",
            bucket_name=self.object_storage.markdown_bucket_name,
        )
        logger.info(
            "knowledge_item_ingestion_service temp object upload finished: item_code=%s, import_request_id=%s, temp_object_key=%s",
            manifest.document.item_code,
            import_request_id,
            temp_object_key,
        )
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = self.knowledge_base_repository.get_by_code(
                cursor, manifest.kb_code
            )
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {manifest.kb_code}"
                )
            if kb_row["status"] != "ACTIVE":
                raise KnowledgeBaseValidationError(
                    f"knowledge base is not ACTIVE: {manifest.kb_code}"
                )
            logger.info(
                "knowledge_item_ingestion_service knowledge base validation finished: kb_code=%s, knowledge_base_id=%s, status=%s",
                manifest.kb_code,
                self._row_id(kb_row),
                kb_row["status"],
            )
            knowledge_base_id = self._row_id(kb_row)
            deleted_item_by_code = self.knowledge_item_repository.get_any_by_item_code(
                cursor,
                knowledge_base_id=knowledge_base_id,
                item_code=manifest.document.item_code,
            )
            if deleted_item_by_code and deleted_item_by_code.get("is_deleted") is True:
                raise KnowledgeBaseValidationError(
                    "file_code is occupied by a soft-deleted knowledge item: "
                    f"{manifest.document.item_code}"
                )
            root_entry_row = self.knowledge_fs_entry_repository.ensure_root_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                kb_name=kb_row["kb_name"],
            )
            root_entry_id = self._row_id(root_entry_row)
            try:
                file_entry_row = self.knowledge_fs_entry_repository.ensure_file_entry(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    root_entry_id=root_entry_id,
                    full_path=manifest.document.full_path,
                )
            except ValueError as exc:
                raise KnowledgeBaseValidationError(str(exc)) from exc
            fs_entry_id = self._row_id(file_entry_row)
            existing_item = self.knowledge_item_repository.get_by_fs_entry_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
            )
            existing_version = None
            if existing_item:
                existing_version = (
                    self.knowledge_item_version_repository.get_by_item_and_version(
                        cursor,
                        knowledge_item_id=self._row_id(existing_item),
                        version=manifest.document.version,
                    )
                )
                if existing_version:
                    raise KnowledgeBaseValidationError(
                        "item_code/version already exists: "
                        f"{manifest.document.item_code}/{manifest.document.version}"
                    )
            logger.info(
                "knowledge_item_ingestion_service duplicate check finished: item_code=%s, existing_item=%s, existing_version=%s",
                manifest.document.item_code,
                existing_item is not None,
                existing_version is not None,
            )
            item_row = self.knowledge_item_repository.upsert(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
                item_code=manifest.document.item_code,
                item_kind="FILE",
                description=None,
                status=manifest.document.status,
                source_code=manifest.document.source_code,
                type_code=manifest.document.type_code,
                metadata=manifest.document.metadata,
            )
            knowledge_item_id = self._row_id(item_row)
            final_object_key = self.object_storage.build_original_object_key(
                knowledge_base_id=knowledge_base_id,
                knowledge_item_id=knowledge_item_id,
                version=manifest.document.version,
            )
            markdown_object_key = self.object_storage.build_markdown_object_key(
                knowledge_base_id=knowledge_base_id,
                knowledge_item_id=knowledge_item_id,
                version=manifest.document.version,
            )
            version_row = self.knowledge_item_version_repository.upsert(
                cursor,
                knowledge_item_id=knowledge_item_id,
                fs_entry_id=fs_entry_id,
                version=manifest.document.version,
                bucket_name=self.object_storage.bucket_name,
                object_key=final_object_key,
                markdown_bucket_name=self.object_storage.markdown_bucket_name,
                markdown_object_key=markdown_object_key,
                markdown_file_size=len(markdown_bytes),
                markdown_checksum=checksum,
                file_size=len(markdown_bytes),
                checksum=checksum,
            )
            chunk_rows = self.knowledge_item_chunk_repository.replace_for_version(
                cursor,
                knowledge_item_id=self._row_id(item_row),
                knowledge_item_version_id=self._row_id(version_row),
                chunks=[chunk.model_dump() for chunk in manifest.chunks],
            )
            self.knowledge_item_repository.update_current_version(
                cursor,
                knowledge_item_id=self._row_id(item_row),
                version_id=self._row_id(version_row),
            )
            chunk_id_by_no = {row["chunk_no"]: self._row_id(row) for row in chunk_rows}
            self.knowledge_item_chunk_repository.replace_embeddings(
                cursor,
                embeddings=[
                    {
                        "chunk_id": chunk_id_by_no[chunk.chunk_no],
                        "embedding": chunk.embedding,
                    }
                    for chunk in manifest.chunks
                ],
            )
            logger.info(
                "knowledge_item_ingestion_service persistence finished: item_code=%s, knowledge_item_id=%s, version_id=%s, chunk_count=%s, final_object_key=%s",
                manifest.document.item_code,
                knowledge_item_id,
                self._row_id(version_row),
                len(manifest.chunks),
                final_object_key,
            )
            self.retrieval_projection_repository.refresh_for_item(
                cursor, knowledge_item_id=self._row_id(item_row)
            )
            connection.commit()
            self.object_storage.promote_temp_object(
                temp_object_key,
                final_object_key,
                bucket_name=self.object_storage.bucket_name,
            )
            self.object_storage.promote_temp_object(
                markdown_temp_object_key,
                markdown_object_key,
                bucket_name=self.object_storage.markdown_bucket_name,
            )
            logger.info(
                "knowledge_item_ingestion_service.import_document finished: item_code=%s, version=%s, chunk_count=%s, final_object_key=%s",
                manifest.document.item_code,
                manifest.document.version,
                len(manifest.chunks),
                final_object_key,
            )
            return KnowledgeItemImportResponse(
                kb_code=manifest.kb_code,
                full_path=manifest.document.full_path,
                version=manifest.document.version,
                status=manifest.document.status,
                chunk_count=len(manifest.chunks),
            )
        except Exception:
            connection.rollback()
            logger.warning(
                "knowledge_item_ingestion_service transaction rolled back: kb_code=%s, item_code=%s, version=%s",
                manifest.kb_code,
                manifest.document.item_code,
                manifest.document.version,
            )
            self.object_storage.delete_object_quietly(
                temp_object_key,
                bucket_name=self.object_storage.bucket_name,
            )
            self.object_storage.delete_object_quietly(
                markdown_temp_object_key,
                bucket_name=self.object_storage.markdown_bucket_name,
            )
            logger.warning(
                "knowledge_item_ingestion_service temp object cleaned up: item_code=%s, temp_object_key=%s",
                manifest.document.item_code,
                temp_object_key,
            )
            raise
        finally:
            connection.close()

    def _validate_embedding_dimensions(
        self, manifest: KnowledgeItemImportManifest
    ) -> None:
        """Ensure all embeddings match the configured dimension."""
        for chunk in manifest.chunks:
            if len(chunk.embedding) != self.embedding_dimension:
                raise KnowledgeBaseValidationError(
                    "embedding dimension does not match EMBEDDING_DIMENSION"
                )

    def _validate_chunk_embedding_dimensions(self, chunks: list[Any]) -> None:
        """Ensure all write-index embeddings match the configured dimension."""
        for chunk in chunks:
            if len(chunk.embedding) != self.embedding_dimension:
                raise KnowledgeBaseValidationError(
                    "embedding dimension does not match EMBEDDING_DIMENSION"
                )

    def _decode_file_content(self, request: WriteFileRequest) -> bytes:
        """Decode route payload content into raw bytes."""
        try:
            return base64.b64decode(request.file_content, validate=True)
        except binascii.Error as exc:
            raise KnowledgeBaseValidationError(
                "file_content must be valid base64"
            ) from exc

    def _derive_type_code(self, file_path: str) -> str:
        """Derive type_code from the final extension segment."""
        suffix = PurePosixPath(file_path).suffix.lower()
        return suffix[1:] if suffix.startswith(".") else suffix

    def _row_id(self, row: dict[str, Any]) -> int:
        if "kid" in row:
            return int(row["kid"])
        return int(row["id"])
