"""Persistence helpers for durable file-build task rows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

FILE_BUILD_TASK_STATUSES = frozenset(
    {
        "pending",
        "running",
        "succeeded",
        "failed",
        "skipped",
        "unsupported",
    }
)
LEGACY_BUILD_TASK_STATUSES = frozenset({"complete"})
FILE_BUILD_STAGES = frozenset(
    {"accepted", "extracting", "chunking", "embedding", "committing"}
)
LEGACY_BUILD_PROFILE = {"legacy": True, "profileVersion": 0}
LEGACY_BUILD_PROFILE_JSON = json.dumps(
    LEGACY_BUILD_PROFILE,
    ensure_ascii=False,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
)
LEGACY_BUILD_PROFILE_HASH = hashlib.sha256(
    LEGACY_BUILD_PROFILE_JSON.encode("utf-8")
).hexdigest()

_TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "skipped", "unsupported", "complete"}
)
_LEGACY_STEP_TO_STAGE = {
    "markdown": "extracting",
    "chunking": "chunking",
    "vectorizing": "embedding",
    "complete": "committing",
}
_STAGE_TO_LEGACY_STEP = {
    "accepted": "markdown",
    "extracting": "markdown",
    "chunking": "chunking",
    "embedding": "vectorizing",
    "committing": "vectorizing",
}
_STAGE_PROGRESS = {
    "accepted": 0,
    "extracting": 10,
    "chunking": 35,
    "embedding": 55,
    "committing": 90,
}


class KnowledgeBuildTaskRepository:
    """Repository for legacy-compatible and new-protocol file-build tasks."""

    async def delete_for_fs_entry_id(self, cursor: Any, *, fs_entry_id: int) -> None:
        """Delete history for legacy callers; new mutation flows must not use it."""
        await cursor.execute(
            "DELETE FROM knowledge_build_task WHERE fs_entry_id = %(fs_entry_id)s",
            {"fs_entry_id": fs_entry_id},
        )

    async def get_latest_by_fs_entry_id(
        self, cursor: Any, *, fs_entry_id: int
    ) -> dict[str, Any] | None:
        """Fetch the latest build task for one stable file identity."""
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_build_task
            WHERE fs_entry_id = %(fs_entry_id)s
            ORDER BY created_at DESC, kid DESC
            LIMIT 1
            """,
            {"fs_entry_id": fs_entry_id},
        )
        return await cursor.fetchone()

    async def get_latest_by_fs_entry_ids(
        self, cursor: Any, *, fs_entry_ids: list[int]
    ) -> list[dict[str, Any]]:
        """Fetch the latest build task for each requested file entry."""
        if not fs_entry_ids:
            return []
        await cursor.execute(
            """
            SELECT *
            FROM (
                SELECT
                    task.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY fs_entry_id
                        ORDER BY created_at DESC, kid DESC
                    ) AS row_no
                FROM knowledge_build_task task
                WHERE fs_entry_id = ANY(%(fs_entry_ids)s)
            ) ranked
            WHERE row_no = 1
            """,
            {"fs_entry_ids": fs_entry_ids},
        )
        return await cursor.fetchall()

    async def create_task(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        status: str,
        current_step: str | None,
        file_path_snapshot: str = "",
    ) -> dict[str, Any] | None:
        """Create a legacy-compatible task while old callers are being migrated."""
        normalized_status = self._normalize_status(status, allow_legacy=True)
        current_stage = self._normalize_stage(current_step or "accepted")
        running = normalized_status == "running"
        terminal = normalized_status in _TERMINAL_STATUSES
        lease_token = uuid4().hex if running else None
        await cursor.execute(
            """
            INSERT INTO knowledge_build_task (
                knowledge_base_id,
                fs_entry_id,
                batch_id,
                origin,
                execution_mode,
                parent_semantic_task_id,
                file_path_snapshot,
                input_checksum,
                input_is_deleted,
                build_profile,
                build_profile_hash,
                status,
                current_step,
                current_stage,
                progress,
                priority,
                outcome_uncertain,
                worker_id,
                lease_token,
                heartbeat_at,
                lease_expires_at,
                started_at,
                finished_at,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                %(fs_entry_id)s,
                NULL,
                'API',
                'BACKGROUND',
                NULL,
                %(file_path_snapshot)s,
                NULL,
                false,
                %(build_profile)s::jsonb,
                %(build_profile_hash)s,
                %(status)s::varchar(32),
                %(current_step)s,
                %(current_stage)s,
                %(progress)s,
                0,
                false,
                CASE WHEN %(running)s THEN 'legacy-background-task' ELSE NULL END,
                %(lease_token)s,
                CASE WHEN %(running)s THEN NOW() ELSE NULL END,
                CASE
                    WHEN %(running)s THEN NOW() + INTERVAL '1200 seconds'
                    ELSE NULL
                END,
                CASE WHEN %(running)s THEN NOW() ELSE NULL END,
                CASE WHEN %(terminal)s THEN NOW() ELSE NULL END,
                NOW(),
                NOW()
            )
            RETURNING *
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "file_path_snapshot": file_path_snapshot,
                "build_profile": LEGACY_BUILD_PROFILE_JSON,
                "build_profile_hash": LEGACY_BUILD_PROFILE_HASH,
                "status": normalized_status,
                "current_step": current_step,
                "current_stage": current_stage,
                "progress": 100 if terminal else _STAGE_PROGRESS[current_stage],
                "running": running,
                "terminal": terminal,
                "lease_token": lease_token,
            },
        )
        return await cursor.fetchone()

    async def create_background_task(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        batch_id: str,
        file_path_snapshot: str,
        input_checksum: str,
        input_is_deleted: bool,
        build_profile: Mapping[str, Any],
        build_profile_hash: str,
        priority: int,
    ) -> dict[str, Any] | None:
        """Create one pending task owned by an external Build Batch."""
        return await self._create_protocol_task(
            cursor,
            knowledge_base_id=knowledge_base_id,
            fs_entry_id=fs_entry_id,
            batch_id=batch_id,
            origin="API",
            execution_mode="BACKGROUND",
            parent_semantic_task_id=None,
            file_path_snapshot=file_path_snapshot,
            input_checksum=input_checksum,
            input_is_deleted=input_is_deleted,
            build_profile=build_profile,
            build_profile_hash=build_profile_hash,
            status="pending",
            current_stage="accepted",
            priority=priority,
        )

    async def create_inline_task(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        origin: str,
        parent_semantic_task_id: int,
        file_path_snapshot: str,
        input_checksum: str,
        input_is_deleted: bool,
        build_profile: Mapping[str, Any],
        build_profile_hash: str,
    ) -> dict[str, Any] | None:
        """Create a running task synchronously owned by an Entity task."""
        normalized_origin = origin.strip().upper()
        if normalized_origin not in {"ENTITY_DISCOVERY", "ENTITY_ENRICH"}:
            raise ValueError(f"unsupported inline file build origin: {origin}")
        if parent_semantic_task_id < 1:
            raise ValueError("parent_semantic_task_id must be greater than 0")
        return await self._create_protocol_task(
            cursor,
            knowledge_base_id=knowledge_base_id,
            fs_entry_id=fs_entry_id,
            batch_id=None,
            origin=normalized_origin,
            execution_mode="INLINE",
            parent_semantic_task_id=parent_semantic_task_id,
            file_path_snapshot=file_path_snapshot,
            input_checksum=input_checksum,
            input_is_deleted=input_is_deleted,
            build_profile=build_profile,
            build_profile_hash=build_profile_hash,
            status="running",
            current_stage="accepted",
            priority=0,
        )

    async def update_task(
        self,
        cursor: Any,
        *,
        task_id: int,
        status: str | None = None,
        current_step: str | None = None,
        error_message: str | None = None,
        error_code: str | None = None,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        """Update a legacy task while keeping the new stage columns coherent."""
        normalized_status = (
            self._normalize_status(status, allow_legacy=True)
            if status is not None
            else None
        )
        current_stage = (
            self._normalize_stage(current_step) if current_step is not None else None
        )
        terminal = finished or normalized_status in _TERMINAL_STATUSES
        await cursor.execute(
            """
            UPDATE knowledge_build_task
            SET status = COALESCE(%(status)s, status),
                current_step = COALESCE(%(current_step)s, current_step),
                current_stage = COALESCE(%(current_stage)s, current_stage),
                progress = CASE
                    WHEN %(terminal)s THEN 100
                    WHEN %(current_stage)s IS NOT NULL THEN %(stage_progress)s
                    ELSE progress
                END,
                error_code = %(error_code)s,
                error_message = %(error_message)s,
                worker_id = CASE WHEN %(terminal)s THEN NULL ELSE worker_id END,
                lease_token = CASE WHEN %(terminal)s THEN NULL ELSE lease_token END,
                heartbeat_at = CASE WHEN %(terminal)s THEN NULL ELSE heartbeat_at END,
                lease_expires_at = CASE
                    WHEN %(terminal)s THEN NULL
                    ELSE lease_expires_at
                END,
                finished_at = CASE
                    WHEN %(finished)s THEN NOW()
                    ELSE finished_at
                END,
                updated_at = NOW()
            WHERE kid = %(task_id)s
            RETURNING *
            """,
            {
                "task_id": task_id,
                "status": normalized_status,
                "current_step": current_step,
                "current_stage": current_stage,
                "stage_progress": (
                    _STAGE_PROGRESS[current_stage]
                    if current_stage is not None
                    else None
                ),
                "error_code": error_code,
                "error_message": error_message,
                "terminal": terminal,
                "finished": finished,
            },
        )
        return await cursor.fetchone()

    async def get_task(
        self,
        cursor: Any,
        *,
        task_id: int,
        knowledge_base_id: int | None = None,
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_build_task
            WHERE kid = %(task_id)s
              AND (
                    %(knowledge_base_id)s IS NULL
                    OR knowledge_base_id = %(knowledge_base_id)s
              )
            """,
            {"task_id": task_id, "knowledge_base_id": knowledge_base_id},
        )
        return await cursor.fetchone()

    async def get_active_for_update(
        self, cursor: Any, *, fs_entry_id: int
    ) -> dict[str, Any] | None:
        """Lock the one active task, if present."""
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_build_task
            WHERE fs_entry_id = %(fs_entry_id)s
              AND status IN ('pending', 'running')
            ORDER BY created_at DESC, kid DESC
            LIMIT 1
            FOR UPDATE
            """,
            {"fs_entry_id": fs_entry_id},
        )
        return await cursor.fetchone()

    async def find_reusable_task(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        input_checksum: str,
        input_is_deleted: bool,
        build_profile_hash: str,
        statuses: Sequence[str],
    ) -> dict[str, Any] | None:
        """Find the newest task with an identical stable input key."""
        normalized_statuses = [self._normalize_status(value) for value in statuses]
        if not normalized_statuses:
            return None
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_build_task
            WHERE fs_entry_id = %(fs_entry_id)s
              AND input_checksum = %(input_checksum)s
              AND input_is_deleted = %(input_is_deleted)s
              AND build_profile_hash = %(build_profile_hash)s
              AND status = ANY(%(statuses)s)
            ORDER BY created_at DESC, kid DESC
            LIMIT 1
            """,
            {
                "fs_entry_id": fs_entry_id,
                "input_checksum": input_checksum,
                "input_is_deleted": input_is_deleted,
                "build_profile_hash": build_profile_hash,
                "statuses": normalized_statuses,
            },
        )
        return await cursor.fetchone()

    async def supersede_active_task(
        self, cursor: Any, *, task_id: int
    ) -> dict[str, Any] | None:
        """Fence an active task before accepting different input for its file."""
        await cursor.execute(
            """
            UPDATE knowledge_build_task
            SET status = 'skipped',
                progress = 100,
                error_code = 'SUPERSEDED',
                error_message = 'Build superseded by a newer request',
                failure_kind = NULL,
                outcome_uncertain = (status = 'running'),
                worker_id = NULL,
                lease_token = NULL,
                heartbeat_at = NULL,
                lease_expires_at = NULL,
                finished_at = NOW(),
                updated_at = NOW()
            WHERE kid = %(task_id)s
              AND status IN ('pending', 'running')
            RETURNING *
            """,
            {"task_id": task_id},
        )
        return await cursor.fetchone()

    async def claim_next_task(
        self,
        cursor: Any,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        """Claim one background task with FIFO priority aging and fencing."""
        await cursor.execute(
            """
            WITH candidate AS (
                SELECT kid
                FROM knowledge_build_task
                WHERE status = 'pending'
                  AND execution_mode = 'BACKGROUND'
                ORDER BY
                    priority
                    + FLOOR(
                        EXTRACT(EPOCH FROM (NOW() - created_at)) / 60
                    ) DESC,
                    created_at,
                    kid
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE knowledge_build_task task
            SET status = 'running',
                worker_id = %(worker_id)s,
                lease_token = %(lease_token)s,
                heartbeat_at = NOW(),
                lease_expires_at = NOW()
                    + (%(lease_seconds)s * INTERVAL '1 second'),
                started_at = COALESCE(started_at, NOW()),
                updated_at = NOW()
            FROM candidate
            WHERE task.kid = candidate.kid
              AND task.status = 'pending'
            RETURNING task.*
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
        await cursor.execute(
            """
            UPDATE knowledge_build_task
            SET heartbeat_at = NOW(),
                lease_expires_at = NOW()
                    + (%(lease_seconds)s * INTERVAL '1 second'),
                updated_at = NOW()
            WHERE kid = %(task_id)s
              AND status = 'running'
              AND execution_mode = 'BACKGROUND'
              AND worker_id = %(worker_id)s
              AND lease_token = %(lease_token)s
              AND lease_expires_at > clock_timestamp()
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

    async def update_claimed_stage(
        self,
        cursor: Any,
        *,
        task_id: int,
        lease_token: str,
        current_stage: str,
    ) -> dict[str, Any] | None:
        normalized_stage = self._normalize_stage(current_stage)
        await cursor.execute(
            """
            UPDATE knowledge_build_task
            SET current_stage = %(current_stage)s,
                current_step = %(current_step)s,
                progress = %(progress)s,
                updated_at = NOW()
            WHERE kid = %(task_id)s
              AND status = 'running'
              AND execution_mode = 'BACKGROUND'
              AND lease_token = %(lease_token)s
              AND lease_expires_at > clock_timestamp()
            RETURNING *
            """,
            {
                "task_id": task_id,
                "lease_token": lease_token,
                "current_stage": normalized_stage,
                "current_step": _STAGE_TO_LEGACY_STEP[normalized_stage],
                "progress": _STAGE_PROGRESS[normalized_stage],
            },
        )
        return await cursor.fetchone()

    async def update_inline_stage(
        self,
        cursor: Any,
        *,
        task_id: int,
        current_stage: str,
    ) -> dict[str, Any] | None:
        """Advance an Entity-owned synchronous task if it is still active."""
        normalized_stage = self._normalize_stage(current_stage)
        await cursor.execute(
            """
            UPDATE knowledge_build_task
            SET current_stage = %(current_stage)s,
                current_step = %(current_step)s,
                progress = %(progress)s,
                updated_at = NOW()
            WHERE kid = %(task_id)s
              AND status = 'running'
              AND execution_mode = 'INLINE'
            RETURNING *
            """,
            {
                "task_id": task_id,
                "current_stage": normalized_stage,
                "current_step": _STAGE_TO_LEGACY_STEP[normalized_stage],
                "progress": _STAGE_PROGRESS[normalized_stage],
            },
        )
        return await cursor.fetchone()

    async def finish_claimed_task(
        self,
        cursor: Any,
        *,
        task_id: int,
        lease_token: str,
        status: str,
        result_payload: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        failure_kind: str | None = None,
        outcome_uncertain: bool = False,
    ) -> dict[str, Any] | None:
        normalized_status = self._normalize_status(status)
        if normalized_status not in _TERMINAL_STATUSES:
            raise ValueError("claimed task must finish in a terminal status")
        await cursor.execute(
            """
            UPDATE knowledge_build_task
            SET status = %(status)s::varchar(32),
                progress = 100,
                current_step = CASE
                    WHEN %(status)s::varchar(32) = 'succeeded'::varchar(32)
                        THEN 'complete'
                    ELSE current_step
                END,
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
              AND execution_mode = 'BACKGROUND'
              AND lease_token = %(lease_token)s
              AND lease_expires_at > clock_timestamp()
            RETURNING *
            """,
            {
                "task_id": task_id,
                "lease_token": lease_token,
                "status": normalized_status,
                "result_payload": self._json_value(result_payload),
                "error_code": error_code,
                "error_message": self._truncate_error(error_message),
                "failure_kind": failure_kind,
                "outcome_uncertain": outcome_uncertain,
            },
        )
        return await cursor.fetchone()

    async def finish_inline_task(
        self,
        cursor: Any,
        *,
        task_id: int,
        status: str,
        result_payload: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        failure_kind: str | None = None,
        outcome_uncertain: bool = False,
    ) -> dict[str, Any] | None:
        """Finish an Entity-owned synchronous task unless it was superseded."""
        normalized_status = self._normalize_status(status)
        if normalized_status not in _TERMINAL_STATUSES:
            raise ValueError("inline task must finish in a terminal status")
        await cursor.execute(
            """
            UPDATE knowledge_build_task
            SET status = %(status)s::varchar(32),
                progress = 100,
                current_step = CASE
                    WHEN %(status)s::varchar(32) = 'succeeded'::varchar(32)
                        THEN 'complete'
                    ELSE current_step
                END,
                result_payload = %(result_payload)s::jsonb,
                error_code = %(error_code)s,
                error_message = %(error_message)s,
                failure_kind = %(failure_kind)s,
                outcome_uncertain = %(outcome_uncertain)s,
                finished_at = NOW(),
                updated_at = NOW()
            WHERE kid = %(task_id)s
              AND status = 'running'
              AND execution_mode = 'INLINE'
            RETURNING *
            """,
            {
                "task_id": task_id,
                "status": normalized_status,
                "result_payload": self._json_value(result_payload),
                "error_code": error_code,
                "error_message": self._truncate_error(error_message),
                "failure_kind": failure_kind,
                "outcome_uncertain": outcome_uncertain,
            },
        )
        return await cursor.fetchone()

    async def lock_next_expired_task(self, cursor: Any) -> dict[str, Any] | None:
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_build_task
            WHERE status = 'running'
              AND execution_mode = 'BACKGROUND'
              AND lease_expires_at <= clock_timestamp()
            ORDER BY lease_expires_at, kid
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        return await cursor.fetchone()

    async def fail_locked_expired_task(
        self, cursor: Any, *, task_id: int
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            UPDATE knowledge_build_task
            SET status = 'failed',
                progress = 100,
                error_code = 'WORKER_LOST',
                error_message = 'Build worker lease expired',
                failure_kind = 'INFRASTRUCTURE',
                outcome_uncertain = TRUE,
                worker_id = NULL,
                lease_token = NULL,
                heartbeat_at = NULL,
                lease_expires_at = NULL,
                finished_at = NOW(),
                updated_at = NOW()
            WHERE kid = %(task_id)s
              AND status = 'running'
              AND execution_mode = 'BACKGROUND'
              AND lease_expires_at <= clock_timestamp()
            RETURNING *
            """,
            {"task_id": task_id},
        )
        return await cursor.fetchone()

    async def list_tasks(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        task_id: int | None = None,
        fs_entry_id: int | None = None,
        batch_id: str | None = None,
        statuses: Sequence[str] | None = None,
        latest_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List Build tasks with the unified status API's filters."""
        self._validate_page(limit=limit, offset=offset)
        conditions, params = self._filters(
            knowledge_base_id=knowledge_base_id,
            task_id=task_id,
            fs_entry_id=fs_entry_id,
            batch_id=batch_id,
        )
        status_condition = self._status_condition(statuses, params)
        await cursor.execute(
            f"""
            WITH ranked_tasks AS (
                SELECT
                    task.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY task.fs_entry_id
                        ORDER BY task.created_at DESC, task.kid DESC
                    ) AS task_rank
                FROM knowledge_build_task task
                WHERE {" AND ".join(conditions)}
            )
            SELECT *
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

    async def count_tasks(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        task_id: int | None = None,
        fs_entry_id: int | None = None,
        batch_id: str | None = None,
        statuses: Sequence[str] | None = None,
        latest_only: bool = True,
    ) -> int:
        conditions, params = self._filters(
            knowledge_base_id=knowledge_base_id,
            task_id=task_id,
            fs_entry_id=fs_entry_id,
            batch_id=batch_id,
        )
        status_condition = self._status_condition(statuses, params)
        await cursor.execute(
            f"""
            WITH ranked_tasks AS (
                SELECT
                    task.status,
                    ROW_NUMBER() OVER (
                        PARTITION BY task.fs_entry_id
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

    async def _create_protocol_task(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        batch_id: str | None,
        origin: str,
        execution_mode: str,
        parent_semantic_task_id: int | None,
        file_path_snapshot: str,
        input_checksum: str,
        input_is_deleted: bool,
        build_profile: Mapping[str, Any],
        build_profile_hash: str,
        status: str,
        current_stage: str,
        priority: int,
    ) -> dict[str, Any] | None:
        normalized_status = self._normalize_status(status)
        normalized_stage = self._normalize_stage(current_stage)
        if not file_path_snapshot:
            raise ValueError("file_path_snapshot must not be empty")
        if not input_checksum:
            raise ValueError("input_checksum must not be empty")
        if len(build_profile_hash) != 64:
            raise ValueError("build_profile_hash must contain 64 characters")
        await cursor.execute(
            """
            INSERT INTO knowledge_build_task (
                knowledge_base_id,
                fs_entry_id,
                batch_id,
                origin,
                execution_mode,
                parent_semantic_task_id,
                file_path_snapshot,
                input_checksum,
                input_is_deleted,
                build_profile,
                build_profile_hash,
                status,
                current_step,
                current_stage,
                progress,
                priority,
                outcome_uncertain,
                started_at,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                %(fs_entry_id)s,
                %(batch_id)s,
                %(origin)s,
                %(execution_mode)s,
                %(parent_semantic_task_id)s,
                %(file_path_snapshot)s,
                %(input_checksum)s,
                %(input_is_deleted)s,
                %(build_profile)s::jsonb,
                %(build_profile_hash)s,
                %(status)s::varchar(32),
                %(current_step)s,
                %(current_stage)s,
                %(progress)s,
                %(priority)s,
                false,
                CASE
                    WHEN %(status)s::varchar(32) = 'running'::varchar(32)
                        THEN NOW()
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
                "batch_id": batch_id,
                "origin": origin,
                "execution_mode": execution_mode,
                "parent_semantic_task_id": parent_semantic_task_id,
                "file_path_snapshot": file_path_snapshot,
                "input_checksum": input_checksum,
                "input_is_deleted": input_is_deleted,
                "build_profile": self._json_value(build_profile),
                "build_profile_hash": build_profile_hash,
                "status": normalized_status,
                "current_step": _STAGE_TO_LEGACY_STEP[normalized_stage],
                "current_stage": normalized_stage,
                "progress": _STAGE_PROGRESS[normalized_stage],
                "priority": priority,
            },
        )
        return await cursor.fetchone()

    @staticmethod
    def _normalize_status(status: str, *, allow_legacy: bool = False) -> str:
        normalized = status.strip().lower()
        allowed = FILE_BUILD_TASK_STATUSES
        if allow_legacy:
            allowed = allowed | LEGACY_BUILD_TASK_STATUSES
        if normalized not in allowed:
            raise ValueError(f"unsupported file build task status: {status}")
        return normalized

    @staticmethod
    def _normalize_stage(stage: str) -> str:
        normalized = stage.strip().lower()
        normalized = _LEGACY_STEP_TO_STAGE.get(normalized, normalized)
        if normalized not in FILE_BUILD_STAGES:
            raise ValueError(f"unsupported file build stage: {stage}")
        return normalized

    @staticmethod
    def _json_value(value: Mapping[str, Any] | None) -> str | None:
        if value is None:
            return None
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _truncate_error(value: str | None, *, limit: int = 2000) -> str | None:
        if value is None:
            return None
        return value[:limit]

    def _filters(
        self,
        *,
        knowledge_base_id: int,
        task_id: int | None,
        fs_entry_id: int | None,
        batch_id: str | None,
    ) -> tuple[list[str], dict[str, Any]]:
        conditions = ["task.knowledge_base_id = %(knowledge_base_id)s"]
        params: dict[str, Any] = {"knowledge_base_id": knowledge_base_id}
        if task_id is not None:
            conditions.append("task.kid = %(task_id)s")
            params["task_id"] = task_id
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

    @staticmethod
    def _validate_page(*, limit: int, offset: int) -> None:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must be greater than or equal to 0")
