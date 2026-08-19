"""Persistence helpers for KnowledgeEntity discovery and enrichment tasks."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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
        file_path_snapshot: str = "",
        current_stage: str | None = None,
        progress: int | None = 0,
        input_fingerprint: str | None = None,
        input_checksum: str | None = None,
        method_version: str | None = None,
        index_version: str | None = None,
        request_params: dict[str, Any] | None = None,
        extra_params: Mapping[str, Any] | None = None,
        result_payload: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        failure_kind: str | None = None,
        outcome_uncertain: bool = False,
    ) -> dict[str, Any] | None:
        """Create one Discovery or Enrich task for an actual input file."""
        normalized_task_type = self._normalize_task_type(task_type)
        normalized_status = self._normalize_status(status)
        normalized_progress = 0 if progress is None else progress
        self._validate_progress(normalized_progress)
        # The same bound status parameter is used as both an INSERT value and a
        # CASE operand. OpenGauss otherwise infers varchar from the target column
        # and text from the comparison, then rejects the prepared statement.
        await cursor.execute(
            """
            INSERT INTO knowledge_semantic_processing_task (
                knowledge_base_id,
                fs_entry_id,
                task_type,
                batch_id,
                file_path_snapshot,
                status,
                current_stage,
                progress,
                input_fingerprint,
                input_checksum,
                method_version,
                index_version,
                request_params,
                extra_params,
                result_payload,
                error_code,
                error_message,
                failure_kind,
                outcome_uncertain,
                started_at,
                finished_at,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                %(fs_entry_id)s,
                %(task_type)s,
                %(batch_id)s,
                %(file_path_snapshot)s,
                %(status)s::varchar(32),
                %(current_stage)s,
                %(progress)s,
                %(input_fingerprint)s,
                %(input_checksum)s,
                %(method_version)s,
                %(index_version)s,
                %(request_params)s::jsonb,
                %(extra_params)s::jsonb,
                %(result_payload)s::jsonb,
                %(error_code)s,
                %(error_message)s,
                %(failure_kind)s,
                %(outcome_uncertain)s,
                CASE
                    WHEN %(status)s::varchar(32) = 'running'::varchar(32)
                        THEN NOW()
                    ELSE NULL
                END,
                CASE
                    WHEN %(status)s::varchar(32) IN (
                        'succeeded'::varchar(32),
                        'failed'::varchar(32),
                        'skipped'::varchar(32),
                        'cancelled'::varchar(32)
                    ) THEN NOW()
                    ELSE NULL
                END,
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
                "file_path_snapshot": file_path_snapshot,
                "status": normalized_status,
                "current_stage": current_stage,
                "progress": normalized_progress,
                "input_fingerprint": input_fingerprint,
                "input_checksum": input_checksum,
                "method_version": method_version,
                "index_version": index_version,
                "request_params": self._json_value(request_params or {}),
                "extra_params": self._json_value(extra_params or {}),
                "result_payload": self._json_value(result_payload),
                "error_code": error_code,
                "error_message": error_message,
                "failure_kind": failure_kind,
                "outcome_uncertain": outcome_uncertain,
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
                    COALESCE(fs.virtual_path, task.file_path_snapshot) AS file_path,
                    ROW_NUMBER() OVER (
                        PARTITION BY task.fs_entry_id, task.task_type
                        ORDER BY task.created_at DESC, task.kid DESC
                    ) AS task_rank
                FROM knowledge_semantic_processing_task task
                LEFT JOIN knowledge_fs_entry fs ON fs.kid = task.fs_entry_id
                WHERE {" AND ".join(conditions)}
            )
            SELECT
                kid,
                knowledge_base_id,
                fs_entry_id,
                file_path,
                task_type,
                batch_id,
                file_path_snapshot,
                status,
                current_stage,
                progress,
                input_fingerprint,
                input_checksum,
                method_version,
                index_version,
                request_params,
                extra_params,
                result_payload,
                error_code,
                error_message,
                failure_kind,
                outcome_uncertain,
                worker_id,
                lease_token,
                heartbeat_at,
                lease_expires_at,
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

    async def claim_next_task(
        self,
        cursor: Any,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        """Atomically claim one pending task without blocking other runners."""
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be greater than 0")
        await cursor.execute(
            """
            UPDATE knowledge_semantic_processing_task
            SET status = 'running',
                current_stage = 'started',
                progress = 1,
                worker_id = %(worker_id)s,
                lease_token = %(lease_token)s,
                heartbeat_at = NOW(),
                lease_expires_at = NOW()
                    + (%(lease_seconds)s * INTERVAL '1 second'),
                started_at = COALESCE(started_at, NOW()),
                updated_at = NOW()
            WHERE kid IN (
                SELECT kid
                FROM knowledge_semantic_processing_task
                WHERE status = 'pending'
                ORDER BY created_at, kid
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            {
                "worker_id": worker_id,
                "lease_token": lease_token,
                "lease_seconds": lease_seconds,
            },
        )
        return await cursor.fetchone()

    async def refresh_lease(
        self,
        cursor: Any,
        *,
        task_id: int,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        """Refresh only the lease still owned by the calling worker."""
        await cursor.execute(
            """
            UPDATE knowledge_semantic_processing_task
            SET heartbeat_at = NOW(),
                lease_expires_at = NOW()
                    + (%(lease_seconds)s * INTERVAL '1 second'),
                updated_at = NOW()
            WHERE kid = %(task_id)s
              AND status = 'running'
              AND worker_id = %(worker_id)s
              AND lease_token = %(lease_token)s
            RETURNING kid
            """,
            {
                "task_id": task_id,
                "worker_id": worker_id,
                "lease_token": lease_token,
                "lease_seconds": lease_seconds,
            },
        )
        return await cursor.fetchone() is not None

    async def finish_claimed_task(
        self,
        cursor: Any,
        *,
        task_id: int,
        lease_token: str,
        status: str,
        current_stage: str,
        index_version: str | None = None,
        result_payload: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        failure_kind: str | None = None,
        outcome_uncertain: bool = False,
    ) -> dict[str, Any] | None:
        """Finish an owned running task and fence stale workers."""
        normalized_status = self._normalize_status(status)
        if normalized_status not in {"succeeded", "failed"}:
            raise ValueError("claimed task may only finish as succeeded or failed")
        await cursor.execute(
            """
            UPDATE knowledge_semantic_processing_task
            SET status = %(status)s,
                current_stage = %(current_stage)s,
                progress = 100,
                index_version = %(index_version)s,
                result_payload = %(result_payload)s::jsonb,
                error_code = %(error_code)s,
                error_message = %(error_message)s,
                failure_kind = %(failure_kind)s,
                outcome_uncertain = %(outcome_uncertain)s,
                worker_id = NULL,
                lease_token = NULL,
                heartbeat_at = NULL,
                lease_expires_at = NULL,
                finished_at = NOW(),
                updated_at = NOW()
            WHERE kid = %(task_id)s
              AND status = 'running'
              AND lease_token = %(lease_token)s
            RETURNING *
            """,
            {
                "task_id": task_id,
                "lease_token": lease_token,
                "status": normalized_status,
                "current_stage": current_stage,
                "index_version": index_version,
                "result_payload": self._json_value(result_payload),
                "error_code": error_code,
                "error_message": error_message,
                "failure_kind": failure_kind,
                "outcome_uncertain": outcome_uncertain,
            },
        )
        return await cursor.fetchone()

    async def lock_next_expired_task(self, cursor: Any) -> dict[str, Any] | None:
        """Lock one expired running task for Lease Reaper processing."""
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_semantic_processing_task
            WHERE status = 'running'
              AND lease_expires_at < NOW()
            ORDER BY lease_expires_at, kid
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        )
        return await cursor.fetchone()

    async def fail_locked_expired_task(
        self, cursor: Any, *, task_id: int
    ) -> dict[str, Any] | None:
        """Fail an expired task previously locked by the current transaction."""
        await cursor.execute(
            """
            UPDATE knowledge_semantic_processing_task
            SET status = 'failed',
                current_stage = 'failed',
                progress = 100,
                error_code = 'WORKER_LOST',
                error_message = 'Worker lease expired during execution',
                failure_kind = 'WORKER_LOST',
                outcome_uncertain = TRUE,
                worker_id = NULL,
                lease_token = NULL,
                heartbeat_at = NULL,
                lease_expires_at = NULL,
                finished_at = NOW(),
                updated_at = NOW()
            WHERE kid = %(task_id)s
              AND status = 'running'
              AND lease_expires_at < NOW()
            RETURNING *
            """,
            {"task_id": task_id},
        )
        return await cursor.fetchone()

    async def get_task(self, cursor: Any, *, task_id: int) -> dict[str, Any] | None:
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_semantic_processing_task
            WHERE kid = %(task_id)s
            """,
            {"task_id": task_id},
        )
        return await cursor.fetchone()

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

    def _json_value(self, value: Mapping[str, Any] | None) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)
