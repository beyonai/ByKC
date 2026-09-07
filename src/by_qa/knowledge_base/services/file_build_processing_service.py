"""Application service for accepting and querying durable File Build work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from by_qa.knowledge_base.api.knowledge_entity_schemas import (
    ProcessingBatchStatusRequest,
    ProcessingTaskStatusRequest,
)
from by_qa.knowledge_base.api.schemas import FileToMarkdownIndexRequest
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.file_build_models import FileBuildProfile


@dataclass(slots=True)
class FileBuildProcessingService:
    """Own the external File Build acceptance and query contracts."""

    connection_factory: Any
    knowledge_base_repository: Any
    knowledge_fs_entry_repository: Any
    acceptance_repository: Any
    batch_repository: Any
    task_repository: Any
    build_profile: FileBuildProfile
    unified_task_repository: Any | None = None
    background_runner: Any | None = None
    terminal_event_service: Any | None = None

    async def start(self) -> None:
        if self.background_runner is not None:
            await self.background_runner.start()

    async def stop(self) -> None:
        if self.background_runner is not None:
            await self.background_runner.stop()

    async def accept(self, request: FileToMarkdownIndexRequest) -> dict[str, Any]:
        target_path = self._normalize_target_path(request.file_path)
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            kb_row = await self.knowledge_base_repository.get_by_code(
                cursor, request.kb_code
            )
            if kb_row is None:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)

            # Freeze path resolution and the file/checksum snapshot for this
            # acceptance transaction. Writers resume immediately after commit.
            await cursor.execute("LOCK TABLE knowledge_fs_entry IN SHARE MODE")

            target = None
            if target_path != "/":
                target = await self.knowledge_fs_entry_repository.get_entry_by_path(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    full_path=target_path,
                )
                if target is None:
                    raise KnowledgeBaseValidationError(
                        f"target path not found: {request.file_path}"
                    )
            entry_type = str(target.get("entry_type")) if target else "DIRECTORY"
            if entry_type not in {"FILE", "DIRECTORY"}:
                raise KnowledgeBaseValidationError(
                    f"target path is not buildable: {request.file_path}"
                )
            scope = "SINGLE_FILE" if entry_type == "FILE" else "DIRECTORY"
            target_fs_entry_id = self._row_id(target) if target else None
            target_path_ltree = str(target.get("path_ltree")) if target else None
            batch_id = f"fb-{uuid4().hex}"
            accepted = await self.acceptance_repository.accept(
                cursor,
                batch_id=batch_id,
                knowledge_base_id=knowledge_base_id,
                scope=scope,
                target_path_snapshot=target_path,
                target_fs_entry_id=target_fs_entry_id,
                target_path_ltree=target_path_ltree,
                build_profile=self.build_profile.storage_value(),
                build_profile_hash=self.build_profile.sha256(),
                force=request.force,
                priority=100 if scope == "SINGLE_FILE" else 0,
            )
            batch = accepted["batch"]
            await connection.commit()
            if self.terminal_event_service is not None:
                completed_batch_ids = [
                    str(row["batch_id"])
                    for row in accepted.get("completed_superseded_batches", [])
                ]
                await self.terminal_event_service.publish_tasks(
                    accepted.get("superseded_tasks", []),
                    completed_batch_ids=completed_batch_ids,
                )
                if str(batch["status"]) == "completed":
                    await self.terminal_event_service.publish_completed_batch(batch_id)
            tasks = [self._acceptance_item(row) for row in accepted["preview"]]
            accepted_count = int(batch["accepted_count"])
            reused_count = int(batch["reused_count"])
            eligible_count = int(batch["eligible_count"])
            return {
                "batchId": str(batch["batch_id"]),
                "scope": str(batch["scope"]),
                "targetPath": str(batch["target_path_snapshot"]),
                "taskType": "FILE_BUILD",
                "candidateCount": int(batch["candidate_count"]),
                "eligibleCount": eligible_count,
                "acceptedCount": accepted_count,
                "reusedCount": reused_count,
                "skippedCount": int(batch["acceptance_skipped_count"]),
                "returnedTaskCount": len(tasks),
                "tasksTruncated": eligible_count > len(tasks),
                "tasks": tasks,
            }
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_processing_task_status(
        self, request: ProcessingTaskStatusRequest
    ) -> dict[str, Any]:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            knowledge_base_id = await self._resolve_kb(cursor, request.kb_code)
            fs_entry_id = await self._resolve_file_filter(
                cursor,
                knowledge_base_id=knowledge_base_id,
                file_id=request.file_id,
                file_path=request.file_path,
            )
            statuses = [status.value.lower() for status in request.status_list or []]
            filters = {
                "knowledge_base_id": knowledge_base_id,
                "task_id": request.task_id,
                "fs_entry_id": fs_entry_id,
                "batch_id": request.batch_id,
                "statuses": statuses or None,
                "latest_only": request.latest_only,
            }
            total = await self.task_repository.count_tasks(cursor, **filters)
            rows = await self.task_repository.list_tasks(
                cursor,
                **filters,
                limit=request.page_size,
                offset=(request.page_num - 1) * request.page_size,
            )
            return {
                "knowledgeBaseId": str(knowledge_base_id),
                "knCode": request.kb_code,
                **({"filePath": request.file_path} if request.file_path else {}),
                "total": total,
                "pageNum": request.page_num,
                "pageSize": request.page_size,
                "data": [
                    self._task_item(row, include_details=request.include_details)
                    for row in rows
                ],
            }
        finally:
            await connection.close()

    async def get_unified_processing_task_status(
        self, request: ProcessingTaskStatusRequest
    ) -> dict[str, Any]:
        """Query both task pools with one database-level page and sort order."""
        if self.unified_task_repository is None:
            raise RuntimeError("unified processing task repository is not configured")
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            knowledge_base_id = await self._resolve_kb(cursor, request.kb_code)
            fs_entry_id = await self._resolve_file_filter(
                cursor,
                knowledge_base_id=knowledge_base_id,
                file_id=request.file_id,
                file_path=request.file_path,
            )
            total, rows = await self.unified_task_repository.query(
                cursor,
                knowledge_base_id=knowledge_base_id,
                task_id=request.task_id,
                fs_entry_id=fs_entry_id,
                batch_id=request.batch_id,
                task_type=request.task_type.value if request.task_type else None,
                statuses=[status.value.lower() for status in request.status_list or []],
                latest_only=request.latest_only,
                limit=request.page_size,
                offset=(request.page_num - 1) * request.page_size,
            )
            return {
                "knowledgeBaseId": str(knowledge_base_id),
                "knCode": request.kb_code,
                **({"filePath": request.file_path} if request.file_path else {}),
                "total": total,
                "pageNum": request.page_num,
                "pageSize": request.page_size,
                "data": [
                    self._unified_task_item(
                        row, include_details=request.include_details
                    )
                    for row in rows
                ],
            }
        finally:
            await connection.close()

    async def get_processing_batch_status(
        self, request: ProcessingBatchStatusRequest
    ) -> dict[str, Any] | None:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            knowledge_base_id = await self._resolve_kb(cursor, request.kb_code)
            batch = await self.batch_repository.get_batch(
                cursor,
                batch_id=request.batch_id,
                knowledge_base_id=knowledge_base_id,
            )
            if batch is None:
                return None
            counts = await self.batch_repository.count_tasks_by_status(
                cursor, batch_id=request.batch_id
            )
            rows = await self.task_repository.list_tasks(
                cursor,
                knowledge_base_id=knowledge_base_id,
                batch_id=request.batch_id,
                latest_only=False,
                limit=request.page_size,
                offset=(request.page_num - 1) * request.page_size,
            )
            accepted_count = int(batch["accepted_count"])
            completed_count = int(batch["completed_count"])
            return {
                "batchId": str(batch["batch_id"]),
                "knowledgeBaseId": str(knowledge_base_id),
                "knCode": request.kb_code,
                "taskType": "FILE_BUILD",
                "scope": str(batch["scope"]),
                "targetPath": str(batch["target_path_snapshot"]),
                "status": str(batch["status"]).upper(),
                "version": int(batch["version"]),
                "candidateCount": int(batch["candidate_count"]),
                "eligibleCount": int(batch["eligible_count"]),
                "acceptedCount": accepted_count,
                "reusedCount": int(batch["reused_count"]),
                "acceptanceSkippedCount": int(batch["acceptance_skipped_count"]),
                "totalCount": accepted_count,
                "completedCount": completed_count,
                "pendingCount": int(counts.get("pending", 0)),
                "runningCount": int(counts.get("running", 0)),
                "succeededCount": int(counts.get("succeeded", 0)),
                "failedCount": int(counts.get("failed", 0)),
                "skippedCount": int(counts.get("skipped", 0)),
                "unsupportedCount": int(counts.get("unsupported", 0)),
                "progress": (
                    100
                    if accepted_count == 0
                    else completed_count * 100 // accepted_count
                ),
                "createdAt": batch["created_at"],
                **(
                    {"completedAt": batch["completed_at"]}
                    if batch.get("completed_at") is not None
                    else {}
                ),
                "pageNum": request.page_num,
                "pageSize": request.page_size,
                "data": [
                    self._task_item(row, include_details=request.include_details)
                    for row in rows
                ],
            }
        finally:
            await connection.close()

    async def _resolve_kb(self, cursor: Any, kb_code: str) -> int:
        row = await self.knowledge_base_repository.get_by_code(cursor, kb_code)
        if row is None:
            raise KnowledgeBaseValidationError(f"knowledge base not found: {kb_code}")
        return self._row_id(row)

    async def _resolve_file_filter(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        file_id: int | None,
        file_path: str | None,
    ) -> int | None:
        path_row = None
        if file_path is not None:
            path_row = await self.knowledge_fs_entry_repository.get_file_by_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=file_path,
            )
            if path_row is None:
                raise KnowledgeBaseValidationError(f"file not found: {file_path}")
        if file_id is not None:
            id_row = await self.knowledge_fs_entry_repository.get_file_by_id_including_deleted(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=file_id,
            )
            if id_row is None:
                raise KnowledgeBaseValidationError(f"file not found: {file_id}")
            if path_row is not None and self._row_id(path_row) != file_id:
                raise KnowledgeBaseValidationError(
                    "fileId and filePath identify different files"
                )
            return file_id
        return self._row_id(path_row) if path_row is not None else None

    @staticmethod
    def _normalize_target_path(path: str) -> str:
        stripped = path.strip()
        if not stripped.startswith("/"):
            raise KnowledgeBaseValidationError("filePath must start with '/'")
        if any(part == ".." for part in stripped.split("/")):
            raise KnowledgeBaseValidationError("filePath must not contain '..'")
        parts = [part for part in stripped.split("/") if part]
        return "/" + "/".join(parts) if parts else "/"

    @staticmethod
    def _acceptance_item(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "taskId": str(row["task_id"]),
            "status": str(row["status"]).upper(),
            "fileId": str(row["fs_entry_id"]),
            "filePathSnapshot": str(row["file_path_snapshot"]),
            "reused": bool(row["reused"]),
        }

    @staticmethod
    def _task_item(row: dict[str, Any], *, include_details: bool) -> dict[str, Any]:
        item: dict[str, Any] = {
            "taskId": str(row["kid"]),
            "batchId": row.get("batch_id"),
            "taskType": "FILE_BUILD",
            "origin": str(row["origin"]),
            "executionMode": str(row["execution_mode"]),
            "parentSemanticTaskId": (
                str(row["parent_semantic_task_id"])
                if row.get("parent_semantic_task_id") is not None
                else None
            ),
            "status": str(row["status"]).upper(),
            "currentStage": str(row["current_stage"]).upper(),
            "progress": int(row["progress"]),
            "fileId": str(row["fs_entry_id"]),
            "filePathSnapshot": str(row["file_path_snapshot"]),
            "inputChecksum": row.get("input_checksum"),
            "inputIsDeleted": bool(row["input_is_deleted"]),
            "outcomeUncertain": bool(row["outcome_uncertain"]),
            "createdAt": row["created_at"],
            "startedAt": row.get("started_at"),
            "finishedAt": row.get("finished_at"),
        }
        if include_details:
            item["buildProfile"] = row.get("build_profile")
            item["buildProfileHash"] = row.get("build_profile_hash")
            if row.get("result_payload") is not None:
                item["result"] = row["result_payload"]
            if row.get("error_code") is not None or row.get("error_message"):
                item["error"] = {
                    "errorCode": row.get("error_code") or "BUILD_FAILED",
                    "message": row.get("error_message") or "",
                }
        return {key: value for key, value in item.items() if value is not None}

    @classmethod
    def _unified_task_item(
        cls, row: dict[str, Any], *, include_details: bool
    ) -> dict[str, Any]:
        if row["task_type"] == "FILE_BUILD":
            return cls._task_item(row, include_details=include_details)
        item: dict[str, Any] = {
            "taskId": str(row["kid"]),
            "batchId": row.get("batch_id"),
            "taskType": str(row["task_type"]),
            "status": str(row["status"]).upper(),
            "currentStage": row.get("current_stage"),
            "progress": int(row["progress"]),
            "fileId": (
                str(row["fs_entry_id"]) if row.get("fs_entry_id") is not None else None
            ),
            "filePath": str(row["file_path_snapshot"]),
            "indexVersion": row.get("index_version"),
            "createdAt": row["created_at"],
            "startedAt": row.get("started_at"),
            "finishedAt": row.get("finished_at"),
        }
        if include_details:
            if row.get("result_payload") is not None:
                item["result"] = row["result_payload"]
            if row.get("error_code") is not None or row.get("error_message"):
                item["error"] = {
                    "errorCode": row.get("error_code") or "PROCESSING_FAILED",
                    "message": row.get("error_message") or "",
                }
        return {key: value for key, value in item.items() if value is not None}

    @staticmethod
    def _row_id(row: dict[str, Any]) -> int:
        return int(row.get("kid", row.get("id")))
