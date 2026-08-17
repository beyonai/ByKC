"""Persistence helpers for per-file processing tasks.

The table keeps its historical ``knowledge_build_task`` name, but now stores
both the original file-build workflow and KnowledgeEntity processing tasks.
Legacy methods in this repository are intentionally restricted to
``FILE_BUILD`` so entity task history is not mistaken for, or deleted with,
build state.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

FILE_BUILD_TASK_TYPE = "FILE_BUILD"
ENTITY_TASK_TYPES = frozenset({"ENTITY_DISCOVERY", "DOCUMENT_ENRICH"})
ENTITY_TASK_STATUSES = frozenset(
    {"pending", "running", "succeeded", "failed", "cancelled", "skipped"}
)


class KnowledgeBuildTaskRepository:
    """Repository for build and KnowledgeEntity per-file task rows."""

    async def delete_for_fs_entry_id(self, cursor: Any, *, fs_entry_id: int) -> None:
        """Delete only FILE_BUILD history for one file entry."""
        await cursor.execute(
            """
            DELETE FROM knowledge_build_task
            WHERE fs_entry_id = %(fs_entry_id)s
              AND task_type = %(task_type)s
            """,
            {"fs_entry_id": fs_entry_id, "task_type": FILE_BUILD_TASK_TYPE},
        )

    async def get_latest_by_fs_entry_id(
        self, cursor: Any, *, fs_entry_id: int
    ) -> dict[str, Any] | None:
        """Fetch the latest FILE_BUILD task for one file entry."""
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_build_task
            WHERE fs_entry_id = %(fs_entry_id)s
              AND task_type = %(task_type)s
            ORDER BY created_at DESC, kid DESC
            LIMIT 1
            """,
            {"fs_entry_id": fs_entry_id, "task_type": FILE_BUILD_TASK_TYPE},
        )
        return await cursor.fetchone()

    async def create_task(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        status: str,
        current_step: str | None,
    ) -> dict[str, Any] | None:
        """Create one FILE_BUILD task row using the legacy call contract."""
        await cursor.execute(
            """
            INSERT INTO knowledge_build_task (
                knowledge_base_id,
                fs_entry_id,
                task_type,
                status,
                current_step,
                started_at,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                %(fs_entry_id)s,
                %(task_type)s,
                %(status)s,
                %(current_step)s,
                NOW(),
                NOW(),
                NOW()
            )
            RETURNING *
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "task_type": FILE_BUILD_TASK_TYPE,
                "status": status,
                "current_step": current_step,
            },
        )
        return await cursor.fetchone()

    async def update_task(
        self,
        cursor: Any,
        *,
        task_id: int,
        status: str | None = None,
        current_step: str | None = None,
        error_message: str | None = None,
        finished: bool = False,
    ) -> None:
        """Update one FILE_BUILD task using the legacy call contract."""
        await cursor.execute(
            """
            UPDATE knowledge_build_task
            SET status = COALESCE(%(status)s, status),
                current_step = COALESCE(%(current_step)s, current_step),
                error_message = %(error_message)s,
                finished_at = CASE
                    WHEN %(finished)s THEN NOW()
                    ELSE finished_at
                END,
                updated_at = NOW()
            WHERE kid = %(task_id)s
              AND task_type = %(task_type)s
            """,
            {
                "task_id": task_id,
                "task_type": FILE_BUILD_TASK_TYPE,
                "status": status,
                "current_step": current_step,
                "error_message": error_message,
                "finished": finished,
            },
        )

    async def create_processing_task(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        task_type: str,
        status: str = "pending",
        batch_id: str | None = None,
        current_step: str | None = None,
        progress: int | None = 0,
        input_fingerprint: str | None = None,
        input_checksum: str | None = None,
        definition_version: str | None = None,
        enrich_version: str | None = None,
        method_version: str | None = None,
        index_version: str | None = None,
        request_params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Create a Discovery or Enrich task for one actual input file."""
        normalized_task_type = self._normalize_entity_task_type(task_type)
        normalized_status = self._normalize_entity_status(status)
        self._validate_progress(progress)
        await cursor.execute(
            """
            INSERT INTO knowledge_build_task (
                knowledge_base_id,
                fs_entry_id,
                task_type,
                batch_id,
                status,
                current_step,
                progress,
                input_fingerprint,
                input_checksum,
                definition_version,
                enrich_version,
                method_version,
                index_version,
                request_params,
                started_at,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                %(fs_entry_id)s,
                %(task_type)s,
                %(batch_id)s,
                %(status)s,
                %(current_step)s,
                %(progress)s,
                %(input_fingerprint)s,
                %(input_checksum)s,
                %(definition_version)s,
                %(enrich_version)s,
                %(method_version)s,
                %(index_version)s,
                %(request_params)s::jsonb,
                CASE WHEN %(status)s = 'running' THEN NOW() ELSE NULL END,
                NOW(),
                NOW()
            )
            RETURNING *
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "task_type": normalized_task_type,
                "batch_id": batch_id,
                "status": normalized_status,
                "current_step": current_step,
                "progress": progress,
                "input_fingerprint": input_fingerprint,
                "input_checksum": input_checksum,
                "definition_version": definition_version,
                "enrich_version": enrich_version,
                "method_version": method_version,
                "index_version": index_version,
                "request_params": self._json_value(request_params),
            },
        )
        return await cursor.fetchone()

    async def update_processing_task(
        self,
        cursor: Any,
        *,
        task_id: int,
        task_type: str,
        status: str | None = None,
        current_step: str | None = None,
        progress: int | None = None,
        index_version: str | None = None,
        result_payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        """Update a Discovery or Enrich task and return its current row."""
        normalized_task_type = self._normalize_entity_task_type(task_type)
        normalized_status = (
            self._normalize_entity_status(status) if status is not None else None
        )
        self._validate_progress(progress)
        await cursor.execute(
            """
            UPDATE knowledge_build_task
            SET status = COALESCE(%(status)s, status),
                current_step = COALESCE(%(current_step)s, current_step),
                progress = COALESCE(%(progress)s, progress),
                index_version = COALESCE(%(index_version)s, index_version),
                result_payload = COALESCE(%(result_payload)s::jsonb, result_payload),
                error_code = COALESCE(%(error_code)s, error_code),
                error_message = COALESCE(%(error_message)s, error_message),
                started_at = CASE
                    WHEN %(started)s AND started_at IS NULL THEN NOW()
                    ELSE started_at
                END,
                finished_at = CASE
                    WHEN %(finished)s THEN NOW()
                    ELSE finished_at
                END,
                updated_at = NOW()
            WHERE kid = %(task_id)s
              AND task_type = %(task_type)s
            RETURNING *
            """,
            {
                "task_id": task_id,
                "task_type": normalized_task_type,
                "status": normalized_status,
                "current_step": current_step,
                "progress": progress,
                "index_version": index_version,
                "result_payload": self._json_value(result_payload),
                "error_code": error_code,
                "error_message": error_message,
                "started": started,
                "finished": finished,
            },
        )
        return await cursor.fetchone()

    async def list_processing_tasks(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int | None = None,
        batch_id: str | None = None,
        task_type: str | None = None,
        statuses: Sequence[str] | None = None,
        latest_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List entity tasks with stable pagination and latest-task grouping."""
        self._validate_page(limit=limit, offset=offset)
        conditions, params = self._processing_filters(
            knowledge_base_id=knowledge_base_id,
            fs_entry_id=fs_entry_id,
            batch_id=batch_id,
            task_type=task_type,
        )
        status_condition = self._status_condition(statuses, params)
        await cursor.execute(
            f"""
            WITH ranked_tasks AS (
                SELECT
                    task.*,
                    fs.virtual_path AS file_path,
                    ROW_NUMBER() OVER (
                        PARTITION BY task.fs_entry_id, task.task_type
                        ORDER BY task.created_at DESC, task.kid DESC
                    ) AS task_rank
                FROM knowledge_build_task task
                JOIN knowledge_fs_entry fs ON fs.kid = task.fs_entry_id
                WHERE {" AND ".join(conditions)}
            )
            SELECT
                kid,
                knowledge_base_id,
                fs_entry_id,
                file_path,
                task_type,
                batch_id,
                status,
                current_step,
                progress,
                input_fingerprint,
                input_checksum,
                definition_version,
                enrich_version,
                method_version,
                index_version,
                request_params,
                result_payload,
                error_code,
                error_message,
                started_at,
                finished_at,
                created_at,
                updated_at
            FROM ranked_tasks
            WHERE (%(latest_only)s = FALSE OR task_rank = 1)
              {status_condition}
            ORDER BY created_at DESC, kid DESC
            LIMIT %(limit)s
            OFFSET %(offset)s
            """,
            {
                **params,
                "latest_only": latest_only,
                "limit": limit,
                "offset": offset,
            },
        )
        return await cursor.fetchall()

    async def count_processing_tasks(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int | None = None,
        batch_id: str | None = None,
        task_type: str | None = None,
        statuses: Sequence[str] | None = None,
        latest_only: bool = True,
    ) -> int:
        """Count tasks using the same filters and grouping as the list method."""
        conditions, params = self._processing_filters(
            knowledge_base_id=knowledge_base_id,
            fs_entry_id=fs_entry_id,
            batch_id=batch_id,
            task_type=task_type,
        )
        status_condition = self._status_condition(statuses, params)
        await cursor.execute(
            f"""
            WITH ranked_tasks AS (
                SELECT
                    task.status,
                    ROW_NUMBER() OVER (
                        PARTITION BY task.fs_entry_id, task.task_type
                        ORDER BY task.created_at DESC, task.kid DESC
                    ) AS task_rank
                FROM knowledge_build_task task
                WHERE {" AND ".join(conditions)}
            )
            SELECT COUNT(*) AS total
            FROM ranked_tasks
            WHERE (%(latest_only)s = FALSE OR task_rank = 1)
              {status_condition}
            """,
            {**params, "latest_only": latest_only},
        )
        row = await cursor.fetchone()
        return int(row["total"]) if row else 0

    def _processing_filters(
        self,
        *,
        knowledge_base_id: int,
        fs_entry_id: int | None,
        batch_id: str | None,
        task_type: str | None,
    ) -> tuple[list[str], dict[str, Any]]:
        conditions = ["task.knowledge_base_id = %(knowledge_base_id)s"]
        params: dict[str, Any] = {"knowledge_base_id": knowledge_base_id}
        if task_type is None:
            conditions.append(
                "task.task_type IN ('ENTITY_DISCOVERY', 'DOCUMENT_ENRICH')"
            )
        else:
            conditions.append("task.task_type = %(task_type)s")
            params["task_type"] = self._normalize_entity_task_type(task_type)
        if fs_entry_id is not None:
            conditions.append("task.fs_entry_id = %(fs_entry_id)s")
            params["fs_entry_id"] = fs_entry_id
        if batch_id is not None:
            conditions.append("task.batch_id = %(batch_id)s")
            params["batch_id"] = batch_id
        return conditions, params

    def _status_condition(
        self, statuses: Sequence[str] | None, params: dict[str, Any]
    ) -> str:
        if not statuses:
            return ""
        params["statuses"] = [
            self._normalize_entity_status(value) for value in statuses
        ]
        return "AND status = ANY(%(statuses)s)"

    def _normalize_entity_task_type(self, task_type: str) -> str:
        normalized = task_type.strip().upper()
        if normalized not in ENTITY_TASK_TYPES:
            raise ValueError(f"unsupported entity task type: {task_type}")
        return normalized

    def _normalize_entity_status(self, status: str) -> str:
        normalized = status.strip().lower()
        if normalized not in ENTITY_TASK_STATUSES:
            raise ValueError(f"unsupported entity task status: {status}")
        return normalized

    def _validate_progress(self, progress: int | None) -> None:
        if progress is not None and (progress < 0 or progress > 100):
            raise ValueError("progress must be between 0 and 100")

    def _validate_page(self, *, limit: int, offset: int) -> None:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must be greater than or equal to 0")

    def _json_value(self, value: dict[str, Any] | None) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)
