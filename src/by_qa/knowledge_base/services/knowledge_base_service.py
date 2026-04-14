"""Service for creating and updating knowledge bases."""

import fnmatch
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import (
    CreateDirectoryRequest,
    CreateDirectoryResponse,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeBaseResponse,
    DeleteDirectoryRequest,
    DeleteDirectoryResponse,
    DeleteKnowledgeBaseRequest,
    DeleteKnowledgeBaseResponse,
    KnowledgeItemDownloadRequest,
    KnowledgeItemFetchRequest,
    KnowledgeItemFetchResponse,
    KnowledgeItemGlobRequest,
    KnowledgeItemListDirRequest,
    KnowledgeItemListDirResponse,
    UpdateDirectoryRequest,
    UpdateDirectoryResponse,
    UpdateFileRequest,
    UpdateFileResponse,
    UpdateKnowledgeBaseRequest,
    UpdateKnowledgeBaseResponse,
)
from by_qa.knowledge_base.services.cache_file_lock import acquire_cache_file_lock
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError


@dataclass
class KnowledgeBaseService:
    """Create or update knowledge base records."""

    connection_factory: Callable[[], Any]
    knowledge_base_repository: Any
    knowledge_fs_entry_repository: Any
    knowledge_item_repository: Any | None = None
    retrieval_projection_repository: Any | None = None
    knowledge_fetch_cache_repository: Any | None = None
    object_storage: Any | None = None
    cache_root: Path | None = None
    cache_ttl_seconds: int = 24 * 60 * 60

    def create_knowledge_base(
        self, request: CreateKnowledgeBaseRequest
    ) -> CreateKnowledgeBaseResponse:
        """Create a knowledge base."""
        logger.info(
            "knowledge_base_service.create_knowledge_base started: kb_name=%s, has_description=%s",
            request.kb_name,
            request.kb_description is not None,
        )
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            existing = self.knowledge_base_repository.get_by_name(
                cursor, request.kb_name
            )
            if existing is not None:
                raise KnowledgeBaseValidationError(
                    f"knowledge base name already exists: {request.kb_name}"
                )
            created = self.knowledge_base_repository.create_knowledge_base(
                cursor,
                kb_name=request.kb_name,
                kb_description=request.kb_description,
            )
            if created is None:
                raise KnowledgeBaseValidationError("failed to create knowledge base")
            logger.info(
                "knowledge_base_service persistence finished: knowledge_base_id=%s",
                self._row_id(created),
            )
            connection.commit()
            logger.info(
                "knowledge_base_service transaction committed: knowledge_base_id=%s",
                self._row_id(created),
            )
            return CreateKnowledgeBaseResponse(
                kb_code=str(self._row_id(created)),
                kb_name=request.kb_name,
                kb_description=request.kb_description,
            )
        except Exception:
            connection.rollback()
            logger.warning(
                "knowledge_base_service transaction rolled back: kb_name=%s",
                request.kb_name,
            )
            raise
        finally:
            connection.close()

    def delete_knowledge_base(
        self, request: DeleteKnowledgeBaseRequest
    ) -> DeleteKnowledgeBaseResponse:
        """Logically delete a knowledge base and its descendant documents."""
        logger.info(
            "knowledge_base_service.delete_knowledge_base started: kb_code=%s",
            request.kb_code,
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
            self.knowledge_base_repository.soft_delete_by_code(
                cursor, kb_code=request.kb_code
            )
            self.knowledge_fs_entry_repository.soft_delete_by_knowledge_base_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
            )
            cursor.execute(
                """
                DELETE FROM knowledge_chunk_retrieval_mv
                WHERE knowledge_base_id = %(knowledge_base_id)s
                """,
                {"knowledge_base_id": knowledge_base_id},
            )
            connection.commit()
            return DeleteKnowledgeBaseResponse(kb_code=request.kb_code, is_deleted=True)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_knowledge_base(
        self, request: UpdateKnowledgeBaseRequest
    ) -> UpdateKnowledgeBaseResponse:
        """Update mutable business fields for one knowledge base."""
        logger.info(
            "knowledge_base_service.update_knowledge_base started: kb_code=%s, has_kb_name=%s, has_description=%s",
            request.kb_code,
            "kb_name" in request.model_fields_set,
            "kb_description" in request.model_fields_set,
        )
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = self.knowledge_base_repository.get_by_code(cursor, request.kb_code)
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            updates: dict[str, Any] = {}
            if "kb_name" in request.model_fields_set:
                if request.kb_name is not None:
                    existing = self.knowledge_base_repository.get_by_name(
                        cursor,
                        request.kb_name,
                    )
                    if existing is not None and self._row_id(existing) != self._row_id(
                        kb_row
                    ):
                        raise KnowledgeBaseValidationError(
                            f"knowledge base name already exists: {request.kb_name}"
                        )
                updates["kb_name"] = request.kb_name
            if "kb_description" in request.model_fields_set:
                updates["kb_description"] = request.kb_description

            self.knowledge_base_repository.update_knowledge_base(
                cursor,
                kb_code=request.kb_code,
                updates=updates,
            )

            connection.commit()
            next_kb_name = (
                updates["kb_name"] if "kb_name" in updates else kb_row.get("kb_name")
            )
            next_description = (
                updates["kb_description"]
                if "kb_description" in updates
                else kb_row.get("kb_description")
            )
            return UpdateKnowledgeBaseResponse(
                kb_code=request.kb_code,
                kb_name=next_kb_name,
                kb_description=next_description,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_directory(
        self, request: CreateDirectoryRequest
    ) -> CreateDirectoryResponse:
        """Create one explicit directory under an existing parent directory."""
        logger.info(
            "knowledge_base_service.create_directory started: kb_code=%s, directory_path=%s",
            request.kb_code,
            request.directory_path,
        )
        normalized_directory_path = request.directory_path.strip("/")
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = self.knowledge_base_repository.get_by_code(cursor, request.kb_code)
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)

            try:
                self.knowledge_fs_entry_repository.create_directory_entry(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    full_path=normalized_directory_path,
                    directory_description=request.directory_description,
                )
            except ValueError as exc:
                raise KnowledgeBaseValidationError(str(exc)) from exc
            connection.commit()
            return CreateDirectoryResponse(
                kb_code=request.kb_code,
                directory_path=self._ensure_leading_slash(normalized_directory_path),
                directory_description=request.directory_description,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_directory(
        self, request: DeleteDirectoryRequest
    ) -> DeleteDirectoryResponse:
        """Logically delete one directory subtree and its retrieval projection rows."""
        logger.info(
            "knowledge_base_service.delete_directory started: kb_code=%s, directory_code=%s",
            request.kb_code,
            request.directory_code,
        )
        if (
            self.knowledge_item_repository is None
            or self.retrieval_projection_repository is None
        ):
            raise KnowledgeBaseValidationError(
                "delete directory runtime is not configured"
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
                item_code=request.directory_code,
            )
            if item_row is None or item_row.get("item_kind") not in (None, "DIRECTORY"):
                raise KnowledgeBaseValidationError(
                    f"directory not found: {request.directory_code}"
                )
            root_fs_entry_id = int(item_row["fs_entry_id"])
            fs_entry_ids = self.knowledge_fs_entry_repository.list_subtree_entry_ids(
                cursor,
                knowledge_base_id=knowledge_base_id,
                root_fs_entry_id=root_fs_entry_id,
            )
            self.knowledge_fs_entry_repository.soft_delete_subtree(
                cursor,
                knowledge_base_id=knowledge_base_id,
                root_fs_entry_id=root_fs_entry_id,
            )
            self.knowledge_item_repository.soft_delete_by_fs_entry_ids(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_ids=fs_entry_ids,
            )
            self.retrieval_projection_repository.delete_for_fs_entry_ids(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_ids=fs_entry_ids,
            )
            connection.commit()
            return DeleteDirectoryResponse(
                kb_code=request.kb_code,
                directory_code=request.directory_code,
                is_deleted=True,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_directory(
        self, request: UpdateDirectoryRequest
    ) -> UpdateDirectoryResponse:
        """Rename one directory by its knowledge-base-relative path."""
        logger.info(
            "knowledge_base_service.update_directory started: kb_code=%s, directory_path=%s, directory_name=%s",
            request.kb_code,
            request.directory_path,
            request.directory_name,
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
            normalized_directory_path = request.directory_path.strip("/")
            fs_entry_row = self.knowledge_fs_entry_repository.get_directory_by_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=normalized_directory_path,
            )
            if fs_entry_row is None or fs_entry_row.get("entry_type") != "DIRECTORY":
                raise KnowledgeBaseValidationError(
                    f"directory not found: {request.directory_path}"
                )
            fs_entry_id = int(fs_entry_row["kid"])

            sibling = self.knowledge_fs_entry_repository.get_child_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=fs_entry_row.get("parent_entry_id"),
                name=request.directory_name,
            )
            if sibling is not None and int(sibling["kid"]) != fs_entry_id:
                raise KnowledgeBaseValidationError(
                    f"directory name already exists under parent: {request.directory_name}"
                )
            self.knowledge_fs_entry_repository.rename_entry(
                cursor,
                entry_id=fs_entry_id,
                new_name=request.directory_name,
            )

            directory_path = (
                self.knowledge_fs_entry_repository.get_virtual_path_by_entry_id(
                    cursor,
                    entry_id=fs_entry_id,
                )
            )
            connection.commit()
            return UpdateDirectoryResponse(
                kb_code=request.kb_code,
                directory_path=self._ensure_leading_slash(str(directory_path or "")),
                directory_name=request.directory_name,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_file(self, request: UpdateFileRequest) -> UpdateFileResponse:
        """Update one file's display name and metadata without moving or rewriting it."""
        logger.info(
            "knowledge_base_service.update_file started: kb_code=%s, file_code=%s, has_name=%s, has_description=%s, has_metadata=%s",
            request.kb_code,
            request.file_code,
            "file_name" in request.model_fields_set,
            "file_description" in request.model_fields_set,
            "metadata" in request.model_fields_set,
        )
        if self.knowledge_item_repository is None:
            raise KnowledgeBaseValidationError("update file runtime is not configured")

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
            if item_row is None or item_row.get("item_kind") != "FILE":
                raise KnowledgeBaseValidationError(
                    f"knowledge item not found: {request.file_code}"
                )

            fs_entry_id = int(item_row["fs_entry_id"])
            fs_entry_row = self.knowledge_fs_entry_repository.get_entry_by_id(
                cursor,
                entry_id=fs_entry_id,
            )
            if fs_entry_row is None or fs_entry_row.get("entry_type") != "FILE":
                raise KnowledgeBaseValidationError(
                    f"knowledge item not found: {request.file_code}"
                )

            if (
                "file_name" in request.model_fields_set
                and request.file_name is not None
            ):
                sibling = self.knowledge_fs_entry_repository.get_child_entry(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    parent_entry_id=int(fs_entry_row["parent_entry_id"]),
                    name=request.file_name,
                )
                if sibling is not None and int(sibling["kid"]) != fs_entry_id:
                    raise KnowledgeBaseValidationError(
                        f"file name already exists under parent: {request.file_name}"
                    )
                self.knowledge_fs_entry_repository.rename_entry(
                    cursor,
                    entry_id=fs_entry_id,
                    new_name=request.file_name,
                )

            updates: dict[str, Any] = {}
            if "file_description" in request.model_fields_set:
                updates["description"] = request.file_description
            if "metadata" in request.model_fields_set:
                updates["metadata"] = request.metadata
            self.knowledge_item_repository.update_knowledge_item(
                cursor,
                knowledge_base_id=knowledge_base_id,
                item_code=request.file_code,
                updates=updates,
            )

            file_path = self.knowledge_fs_entry_repository.get_virtual_path_by_entry_id(
                cursor,
                entry_id=fs_entry_id,
            )
            connection.commit()
            return UpdateFileResponse(
                kb_code=request.kb_code,
                file_code=request.file_code,
                file_path=self._ensure_leading_slash(str(file_path or "")),
                file_description=(
                    request.file_description
                    if "file_description" in request.model_fields_set
                    else item_row.get("description")
                ),
                metadata=(
                    request.metadata
                    if "metadata" in request.model_fields_set
                    else item_row.get("metadata")
                ),
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_dir(
        self, request: KnowledgeItemListDirRequest
    ) -> KnowledgeItemListDirResponse:
        """List entries from the virtual filesystem."""
        logger.info(
            "knowledge_base_service.list_dir started: path=%s",
            request.path,
        )
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            if not request.kb_codes:
                return KnowledgeItemListDirResponse(items=[])
            normalized_path = request.path.strip()
            if normalized_path in ("", "/"):
                items = [
                    self._normalize_output_item(item)
                    for item in self.knowledge_fs_entry_repository.list_root_entries(
                        cursor,
                        kb_codes=request.kb_codes,
                    )
                ]
            else:
                items = self._list_directory_entries(
                    cursor,
                    normalized_path,
                    kb_codes=request.kb_codes,
                )
            logger.info(
                "knowledge_base_service.list_dir finished: path=%s, item_count=%s",
                request.path,
                len(items),
            )
            return KnowledgeItemListDirResponse(items=items)
        finally:
            connection.close()

    def glob(self, request: KnowledgeItemGlobRequest) -> KnowledgeItemListDirResponse:
        """Match filesystem entries via layered glob/regex-style segments."""
        logger.info("knowledge_base_service.glob started: path=%s", request.path)
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            items = self._list_by_path_pattern(
                cursor,
                request.path.strip("/"),
                list_directory_contents=request.path.strip().endswith("/"),
                kb_codes=request.kb_codes,
            )
            logger.info(
                "knowledge_base_service.glob finished: path=%s, item_count=%s",
                request.path,
                len(items),
            )
            return KnowledgeItemListDirResponse(items=items)
        finally:
            connection.close()

    def fetch(self, request: KnowledgeItemFetchRequest) -> KnowledgeItemFetchResponse:
        """Fetch original or markdown content for the current version of a file."""
        logger.info(
            "knowledge_base_service.fetch started: path=%s, content_type=%s, start_line=%s, end_line=%s",
            request.path,
            request.content_type,
            request.start_line,
            request.end_line,
        )
        if request.content_type == "markdown" and request.start_line is not None:
            if request.start_line < 1:
                raise KnowledgeBaseValidationError("start_line must be greater than 0")
            if request.end_line is None or request.end_line < request.start_line:
                raise KnowledgeBaseValidationError(
                    "end_line must be greater than or equal to start_line"
                )
        if (
            self.object_storage is None
            or self.cache_root is None
            or self.knowledge_fetch_cache_repository is None
        ):
            raise KnowledgeBaseValidationError("fetch runtime is not configured")

        normalized_virtual_path = self._normalize_virtual_path(request.path)
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            file_node = self._resolve_virtual_path(
                cursor,
                normalized_virtual_path,
                kb_codes=request.kb_codes,
            )
            if file_node is None or file_node["type"] != "file":
                raise KnowledgeBaseValidationError(f"file not found: {request.path}")
            version_row = (
                self.knowledge_fs_entry_repository.get_current_file_version_by_entry_id(
                    cursor,
                    fs_entry_id=int(file_node["kid"]),
                )
            )
            if version_row is None:
                raise KnowledgeBaseValidationError(
                    f"current version not found: {request.path}"
                )
        finally:
            connection.close()

        if request.content_type == "original":
            access_url = self.object_storage.build_access_url(
                str(version_row["object_key"]),
                expires=timedelta(hours=1),
                bucket_name=str(version_row["bucket_name"]),
            )
            logger.info(
                "knowledge_base_service.fetch finished: path=%s, mode=original_url",
                request.path,
            )
            return KnowledgeItemFetchResponse(
                kb_code=str(version_row["kb_code"]),
                path=self._ensure_leading_slash(normalized_virtual_path),
                content_type="original",
                url=access_url,
            )

        markdown_object_key = version_row.get("markdown_object_key")
        markdown_bucket_name = version_row.get("markdown_bucket_name")
        markdown_checksum = version_row.get("markdown_checksum")
        markdown_file_size = version_row.get("markdown_file_size")

        if not markdown_object_key:
            access_url = self.object_storage.build_access_url(
                str(version_row["object_key"]),
                expires=timedelta(hours=1),
                bucket_name=str(version_row["bucket_name"]),
            )
            logger.info(
                "knowledge_base_service.fetch finished: path=%s, mode=markdown_fallback_original_url",
                request.path,
            )
            return KnowledgeItemFetchResponse(
                kb_code=str(version_row["kb_code"]),
                path=self._ensure_leading_slash(normalized_virtual_path),
                content_type="original",
                url=access_url,
            )

        cached_file_path = self.cache_root / normalized_virtual_path
        with acquire_cache_file_lock(cached_file_path):
            cache_connection = self.connection_factory()
            try:
                cache_cursor = cache_connection.cursor()
                cache_entry = self.knowledge_fetch_cache_repository.get_by_version_id(
                    cache_cursor,
                    knowledge_item_version_id=int(
                        version_row["knowledge_item_version_id"]
                    ),
                )
                if self._cache_entry_is_usable(
                    cache_entry=cache_entry,
                    cached_file_path=cached_file_path,
                    checksum=markdown_checksum,
                ):
                    logger.info(
                        "knowledge_base_service.fetch cache resolved: path=%s, source=cache, cache_file_path=%s",
                        request.path,
                        cached_file_path,
                    )
                    self.knowledge_fetch_cache_repository.touch_cache_entry(
                        cache_cursor,
                        cache_entry_id=int(cache_entry["kid"]),
                        cache_ttl_seconds=self.cache_ttl_seconds,
                    )
                    cache_connection.commit()
                else:
                    self._clear_cache_entry(cached_file_path)
                    payload = self.object_storage.download_object(
                        str(markdown_object_key),
                        bucket_name=str(markdown_bucket_name),
                    )
                    cached_file_path.parent.mkdir(parents=True, exist_ok=True)
                    cached_file_path.write_bytes(payload)
                    logger.info(
                        "knowledge_base_service.fetch cache resolved: path=%s, source=minio, object_key=%s, cache_file_path=%s",
                        request.path,
                        markdown_object_key,
                        cached_file_path,
                    )
                    self.knowledge_fetch_cache_repository.upsert_cache_entry(
                        cache_cursor,
                        knowledge_base_id=int(version_row["knowledge_base_id"]),
                        fs_entry_id=int(file_node["kid"]),
                        knowledge_item_id=int(version_row["knowledge_item_id"]),
                        knowledge_item_version_id=int(
                            version_row["knowledge_item_version_id"]
                        ),
                        kb_code=str(version_row["kb_code"]),
                        full_path=str(version_row["full_path"]),
                        virtual_path=normalized_virtual_path,
                        bucket_name=str(markdown_bucket_name),
                        object_key=str(markdown_object_key),
                        checksum=markdown_checksum,
                        cache_file_path=str(cached_file_path),
                        file_size=markdown_file_size,
                        cache_ttl_seconds=self.cache_ttl_seconds,
                    )
                    cache_connection.commit()
            except Exception:
                cache_connection.rollback()
                raise
            finally:
                cache_connection.close()
            if request.start_line is None:
                selected_text = cached_file_path.read_text(encoding="utf-8")
                reached_eof = True
                start_line = None
                end_line = None
            else:
                selected_text, reached_eof = self._read_line_window(
                    cached_file_path,
                    start_line=request.start_line,
                    end_line=request.end_line or request.start_line,
                )
                start_line = request.start_line
                end_line = request.end_line
        logger.info(
            "knowledge_base_service.fetch finished: path=%s, returned_line_count=%s",
            request.path,
            selected_text.count("\n") if selected_text else 0,
        )
        return KnowledgeItemFetchResponse(
            kb_code=str(version_row["kb_code"]),
            path=self._ensure_leading_slash(normalized_virtual_path),
            content_type="markdown",
            start_line=start_line,
            end_line=end_line,
            data=selected_text,
            reached_eof=reached_eof,
        )

    def download_file(self, request: KnowledgeItemDownloadRequest) -> dict[str, Any]:
        """Download the current original file bytes for a virtual path."""
        logger.info(
            "knowledge_base_service.download_file started: path=%s",
            request.path,
        )
        if self.object_storage is None:
            raise KnowledgeBaseValidationError("download runtime is not configured")

        normalized_virtual_path = self._normalize_virtual_path(request.path)
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            file_node = self._resolve_virtual_path(
                cursor,
                normalized_virtual_path,
                kb_codes=request.kb_codes,
            )
            if file_node is None or file_node["type"] != "file":
                raise KnowledgeBaseValidationError(f"file not found: {request.path}")
            version_row = (
                self.knowledge_fs_entry_repository.get_current_file_version_by_entry_id(
                    cursor,
                    fs_entry_id=int(file_node["kid"]),
                )
            )
            if version_row is None:
                raise KnowledgeBaseValidationError(
                    f"current version not found: {request.path}"
                )
        finally:
            connection.close()

        payload = self.object_storage.download_object(
            str(version_row["object_key"]),
            bucket_name=str(version_row["bucket_name"]),
        )
        filename = PurePosixPath(normalized_virtual_path).name or "download"
        media_type = self._guess_media_type(filename)
        logger.info(
            "knowledge_base_service.download_file finished: path=%s, filename=%s, returned_bytes=%s",
            request.path,
            filename,
            len(payload),
        )
        return {
            "filename": filename,
            "media_type": media_type,
            "content": payload,
        }

    def _guess_media_type(self, filename: str) -> str:
        suffix = PurePosixPath(filename).suffix.lower()
        if suffix in {".md", ".markdown"}:
            return "text/markdown"
        return mimetypes.guess_type(filename)[0] or "application/octet-stream"

    def _row_id(self, row: dict[str, Any]) -> int:
        if "kid" in row:
            return int(row["kid"])
        return int(row["id"])

    def _path_to_regex(self, path: str) -> str:
        if any(
            token in path for token in ("\\", "^", "$", "+", "(", ")", "{", "}", "|")
        ):
            return path
        return fnmatch.translate(path)

    def _list_by_path_pattern(
        self,
        cursor: Any,
        path: str,
        *,
        list_directory_contents: bool,
        kb_codes: list[str],
    ) -> list[dict[str, Any]]:
        pattern_segments = [
            segment for segment in path.strip("/").split("/") if segment
        ]
        root_nodes = [
            self._with_virtual_full_path(node, str(node["name"]))
            for node in self.knowledge_fs_entry_repository.list_root_nodes(
                cursor,
                kb_codes=kb_codes,
            )
        ]
        return self._match_pattern_segments(
            root_nodes,
            pattern_segments,
            cursor=cursor,
            list_directory_contents=list_directory_contents,
        )

    def _list_directory_entries(
        self, cursor: Any, path: str, *, kb_codes: list[str]
    ) -> list[dict[str, Any]]:
        normalized_virtual_path = self._normalize_virtual_path(path)
        node = self._resolve_virtual_path(
            cursor, normalized_virtual_path, kb_codes=kb_codes
        )
        if node is None:
            raise KnowledgeBaseValidationError(f"directory not found: {path}")
        if node["type"] == "directory":
            return self._expand_directory_contents(cursor, [node])
        return [self._project_node(node)]

    def _match_pattern_segments(
        self,
        nodes: list[dict[str, Any]],
        pattern_segments: list[str],
        *,
        cursor: Any,
        list_directory_contents: bool,
    ) -> list[dict[str, Any]]:
        if not pattern_segments:
            if list_directory_contents:
                return self._expand_directory_contents(cursor, nodes)
            return [self._project_node(node) for node in nodes]

        current_nodes = nodes
        for index, segment in enumerate(pattern_segments):
            is_last_segment = index == len(pattern_segments) - 1
            matched_nodes = [
                node
                for node in current_nodes
                if self._segment_matches_pattern(node["name"], segment)
            ]
            if is_last_segment:
                if list_directory_contents:
                    return self._expand_directory_contents(cursor, matched_nodes)
                if not self._segment_has_pattern(segment) and self._all_directories(
                    matched_nodes
                ):
                    return self._expand_directory_contents(cursor, matched_nodes)
                return [self._project_node(node) for node in matched_nodes]

            next_nodes: list[dict[str, Any]] = []
            for node in matched_nodes:
                if node["type"] != "directory":
                    continue
                child_nodes = self.knowledge_fs_entry_repository.list_child_nodes(
                    cursor,
                    parent_path_ltree=str(node["path_ltree"]),
                )
                next_nodes.extend(
                    self._with_virtual_full_path(
                        child_node,
                        f"{node['virtual_full_path']}/{child_node['name']}",
                    )
                    for child_node in child_nodes
                )
            current_nodes = next_nodes
            if not current_nodes:
                return []
        return []

    def _project_node(self, node: dict[str, Any]) -> dict[str, Any]:
        return {
            "kb_code": str(node["kb_code"]),
            "name": self._ensure_leading_slash(
                str(node.get("virtual_full_path", node.get("full_path", node["name"])))
            ),
            "type": str(node["type"]),
            "size": int(node["size"]),
        }

    def _normalize_output_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            **item,
            "name": self._ensure_leading_slash(str(item["name"])),
        }

    def _ensure_leading_slash(self, path: str) -> str:
        return path if path.startswith("/") else f"/{path}"

    def _segment_matches_pattern(self, name: str, pattern: str) -> bool:
        if any(
            token in pattern for token in ("\\", "^", "$", "+", "(", ")", "{", "}", "|")
        ):
            return re.fullmatch(pattern, name) is not None
        return fnmatch.fnmatchcase(name, pattern)

    def _segment_has_pattern(self, segment: str) -> bool:
        return re.search(r"[\*\?\[\]\(\)\{\}\+\|\^\$\\]", segment) is not None

    def _expand_directory_contents(
        self, cursor: Any, nodes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        projected_items: list[dict[str, Any]] = []
        for node in nodes:
            if node["type"] != "directory":
                projected_items.append(self._project_node(node))
                continue
            child_nodes = self.knowledge_fs_entry_repository.list_child_nodes(
                cursor,
                parent_path_ltree=str(node["path_ltree"]),
            )
            projected_items.extend(
                self._project_node(
                    self._with_virtual_full_path(
                        child_node,
                        f"{node['virtual_full_path']}/{child_node['name']}",
                    )
                )
                for child_node in child_nodes
            )
        return projected_items

    def _resolve_virtual_path(
        self,
        cursor: Any,
        path: str,
        *,
        kb_codes: list[str],
    ) -> dict[str, Any] | None:
        path_segments = [segment for segment in path.split("/") if segment]
        if not path_segments:
            return None
        current_nodes = [
            self._with_virtual_full_path(node, str(node["name"]))
            for node in self.knowledge_fs_entry_repository.list_root_nodes(
                cursor,
                kb_codes=kb_codes,
            )
        ]
        for index, segment in enumerate(path_segments):
            matched_nodes = [node for node in current_nodes if node["name"] == segment]
            if not matched_nodes:
                return None
            is_last_segment = index == len(path_segments) - 1
            if is_last_segment:
                return matched_nodes[0]
            next_nodes: list[dict[str, Any]] = []
            for node in matched_nodes:
                if node["type"] != "directory":
                    continue
                child_nodes = self.knowledge_fs_entry_repository.list_child_nodes(
                    cursor,
                    parent_path_ltree=str(node["path_ltree"]),
                )
                next_nodes.extend(
                    self._with_virtual_full_path(
                        child_node,
                        f"{node['virtual_full_path']}/{child_node['name']}",
                    )
                    for child_node in child_nodes
                )
            current_nodes = next_nodes
        return None

    def _with_virtual_full_path(
        self, node: dict[str, Any], virtual_full_path: str
    ) -> dict[str, Any]:
        return {**node, "virtual_full_path": virtual_full_path}

    def _all_directories(self, nodes: list[dict[str, Any]]) -> bool:
        return bool(nodes) and all(node["type"] == "directory" for node in nodes)

    def _normalize_virtual_path(self, path: str) -> str:
        normalized = path.strip().strip("/")
        if not normalized:
            raise KnowledgeBaseValidationError("path must not be empty")
        posix_path = PurePosixPath(normalized)
        if any(part in ("", ".", "..") for part in posix_path.parts):
            raise KnowledgeBaseValidationError("path contains invalid segments")
        return str(posix_path)

    def _cache_entry_is_usable(
        self,
        *,
        cache_entry: dict[str, Any] | None,
        cached_file_path: Path,
        checksum: Any,
    ) -> bool:
        if cache_entry is None:
            return False
        if cache_entry.get("cache_status") != "READY":
            return False
        if cache_entry.get("checksum") != checksum:
            return False
        expires_at = cache_entry.get("expires_at")
        if expires_at is None:
            return False
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return cached_file_path.exists() and expires_at > datetime.now(timezone.utc)

    def _clear_cache_entry(self, cached_file_path: Path) -> None:
        if cached_file_path.exists():
            cached_file_path.unlink()

    def _read_line_window(
        self, cached_file_path: Path, *, start_line: int, end_line: int
    ) -> tuple[str, bool]:
        with cached_file_path.open("r", encoding="utf-8") as file_handle:
            selected_lines = list(islice(file_handle, start_line - 1, end_line))
            next_line = next(file_handle, None)
        return "".join(selected_lines), next_line is None
