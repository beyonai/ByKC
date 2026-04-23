"""Service for creating and updating knowledge bases."""

import fnmatch
import mimetypes
from dataclasses import dataclass
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
    FileBuildStatusRequest,
    KnowledgeItemDownloadRequest,
    KnowledgeItemGlobRequest,
    KnowledgeItemListDirItem,
    KnowledgeItemListDirRequest,
    KnowledgeItemListDirResponse,
    ReadFileRequest,
    UpdateDirectoryRequest,
    UpdateDirectoryResponse,
    UpdateKnowledgeBaseRequest,
    UpdateKnowledgeBaseResponse,
)
from by_qa.knowledge_base.build_status import STATUS_DICT, STEP_DICT
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError


@dataclass
class KnowledgeBaseService:
    """Create or update knowledge base records."""

    connection_factory: Callable[[], Any]
    knowledge_base_repository: Any
    knowledge_fs_entry_repository: Any
    knowledge_build_task_repository: Any | None = None
    retrieval_projection_repository: Any | None = None
    knowledge_fetch_cache_repository: Any | None = None
    object_storage: Any | None = None
    cache_root: Path | None = None
    cache_ttl_seconds: int = 24 * 60 * 60

    async def create_knowledge_base(
        self, request: CreateKnowledgeBaseRequest
    ) -> CreateKnowledgeBaseResponse:
        """Create a knowledge base."""
        logger.info(
            "knowledge_base_service.create_knowledge_base started: kb_name=%s, has_description=%s",
            request.kb_name,
            request.kb_description is not None,
        )
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            existing = await self.knowledge_base_repository.get_by_name(
                cursor, request.kb_name
            )
            if existing is not None:
                raise KnowledgeBaseValidationError(
                    f"knowledge base name already exists: {request.kb_name}"
                )
            created = await self.knowledge_base_repository.create_knowledge_base(
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
            await connection.commit()
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
            await connection.rollback()
            logger.warning(
                "knowledge_base_service transaction rolled back: kb_name=%s",
                request.kb_name,
            )
            raise
        finally:
            await connection.close()

    async def delete_knowledge_base(
        self, request: DeleteKnowledgeBaseRequest
    ) -> DeleteKnowledgeBaseResponse:
        """Logically delete a knowledge base and its descendant documents."""
        logger.info(
            "knowledge_base_service.delete_knowledge_base started: kb_code=%s",
            request.kb_code,
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
            await self.knowledge_base_repository.soft_delete_by_code(
                cursor, kb_code=request.kb_code
            )
            await self.knowledge_fs_entry_repository.soft_delete_by_knowledge_base_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
            )
            await cursor.execute(
                """
                DELETE FROM knowledge_chunk_retrieval_mv
                WHERE knowledge_base_id = %(knowledge_base_id)s
                """,
                {"knowledge_base_id": knowledge_base_id},
            )
            await connection.commit()
            return DeleteKnowledgeBaseResponse(kb_code=request.kb_code, is_deleted=True)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def update_knowledge_base(
        self, request: UpdateKnowledgeBaseRequest
    ) -> UpdateKnowledgeBaseResponse:
        """Update mutable business fields for one knowledge base."""
        logger.info(
            "knowledge_base_service.update_knowledge_base started: kb_code=%s, has_kb_name=%s, has_description=%s",
            request.kb_code,
            "kb_name" in request.model_fields_set,
            "kb_description" in request.model_fields_set,
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
            updates: dict[str, Any] = {}
            if "kb_name" in request.model_fields_set:
                if request.kb_name is not None:
                    existing = await self.knowledge_base_repository.get_by_name(
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

            await self.knowledge_base_repository.update_knowledge_base(
                cursor,
                kb_code=request.kb_code,
                updates=updates,
            )

            await connection.commit()
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
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def create_directory(
        self, request: CreateDirectoryRequest
    ) -> CreateDirectoryResponse:
        """Create one explicit directory under an existing parent directory."""
        logger.info(
            "knowledge_base_service.create_directory started: kb_code=%s, directory_path=%s",
            request.kb_code,
            request.directory_path,
        )
        normalized_directory_path = request.directory_path.strip("/")
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

            try:
                await self.knowledge_fs_entry_repository.create_directory_entry(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    full_path=normalized_directory_path,
                    directory_description=request.directory_description,
                )
            except ValueError as exc:
                raise KnowledgeBaseValidationError(str(exc)) from exc
            await connection.commit()
            return CreateDirectoryResponse(
                kb_code=request.kb_code,
                directory_path=self._ensure_leading_slash(normalized_directory_path),
                directory_description=request.directory_description,
            )
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def delete_directory(
        self, request: DeleteDirectoryRequest
    ) -> DeleteDirectoryResponse:
        """Logically delete one directory subtree and its retrieval projection rows."""
        logger.info(
            "knowledge_base_service.delete_directory started: kb_code=%s, directory_path=%s",
            request.kb_code,
            request.directory_path,
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
            normalized_directory_path = request.directory_path.strip("/")
            directory_row = (
                await self.knowledge_fs_entry_repository.get_directory_by_path(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    full_path=normalized_directory_path,
                )
            )
            if directory_row is None or directory_row.get("entry_type") != "DIRECTORY":
                raise KnowledgeBaseValidationError(
                    f"directory not found: {request.directory_path}"
                )
            root_fs_entry_id = int(directory_row["kid"])
            fs_entry_ids = (
                await self.knowledge_fs_entry_repository.list_subtree_entry_ids(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    root_fs_entry_id=root_fs_entry_id,
                )
            )
            await self.knowledge_fs_entry_repository.soft_delete_subtree(
                cursor,
                knowledge_base_id=knowledge_base_id,
                root_fs_entry_id=root_fs_entry_id,
            )
            await cursor.execute(
                """
                DELETE FROM knowledge_chunk_retrieval_mv
                WHERE knowledge_base_id = %(knowledge_base_id)s
                  AND fs_entry_id = ANY(%(fs_entry_ids)s)
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "fs_entry_ids": fs_entry_ids,
                },
            )
            await connection.commit()
            return DeleteDirectoryResponse(
                kb_code=request.kb_code,
                directory_path=self._ensure_leading_slash(normalized_directory_path),
                is_deleted=True,
            )
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def update_directory(
        self, request: UpdateDirectoryRequest
    ) -> UpdateDirectoryResponse:
        """Rename one directory by its knowledge-base-relative path."""
        logger.info(
            "knowledge_base_service.update_directory started: kb_code=%s, directory_path=%s, directory_name=%s",
            request.kb_code,
            request.directory_path,
            request.directory_name,
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
            normalized_directory_path = request.directory_path.strip("/")
            fs_entry_row = (
                await self.knowledge_fs_entry_repository.get_directory_by_path(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    full_path=normalized_directory_path,
                )
            )
            if fs_entry_row is None or fs_entry_row.get("entry_type") != "DIRECTORY":
                raise KnowledgeBaseValidationError(
                    f"directory not found: {request.directory_path}"
                )
            fs_entry_id = int(fs_entry_row["kid"])

            sibling = await self.knowledge_fs_entry_repository.get_child_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=fs_entry_row.get("parent_entry_id"),
                name=request.directory_name,
            )
            if sibling is not None and int(sibling["kid"]) != fs_entry_id:
                raise KnowledgeBaseValidationError(
                    f"directory name already exists under parent: {request.directory_name}"
                )
            await self.knowledge_fs_entry_repository.rename_entry(
                cursor,
                entry_id=fs_entry_id,
                new_name=request.directory_name,
            )

            directory_path = (
                await self.knowledge_fs_entry_repository.get_virtual_path_by_entry_id(
                    cursor,
                    entry_id=fs_entry_id,
                )
            )
            await connection.commit()
            return UpdateDirectoryResponse(
                kb_code=request.kb_code,
                directory_path=self._ensure_leading_slash(str(directory_path or "")),
                directory_name=request.directory_name,
            )
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def list_dir(
        self, request: KnowledgeItemListDirRequest
    ) -> KnowledgeItemListDirResponse:
        """List direct children under one knowledge-base-relative directory."""
        logger.info(
            "knowledge_base_service.list_dir started: kb_code=%s, directory_path=%s",
            request.kb_code,
            request.directory_path,
        )
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = await self.knowledge_base_repository.get_by_code(
                cursor,
                request.kb_code,
            )
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)
            normalized_path = request.directory_path.strip()
            if not normalized_path.startswith("/"):
                raise KnowledgeBaseValidationError("directoryPath must start with /")
            if normalized_path == "/":
                parent_entry_id = None
                output_prefix = ""
            else:
                directory_row = (
                    await self.knowledge_fs_entry_repository.get_directory_by_path(
                        cursor,
                        knowledge_base_id=knowledge_base_id,
                        full_path=normalized_path.strip("/"),
                    )
                )
                if directory_row is None:
                    raise KnowledgeBaseValidationError(
                        f"directory not found: {request.directory_path}"
                    )
                parent_entry_id = int(directory_row["kid"])
                output_prefix = normalized_path.rstrip("/")
            child_rows = await self.knowledge_fs_entry_repository.list_children_by_parent_entry_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=parent_entry_id,
            )
            items = [
                KnowledgeItemListDirItem(
                    kb_code=request.kb_code,
                    name=f"{output_prefix}/{row['name']}",
                    type=row["type"],
                    size=int(row.get("size") or 0),
                )
                for row in child_rows
            ]
            logger.info(
                "knowledge_base_service.list_dir finished: directory_path=%s, item_count=%s",
                request.directory_path,
                len(items),
            )
            return KnowledgeItemListDirResponse(items=items)
        finally:
            await connection.close()

    async def file_build_status(
        self, request: FileBuildStatusRequest
    ) -> dict[str, Any]:
        """Return the latest build task snapshot for one file."""
        logger.info(
            "knowledge_base_service.file_build_status started: kb_code=%s, file_path=%s",
            request.kb_code,
            request.file_path,
        )
        if self.knowledge_build_task_repository is None:
            raise KnowledgeBaseValidationError(
                "file build status runtime is not configured"
            )

        normalized_file_path = request.file_path.strip()
        if not normalized_file_path.startswith("/"):
            raise KnowledgeBaseValidationError("filePath must start with /")

        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = await self.knowledge_base_repository.get_by_code(
                cursor,
                request.kb_code,
            )
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)
            file_row = await self.knowledge_fs_entry_repository.get_file_by_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=normalized_file_path.strip("/"),
            )
            if file_row is None:
                raise KnowledgeBaseValidationError(
                    f"file not found: {request.file_path}"
                )
            latest_task = (
                await self.knowledge_build_task_repository.get_latest_by_fs_entry_id(
                    cursor,
                    fs_entry_id=self._row_id(file_row),
                )
            )
            if latest_task is None:
                raise KnowledgeBaseValidationError(
                    f"build task not found: {request.file_path}"
                )
            result = {
                "status": latest_task.get("status"),
                "currentStep": latest_task.get("current_step"),
                "statusDict": STATUS_DICT,
                "stepDict": STEP_DICT,
            }
            logger.info(
                "knowledge_base_service.file_build_status finished: kb_code=%s, file_path=%s, status=%s, current_step=%s",
                request.kb_code,
                request.file_path,
                result["status"],
                result["currentStep"],
            )
            return result
        finally:
            await connection.close()

    async def glob(
        self, request: KnowledgeItemGlobRequest
    ) -> KnowledgeItemListDirResponse:
        """Match filesystem entries via single-level path segments."""
        logger.info(
            "knowledge_base_service.glob started: kb_code=%s, path_rule=%s",
            request.kb_code,
            request.path_rule,
        )
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = await self.knowledge_base_repository.get_by_code(
                cursor,
                request.kb_code,
            )
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)
            normalized_rule = request.path_rule.strip()
            if not normalized_rule:
                raise KnowledgeBaseValidationError("pathRule must not be empty")
            if not normalized_rule.startswith("/"):
                raise KnowledgeBaseValidationError("pathRule must start with /")
            pattern_segments = [
                segment for segment in normalized_rule.strip("/").split("/") if segment
            ]
            if not pattern_segments:
                raise KnowledgeBaseValidationError("pathRule must not be root")
            if any("**" in segment for segment in pattern_segments):
                raise KnowledgeBaseValidationError(
                    "pathRule does not support ** multi-level matching"
                )
            items = await self._glob_relative_path_segments(
                cursor,
                knowledge_base_id=knowledge_base_id,
                kb_code=request.kb_code,
                pattern_segments=pattern_segments,
            )
            logger.info(
                "knowledge_base_service.glob finished: path_rule=%s, item_count=%s",
                request.path_rule,
                len(items),
            )
            return KnowledgeItemListDirResponse(items=items)
        finally:
            await connection.close()

    async def download_file(
        self, request: KnowledgeItemDownloadRequest
    ) -> dict[str, Any]:
        """Download original file bytes for a knowledge-base-relative file path."""
        logger.info(
            "knowledge_base_service.download_file started: kb_code=%s, file_path=%s",
            request.kb_code,
            request.file_path,
        )
        if self.object_storage is None:
            raise KnowledgeBaseValidationError("download runtime is not configured")

        normalized_file_path = request.file_path.strip()
        if not normalized_file_path.startswith("/"):
            raise KnowledgeBaseValidationError("filePath must start with /")
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = await self.knowledge_base_repository.get_by_code(
                cursor,
                request.kb_code,
            )
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)
            file_row = await self.knowledge_fs_entry_repository.get_file_by_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=normalized_file_path.strip("/"),
            )
            if file_row is None or not file_row.get("file_object_key"):
                raise KnowledgeBaseValidationError(
                    f"file not found: {request.file_path}"
                )
        finally:
            await connection.close()

        payload = await self.object_storage.download_object(
            str(file_row["file_object_key"]),
            bucket_name=str(
                file_row.get("file_bucket_name") or self.object_storage.bucket_name
            ),
        )
        filename = PurePosixPath(normalized_file_path).name or "download"
        media_type = str(file_row.get("mime_type") or self._guess_media_type(filename))
        logger.info(
            "knowledge_base_service.download_file finished: file_path=%s, filename=%s, returned_bytes=%s",
            request.file_path,
            filename,
            len(payload),
        )
        return {
            "filename": filename,
            "media_type": media_type,
            "content": payload,
        }

    async def read_file(self, request: ReadFileRequest) -> dict[str, Any]:
        """Read built markdown content for a knowledge-base-relative file path."""
        logger.info(
            "knowledge_base_service.read_file started: kb_code=%s, file_path=%s, start_line=%s, end_line=%s",
            request.kb_code,
            request.file_path,
            request.start_line,
            request.end_line,
        )
        if request.start_line is not None:
            if request.start_line < 1:
                raise KnowledgeBaseValidationError("startLine must be greater than 0")
            if request.end_line is None or request.end_line < request.start_line:
                raise KnowledgeBaseValidationError(
                    "endLine must be greater than or equal to startLine"
                )
        if self.object_storage is None:
            raise KnowledgeBaseValidationError("read file runtime is not configured")

        normalized_file_path = request.file_path.strip()
        if not normalized_file_path.startswith("/"):
            raise KnowledgeBaseValidationError("filePath must start with /")
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = await self.knowledge_base_repository.get_by_code(
                cursor,
                request.kb_code,
            )
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)
            file_row = await self.knowledge_fs_entry_repository.get_file_by_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=normalized_file_path.strip("/"),
            )
            if file_row is None:
                raise KnowledgeBaseValidationError(
                    f"file not found: {request.file_path}"
                )
        finally:
            await connection.close()

        markdown_object_key = file_row.get("markdown_object_key")
        markdown_bucket_name = file_row.get("markdown_bucket_name")
        if not markdown_object_key:
            raise KnowledgeBaseValidationError(f"file not built: {request.file_path}")

        payload = await self.object_storage.download_object(
            str(markdown_object_key),
            bucket_name=str(markdown_bucket_name or self.object_storage.bucket_name),
        )
        markdown_text = payload.decode("utf-8")

        if request.start_line is None:
            data = markdown_text
            reached_eof = True
            start_line = None
            end_line = None
        else:
            lines = markdown_text.splitlines(keepends=True)
            selected = lines[request.start_line - 1 : request.end_line]
            data = "".join(selected)
            reached_eof = request.end_line >= len(lines)
            start_line = request.start_line
            end_line = request.end_line

        logger.info(
            "knowledge_base_service.read_file finished: file_path=%s, returned_line_count=%s",
            request.file_path,
            data.count("\n") if data else 0,
        )
        return {
            "knCode": request.kb_code,
            "filePath": request.file_path,
            "startLine": start_line,
            "endLine": end_line,
            "data": data,
            "reachedEof": reached_eof,
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

    async def _glob_relative_path_segments(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        kb_code: str,
        pattern_segments: list[str],
    ) -> list[KnowledgeItemListDirItem]:
        current_matches: list[tuple[int | None, str, str, int]] = [
            (None, "", "directory", 0)
        ]
        for segment in pattern_segments:
            next_matches: list[tuple[int, str, str, int]] = []
            for match in current_matches:
                parent_entry_id = match[0]
                parent_path = match[1]
                child_rows = await self.knowledge_fs_entry_repository.list_children_by_parent_entry_id(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    parent_entry_id=parent_entry_id,
                )
                for row in child_rows:
                    name = str(row["name"])
                    if not self._segment_matches_path_rule(name, segment):
                        continue
                    child_path = f"{parent_path}/{name}"
                    next_matches.append(
                        (
                            int(row["kid"]),
                            child_path,
                            str(row["type"]),
                            int(row.get("size") or 0),
                        )
                    )
            current_matches = next_matches
            if not current_matches:
                return []
        return [
            KnowledgeItemListDirItem(
                kb_code=kb_code,
                name=matched_path,
                type=item_type,
                size=item_size,
            )
            for row_id, matched_path, item_type, item_size in current_matches
            if row_id is not None
        ]

    def _segment_matches_path_rule(self, name: str, pattern: str) -> bool:
        return fnmatch.fnmatchcase(name, pattern)

    def _ensure_leading_slash(self, path: str) -> str:
        return path if path.startswith("/") else f"/{path}"
