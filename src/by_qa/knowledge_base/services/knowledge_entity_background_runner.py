"""Database-backed runner for KnowledgeEntity discovery and enrichment tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from by_qa.core import logger
from by_qa.knowledge_base.services.knowledge_entity_callback import (
    KnowledgeEntityCallbackInvoker,
    invoke_terminal_callbacks,
    json_mapping,
)
from by_qa.knowledge_base.services.knowledge_entity_task_worker import (
    KnowledgeEntityTaskContext,
)


@dataclass(slots=True)
class KnowledgeEntityBackgroundRunner:
    connection_factory: Any
    task_repository: Any
    batch_repository: Any
    worker: Any
    callback_invoker: KnowledgeEntityCallbackInvoker
    worker_id: str
    concurrency: int = 4
    poll_seconds: float = 3.0
    task_timeout_seconds: float = 1200.0
    lease_seconds: int = 180
    heartbeat_seconds: float = 30.0
    reaper_seconds: float = 30.0
    status_log_seconds: float = 60.0
    shutdown_grace_seconds: float = 60.0
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _active_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _poll_task: asyncio.Task[None] | None = field(default=None, init=False)
    _reaper_task: asyncio.Task[None] | None = field(default=None, init=False)
    _status_task: asyncio.Task[None] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be greater than 0")
        if self.task_timeout_seconds <= 0:
            raise ValueError("task_timeout_seconds must be greater than 0")
        if not 0 < self.heartbeat_seconds < self.lease_seconds:
            raise ValueError("heartbeat_seconds must be between 0 and lease_seconds")
        if self.status_log_seconds <= 0:
            raise ValueError("status_log_seconds must be greater than 0")

    async def start(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name="knowledge-entity-runner"
        )
        self._reaper_task = asyncio.create_task(
            self._reaper_loop(), name="knowledge-entity-lease-reaper"
        )
        self._status_task = asyncio.create_task(
            self._status_loop(), name="knowledge-entity-worker-status"
        )
        logger.info(
            "knowledge_entity background runner started: worker_id=%s concurrency=%s",
            self.worker_id,
            self.concurrency,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        loops = [
            task
            for task in (self._poll_task, self._reaper_task, self._status_task)
            if task
        ]
        if loops:
            await asyncio.gather(*loops, return_exceptions=True)
        if self._active_tasks:
            _, pending = await asyncio.wait(
                self._active_tasks,
                timeout=self.shutdown_grace_seconds,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._poll_task = None
        self._reaper_task = None
        self._status_task = None
        logger.info(
            "knowledge_entity background runner stopped: worker_id=%s",
            self.worker_id,
        )

    async def run_claim_cycle(self) -> int:
        """Fill currently available execution slots and return claimed count."""
        claimed_count = 0
        while (
            not self._stop_event.is_set() and len(self._active_tasks) < self.concurrency
        ):
            claimed = await self._claim_one()
            if claimed is None:
                break
            execution = asyncio.create_task(
                self._execute_claimed(claimed),
                name=f"knowledge-entity-task-{claimed['kid']}",
            )
            self._active_tasks.add(execution)
            execution.add_done_callback(self._task_done)
            claimed_count += 1
        return claimed_count

    async def run_reaper_cycle(self, *, limit: int = 100) -> int:
        """Fail expired leases without putting their tasks back to pending."""
        reaped = 0
        while reaped < limit:
            connection = await self.connection_factory()
            terminal: tuple[dict[str, Any], dict[str, Any], dict[str, int]] | None = (
                None
            )
            try:
                cursor = connection.cursor()
                expired = await self.task_repository.lock_next_expired_task(cursor)
                if expired is None:
                    await connection.rollback()
                    break
                task = await self.task_repository.fail_locked_expired_task(
                    cursor, task_id=int(expired["kid"])
                )
                if task is None:
                    await connection.rollback()
                    continue
                batch = await self.batch_repository.advance_batch(
                    cursor, batch_id=str(task["batch_id"])
                )
                if batch is None:
                    raise RuntimeError("failed to advance batch for expired task")
                counts = await self.batch_repository.count_tasks_by_status(
                    cursor, batch_id=str(task["batch_id"])
                )
                terminal = (task, batch, counts)
                await connection.commit()
            except Exception:
                await connection.rollback()
                logger.exception("knowledge_entity lease reaper cycle failed")
                raise
            finally:
                await connection.close()
            if terminal is not None:
                await invoke_terminal_callbacks(self.callback_invoker, *terminal)
                reaped += 1
        return reaped

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                claimed = await self.run_claim_cycle()
            except Exception:
                logger.exception("knowledge_entity runner claim cycle failed")
                claimed = 0
            if claimed:
                await asyncio.sleep(0)
                continue
            await self._wait_or_stop(self.poll_seconds)

    async def _reaper_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_reaper_cycle()
            except Exception:
                pass
            await self._wait_or_stop(self.reaper_seconds)

    async def _status_loop(self) -> None:
        while not self._stop_event.is_set():
            await self._wait_or_stop(self.status_log_seconds)
            if self._stop_event.is_set():
                break
            self.log_status()

    def log_status(self) -> None:
        """Emit a periodic liveness snapshot for this runner process."""
        active = sorted(
            task.get_name().removeprefix("knowledge-entity-task-")
            for task in self._active_tasks
            if not task.done()
        )
        active_count = len(active)
        logger.info(
            "knowledge_entity worker status: worker_id=%s status=alive "
            "concurrency=%s active_tasks=%s available_slots=%s "
            "active_task_ids=%s poll_loop_alive=%s reaper_loop_alive=%s",
            self.worker_id,
            self.concurrency,
            active_count,
            max(0, self.concurrency - active_count),
            ",".join(active) or "-",
            self._poll_task is not None and not self._poll_task.done(),
            self._reaper_task is not None and not self._reaper_task.done(),
        )

    async def _claim_one(self) -> dict[str, Any] | None:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            row = await self.task_repository.claim_next_task(
                cursor,
                worker_id=self.worker_id,
                lease_token=uuid4().hex,
                lease_seconds=self.lease_seconds,
            )
            await connection.commit()
            return row
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def _execute_claimed(self, row: Mapping[str, Any]) -> None:
        task_id = int(row["kid"])
        lease_token = str(row["lease_token"])
        try:
            context = self._task_context(row)
        except Exception as exc:
            await self._finish_claimed(
                row,
                lease_token=lease_token,
                status="failed",
                error_code="TASK_INPUT_UNAVAILABLE",
                error_message=str(exc),
                failure_kind="INPUT",
            )
            return
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(task_id, lease_token, lease_lost),
            name=f"knowledge-entity-heartbeat-{task_id}",
        )
        worker_task = asyncio.create_task(
            self.worker.run_task(context),
            name=f"knowledge-entity-worker-{task_id}",
        )
        lost_wait = asyncio.create_task(lease_lost.wait())
        try:
            done, _ = await asyncio.wait(
                {worker_task, lost_wait},
                timeout=self.task_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                worker_task.cancel()
                with suppress(asyncio.CancelledError):
                    await worker_task
                await self._finish_claimed(
                    row,
                    lease_token=lease_token,
                    status="failed",
                    error_code="TASK_TIMEOUT",
                    error_message=(
                        f"task execution exceeded {self.task_timeout_seconds:g} seconds"
                    ),
                    failure_kind="INFRASTRUCTURE",
                    outcome_uncertain=True,
                )
                return
            if lost_wait in done and lease_lost.is_set():
                worker_task.cancel()
                with suppress(asyncio.CancelledError):
                    await worker_task
                return
            lost_wait.cancel()
            with suppress(asyncio.CancelledError):
                await lost_wait
            result = await worker_task
            if isinstance(result, Mapping):
                payload = result.get("result_payload", result.get("resultPayload"))
                index_version = result.get("index_version", result.get("indexVersion"))
            else:
                payload = getattr(result, "result_payload", None)
                index_version = getattr(result, "index_version", None)
            await self._finish_claimed(
                row,
                lease_token=lease_token,
                status="succeeded",
                result_payload=payload,
                index_version=index_version,
            )
        except asyncio.CancelledError:
            worker_task.cancel()
            raise
        except Exception as exc:
            await self._finish_claimed(
                row,
                lease_token=lease_token,
                status="failed",
                error_code=str(getattr(exc, "error_code", "PROCESSING_FAILED")),
                error_message=str(exc),
                failure_kind=str(getattr(exc, "failure_kind", "INFRASTRUCTURE")),
                outcome_uncertain=bool(getattr(exc, "outcome_uncertain", False)),
            )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            if not lost_wait.done():
                lost_wait.cancel()

    async def _heartbeat(
        self, task_id: int, lease_token: str, lease_lost: asyncio.Event
    ) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            connection = await self.connection_factory()
            try:
                cursor = connection.cursor()
                refreshed = await self.task_repository.refresh_lease(
                    cursor,
                    task_id=task_id,
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    lease_seconds=self.lease_seconds,
                )
                await connection.commit()
                if not refreshed:
                    lease_lost.set()
                    return
            except Exception:
                await connection.rollback()
                logger.exception(
                    "knowledge_entity heartbeat failed: task_id=%s worker_id=%s",
                    task_id,
                    self.worker_id,
                )
            finally:
                await connection.close()

    async def _finish_claimed(
        self,
        row: Mapping[str, Any],
        *,
        lease_token: str,
        status: str,
        result_payload: Mapping[str, Any] | None = None,
        index_version: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        failure_kind: str | None = None,
        outcome_uncertain: bool = False,
    ) -> None:
        terminal: tuple[dict[str, Any], dict[str, Any], dict[str, int]] | None = None
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            task = await self.task_repository.finish_claimed_task(
                cursor,
                task_id=int(row["kid"]),
                lease_token=lease_token,
                status=status,
                current_stage="completed" if status == "succeeded" else "failed",
                index_version=index_version,
                result_payload=result_payload,
                error_code=error_code,
                error_message=error_message,
                failure_kind=failure_kind,
                outcome_uncertain=outcome_uncertain,
            )
            if task is None:
                await connection.rollback()
                return
            batch = await self.batch_repository.advance_batch(
                cursor, batch_id=str(task["batch_id"])
            )
            if batch is None:
                raise RuntimeError("failed to advance batch for terminal task")
            counts = await self.batch_repository.count_tasks_by_status(
                cursor, batch_id=str(task["batch_id"])
            )
            terminal = (task, batch, counts)
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()
        if terminal is not None:
            await invoke_terminal_callbacks(self.callback_invoker, *terminal)

    def _task_context(self, row: Mapping[str, Any]) -> KnowledgeEntityTaskContext:
        if row.get("fs_entry_id") is None:
            raise ValueError("source file was deleted before task execution")
        return KnowledgeEntityTaskContext(
            task_id=int(row["kid"]),
            task_type=str(row["task_type"]),
            kb_code=str(row["knowledge_base_id"]),
            knowledge_base_id=int(row["knowledge_base_id"]),
            source_file_id=int(row["fs_entry_id"]),
            file_path=str(row["file_path_snapshot"]),
            input_fingerprint=row.get("input_fingerprint"),
            input_checksum=row.get("input_checksum"),
            request_params=json_mapping(row.get("request_params")) or {},
            batch_id=str(row["batch_id"]),
        )

    async def _wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except TimeoutError:
            pass

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._active_tasks.discard(task)
        if task.cancelled():
            return
        failure = task.exception()
        if failure is not None:
            logger.error(
                "knowledge_entity background task failed: error_type=%s",
                type(failure).__name__,
                exc_info=(type(failure), failure, failure.__traceback__),
            )
