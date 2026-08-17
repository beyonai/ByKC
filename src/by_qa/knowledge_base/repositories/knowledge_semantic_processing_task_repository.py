"""Persistence helpers for KnowledgeEntity discovery and enrichment tasks."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from by_qa.core import logger

SEMANTIC_TASK_TYPES = frozenset({"ENTITY_DISCOVERY", "DOCUMENT_ENRICH"})
SEMANTIC_TASK_STATUSES = frozenset(
    {"pending", "running", "succeeded", "failed", "cancelled", "skipped"}
)


class KnowledgeSemanticProcessingTaskRepository:
    """Repository for rows in the additive semantic-processing task table."""

    async def create_processing_task(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        task_type: str,
        status: str = "pending",
        batch_id: str | None = None,
        current_stage: str | None = None,
        progress: int | None = 0,
        input_fingerprint: str | None = None,
        input_checksum: str | None = None,
        definition_version: str | None = None,
        enrich_version: str | None = None,
        method_version: str | None = None,
        index_version: str | None = None,
        request_params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Create one Discovery or Enrich task for an actual input file."""
        normalized_task_type = self._normalize_task_type(task_type)
        normalized_status = self._normalize_status(status)
        normalized_progress = 0 if progress is None else progress
        self._validate_progress(normalized_progress)
        await cursor.execute(
            """
            INSERT INTO knowledge_semantic_processing_task (
                knowledge_base_id,
                fs_entry_id,
                task_type,
                batch_id,
                status,
                current_stage,
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
                %(current_stage)s,
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
                "current_stage": current_stage,
                "progress": normalized_progress,
                "input_fingerprint": input_fingerprint,
                "input_checksum": input_checksum,
                "definition_version": definition_version,
                "enrich_version": enrich_version,
                "method_version": method_version,
                "index_version": index_version,
                "request_params": self._json_value(request_params),
            },
        )
        row = await cursor.fetchone()
        if row is None:
            logger.warning(
                "semantic processing task create returned no row: kb_id=%s source_id=%s task_type=%s batch_id=%s",
                knowledge_base_id,
                fs_entry_id,
                normalized_task_type,
                batch_id,
            )
        else:
            logger.debug(
                "semantic processing task created: task_id=%s kb_id=%s source_id=%s task_type=%s batch_id=%s status=%s stage=%s",
                row.get("kid"),
                knowledge_base_id,
                fs_entry_id,
                normalized_task_type,
                batch_id,
                row.get("status", normalized_status),
                row.get("current_stage", current_stage),
            )
        return row

    async def update_processing_task(
        self,
        cursor: Any,
        *,
        task_id: int,
        task_type: str,
        status: str | None = None,
        current_stage: str | None = None,
        progress: int | None = None,
        index_version: str | None = None,
        result_payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        """Update one semantic-processing task and return its current row."""
        normalized_task_type = self._normalize_task_type(task_type)
        normalized_status = (
            self._normalize_status(status) if status is not None else None
        )
        self._validate_progress(progress)
        await cursor.execute(
            """
            UPDATE knowledge_semantic_processing_task
            SET status = COALESCE(%(status)s, status),
                current_stage = COALESCE(%(current_stage)s, current_stage),
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
                "current_stage": current_stage,
                "progress": progress,
                "index_version": index_version,
                "result_payload": self._json_value(result_payload),
                "error_code": error_code,
                "error_message": error_message,
                "started": started,
                "finished": finished,
            },
        )
        row = await cursor.fetchone()
        if row is None:
            logger.warning(
                "semantic processing task update matched no row: task_id=%s task_type=%s requested_status=%s stage=%s",
                task_id,
                normalized_task_type,
                normalized_status,
                current_stage,
            )
            return None
        logger.debug(
            "semantic processing task updated: task_id=%s task_type=%s status=%s stage=%s progress=%s error_code=%s started=%s finished=%s",
            task_id,
            normalized_task_type,
            row.get("status", normalized_status),
            row.get("current_stage", current_stage),
            row.get("progress", progress),
            row.get("error_code", error_code),
            started,
            finished,
        )
        return row

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
        """List tasks with stable pagination and latest-task grouping."""
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
                FROM knowledge_semantic_processing_task task
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
                current_stage,
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
        rows = await cursor.fetchall()
        logger.debug(
            "semantic processing tasks listed: kb_id=%s source_id=%s task_type=%s batch_id=%s latest_only=%s count=%s offset=%s limit=%s",
            knowledge_base_id,
            fs_entry_id,
            task_type,
            batch_id,
            latest_only,
            len(rows),
            offset,
            limit,
        )
        return rows

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
        """Count tasks using the list method's filters and grouping."""
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
                FROM knowledge_semantic_processing_task task
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
        total = int(row["total"]) if row else 0
        logger.debug(
            "semantic processing tasks counted: kb_id=%s source_id=%s task_type=%s batch_id=%s latest_only=%s total=%s",
            knowledge_base_id,
            fs_entry_id,
            task_type,
            batch_id,
            latest_only,
            total,
        )
        return total

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
        if task_type is not None:
            conditions.append("task.task_type = %(task_type)s")
            params["task_type"] = self._normalize_task_type(task_type)
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
        params["statuses"] = [self._normalize_status(value) for value in statuses]
        return "AND status = ANY(%(statuses)s)"

    def _normalize_task_type(self, task_type: str) -> str:
        normalized = task_type.strip().upper()
        if normalized not in SEMANTIC_TASK_TYPES:
            raise ValueError(f"unsupported semantic task type: {task_type}")
        return normalized

    def _normalize_status(self, status: str) -> str:
        normalized = status.strip().lower()
        if normalized not in SEMANTIC_TASK_STATUSES:
            raise ValueError(f"unsupported semantic task status: {status}")
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
