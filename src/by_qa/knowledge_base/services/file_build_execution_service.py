"""Lease-fenced execution of one durable File Build task."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from by_qa.knowledge_base.infrastructure.storage import StorageLocation
from by_qa.knowledge_common.exceptions import UnsupportedFileTypeError


class FileBuildLeaseLostError(RuntimeError):
    """The current worker no longer owns the task and must stop writing."""


class FileBuildTerminalError(RuntimeError):
    """A deterministic terminal outcome discovered during execution."""

    def __init__(
        self,
        *,
        status: str,
        error_code: str,
        message: str,
        failure_kind: str | None = None,
        outcome_uncertain: bool = False,
    ):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.failure_kind = failure_kind
        self.outcome_uncertain = outcome_uncertain


@dataclass(slots=True)
class FileBuildExecutionService:
    """Clear stale derivatives, recompute, and commit with optimistic CAS."""

    connection_factory: Any
    task_repository: Any
    batch_repository: Any
    fs_entry_repository: Any
    chunk_repository: Any
    retrieval_repository: Any
    fetch_cache_repository: Any | None
    storage_provider: Any
    document_chunking_service: Any
    embedding_dimension: int

    async def execute_claimed(self, task: dict[str, Any]) -> dict[str, Any]:
        return await self._execute(task, lease_token=str(task["lease_token"]))

    async def execute_inline(self, task: dict[str, Any]) -> dict[str, Any]:
        """Execute one Entity-owned task synchronously without a worker lease."""
        return await self._execute(task, lease_token=None)

    async def _execute(
        self, task: dict[str, Any], *, lease_token: str | None
    ) -> dict[str, Any]:
        task_id = int(task["kid"])
        file_row = await self._prepare(task_id=task_id, lease_token=lease_token)
        current_path = str(file_row["virtual_path"])
        original_location = StorageLocation(
            namespace=str(file_row.get("file_bucket_name") or ""),
            key=str(file_row.get("file_object_key") or ""),
        )
        if not original_location.namespace or not original_location.key:
            raise FileBuildTerminalError(
                status="skipped",
                error_code="SOURCE_NOT_READY",
                message="Source object location is unavailable",
            )
        await self._stage(task_id, lease_token, "extracting")
        file_bytes = await self.storage_provider.read(original_location)
        file_type = self._derive_file_type(file_row, current_path)
        try:
            markdown_content = await asyncio.to_thread(
                self.document_chunking_service.extract_text_from_file,
                file_bytes,
                file_type,
            )
        except UnsupportedFileTypeError as exc:
            raise FileBuildTerminalError(
                status="unsupported",
                error_code="UNSUPPORTED_FILE_TYPE",
                message=str(exc) or "unsupported file type",
            ) from exc

        await self._stage(task_id, lease_token, "chunking")
        markdown_bytes = markdown_content.encode("utf-8")
        original_name = str(file_row.get("name") or PurePosixPath(current_path).name)
        chunk_filename = PurePosixPath(original_name).stem + ".md"
        chunks = await asyncio.to_thread(
            self.document_chunking_service.chunk_and_embed,
            markdown_bytes,
            filename=chunk_filename,
        )
        await self._stage(task_id, lease_token, "embedding")
        self._validate_embedding_dimensions(chunks)
        return await self._commit_success(
            task=task,
            lease_token=lease_token,
            markdown_content=markdown_content,
            markdown_bytes=markdown_bytes,
            chunks=chunks,
        )

    async def finish_inline(
        self,
        task: dict[str, Any],
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        failure_kind: str | None = None,
        outcome_uncertain: bool = False,
    ) -> dict[str, Any] | None:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            finished = await self.task_repository.finish_inline_task(
                cursor,
                task_id=int(task["kid"]),
                status=status,
                error_code=error_code,
                error_message=error_message,
                failure_kind=failure_kind,
                outcome_uncertain=outcome_uncertain,
            )
            if finished is None:
                await connection.rollback()
                return None
            await connection.commit()
            return finished
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def finish_claimed(
        self,
        task: dict[str, Any],
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        failure_kind: str | None = None,
        outcome_uncertain: bool = False,
    ) -> dict[str, Any] | None:
        """Persist a non-success terminal result and advance its batch."""
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            finished = await self.task_repository.finish_claimed_task(
                cursor,
                task_id=int(task["kid"]),
                lease_token=str(task["lease_token"]),
                status=status,
                error_code=error_code,
                error_message=error_message,
                failure_kind=failure_kind,
                outcome_uncertain=outcome_uncertain,
            )
            if finished is None:
                await connection.rollback()
                return None
            await self._advance_batch(cursor, finished)
            await connection.commit()
            return finished
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def reap_one_expired(self) -> dict[str, Any] | None:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            expired = await self.task_repository.lock_next_expired_task(cursor)
            if expired is None:
                await connection.rollback()
                return None
            finished = await self.task_repository.fail_locked_expired_task(
                cursor, task_id=int(expired["kid"])
            )
            if finished is None:
                await connection.rollback()
                return None
            await self._advance_batch(cursor, finished)
            await connection.commit()
            return finished
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def _prepare(
        self, *, task_id: int, lease_token: str | None
    ) -> dict[str, Any]:
        connection = await self.connection_factory()
        markdown_location = None
        try:
            cursor = connection.cursor()
            task, file_row = await self._lock_input(
                cursor, task_id=task_id, lease_token=lease_token
            )
            self._validate_input(task, file_row)
            knowledge_base_id = int(task["knowledge_base_id"])
            fs_entry_id = int(task["fs_entry_id"])
            current_path = str(file_row["virtual_path"])
            markdown_location = self.storage_provider.build_markdown_location(
                kb_code=str(knowledge_base_id),
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
                file_path=current_path,
            )
            await self.retrieval_repository.delete_for_fs_entry_ids(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_ids=[fs_entry_id],
            )
            await self.chunk_repository.delete_for_fs_entry(
                cursor, fs_entry_id=fs_entry_id
            )
            await self.fs_entry_repository.clear_markdown_metadata(
                cursor, fs_entry_id=fs_entry_id
            )
            if self.fetch_cache_repository is not None:
                await self.fetch_cache_repository.delete_cache_entries_for_fs_entry_ids(
                    cursor, fs_entry_ids=[fs_entry_id]
                )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()
        if markdown_location is not None:
            await self.storage_provider.delete_quietly(markdown_location)
        return file_row

    async def _stage(
        self, task_id: int, lease_token: str | None, current_stage: str
    ) -> None:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            if lease_token is None:
                updated = await self.task_repository.update_inline_stage(
                    cursor, task_id=task_id, current_stage=current_stage
                )
            else:
                updated = await self.task_repository.update_claimed_stage(
                    cursor,
                    task_id=task_id,
                    lease_token=lease_token,
                    current_stage=current_stage,
                )
            if updated is None:
                await connection.rollback()
                raise FileBuildLeaseLostError(
                    f"lease lost before stage {current_stage}"
                )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def _commit_success(
        self,
        *,
        task: dict[str, Any],
        lease_token: str | None,
        markdown_content: str,
        markdown_bytes: bytes,
        chunks: list[Any],
    ) -> dict[str, Any]:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            current_task, file_row = await self._lock_input(
                cursor, task_id=int(task["kid"]), lease_token=lease_token
            )
            self._validate_input(current_task, file_row)
            if lease_token is None:
                staged = await self.task_repository.update_inline_stage(
                    cursor, task_id=int(task["kid"]), current_stage="committing"
                )
            else:
                staged = await self.task_repository.update_claimed_stage(
                    cursor,
                    task_id=int(task["kid"]),
                    lease_token=lease_token,
                    current_stage="committing",
                )
            if staged is None:
                raise FileBuildLeaseLostError("lease lost before commit")

            knowledge_base_id = int(task["knowledge_base_id"])
            fs_entry_id = int(task["fs_entry_id"])
            current_path = str(file_row["virtual_path"])
            markdown_location = self.storage_provider.build_markdown_location(
                kb_code=str(knowledge_base_id),
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
                file_path=current_path,
            )
            stored_markdown = await self.storage_provider.write(
                markdown_location,
                markdown_bytes,
                content_type="text/markdown; charset=utf-8",
            )
            chunk_rows = await self.chunk_repository.replace_for_fs_entry(
                cursor,
                fs_entry_id=fs_entry_id,
                chunks=[chunk.model_dump() for chunk in chunks],
            )
            chunk_id_by_no = {row["chunk_no"]: self._row_id(row) for row in chunk_rows}
            await self.chunk_repository.replace_embeddings(
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
            await self.fs_entry_repository.update_markdown_metadata(
                cursor,
                fs_entry_id=fs_entry_id,
                markdown_location=stored_markdown.location,
                line_count=line_count,
            )
            await self.retrieval_repository.refresh_for_fs_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
                full_path=current_path.strip("/"),
            )
            result_payload = {
                "lineCount": line_count,
                "chunkCount": len(chunks),
            }
            if lease_token is None:
                finished = await self.task_repository.finish_inline_task(
                    cursor,
                    task_id=int(task["kid"]),
                    status="succeeded",
                    result_payload=result_payload,
                )
            else:
                finished = await self.task_repository.finish_claimed_task(
                    cursor,
                    task_id=int(task["kid"]),
                    lease_token=lease_token,
                    status="succeeded",
                    result_payload=result_payload,
                )
            if finished is None:
                raise FileBuildLeaseLostError("lease lost during commit")
            await self._advance_batch(cursor, finished)
            await connection.commit()
            return finished
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def _lock_input(
        self, cursor: Any, *, task_id: int, lease_token: str | None
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if lease_token is None:
            await cursor.execute(
                """
                SELECT *
                FROM knowledge_build_task
                WHERE kid = %(task_id)s
                  AND status = 'running'
                  AND execution_mode = 'INLINE'
                FOR UPDATE
                """,
                {"task_id": task_id},
            )
        else:
            await cursor.execute(
                """
            SELECT *
            FROM knowledge_build_task
            WHERE kid = %(task_id)s
              AND status = 'running'
              AND execution_mode = 'BACKGROUND'
              AND lease_token = %(lease_token)s
              AND lease_expires_at > clock_timestamp()
            FOR UPDATE
            """,
                {"task_id": task_id, "lease_token": lease_token},
            )
        task = await cursor.fetchone()
        if task is None:
            raise FileBuildLeaseLostError("file build lease is no longer valid")
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_fs_entry
            WHERE kid = %(fs_entry_id)s
            FOR UPDATE
            """,
            {"fs_entry_id": task["fs_entry_id"]},
        )
        return task, await cursor.fetchone()

    @staticmethod
    def _validate_input(task: dict[str, Any], file_row: dict[str, Any] | None) -> None:
        if file_row is None or bool(file_row.get("is_deleted")):
            raise FileBuildTerminalError(
                status="skipped",
                error_code="SOURCE_DELETED",
                message="Source file was deleted",
            )
        if str(file_row.get("checksum") or "") != str(task["input_checksum"]):
            raise FileBuildTerminalError(
                status="skipped",
                error_code="INPUT_STALE",
                message="Source checksum changed after task acceptance",
            )

    async def _advance_batch(self, cursor: Any, task: dict[str, Any]) -> None:
        batch_id = task.get("batch_id")
        if batch_id is None:
            return
        batch = await self.batch_repository.advance_batch(
            cursor, batch_id=str(batch_id)
        )
        if batch is None:
            raise RuntimeError(f"failed to advance build batch: {batch_id}")

    def _validate_embedding_dimensions(self, chunks: list[Any]) -> None:
        for chunk in chunks:
            if len(chunk.embedding) != self.embedding_dimension:
                raise ValueError(
                    "embedding dimension mismatch: "
                    f"expected {self.embedding_dimension}, got {len(chunk.embedding)}"
                )

    @staticmethod
    def _derive_file_type(file_row: dict[str, Any], file_path: str) -> str:
        suffix = PurePosixPath(file_path).suffix.lower().lstrip(".")
        if suffix:
            return suffix
        mime_type = str(file_row.get("mime_type") or "")
        return mime_type.split("/", maxsplit=1)[-1].lower()

    @staticmethod
    def _row_id(row: dict[str, Any]) -> int:
        return int(row.get("kid", row.get("id")))
