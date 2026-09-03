"""Independent database-backed runner for File Build tasks."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from by_qa.core import logger
from by_qa.knowledge_base.services.file_build_execution_service import (
    FileBuildLeaseLostError,
    FileBuildTerminalError,
)


@dataclass(slots=True)
class FileBuildBackgroundRunner:
    """Claim only Build work and enforce one process-local concurrency limit."""

    connection_factory: Any
    task_repository: Any
    batch_repository: Any
    execution_service: Any
    worker_id: str
    concurrency: int = 16
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

    async def start(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name="file-build-runner"
        )
        self._reaper_task = asyncio.create_task(
            self._reaper_loop(), name="file-build-lease-reaper"
        )
        self._status_task = asyncio.create_task(
            self._status_loop(), name="file-build-worker-status"
        )
        logger.info(
            "file_build runner started: worker_id=%s concurrency=%s",
            self.worker_id,
            self.concurrency,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        loops = [
            task
            for task in (self._poll_task, self._reaper_task, self._status_task)
            if task is not None
        ]
        if loops:
            await asyncio.gather(*loops, return_exceptions=True)
        if self._active_tasks:
            _, pending = await asyncio.wait(
                self._active_tasks, timeout=self.shutdown_grace_seconds
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._poll_task = self._reaper_task = self._status_task = None
        logger.info("file_build runner stopped: worker_id=%s", self.worker_id)

    async def run_claim_cycle(self) -> int:
        claimed_count = 0
        while (
            not self._stop_event.is_set() and len(self._active_tasks) < self.concurrency
        ):
            claimed = await self._claim_one()
            if claimed is None:
                break
            execution = asyncio.create_task(
                self._execute_claimed(claimed),
                name=f"file-build-task-{claimed['kid']}",
            )
            self._active_tasks.add(execution)
            execution.add_done_callback(self._task_done)
            claimed_count += 1
        return claimed_count

    async def run_reaper_cycle(self, *, limit: int = 100) -> int:
        reaped = 0
        while reaped < limit:
            if await self.execution_service.reap_one_expired() is None:
                break
            reaped += 1
        return reaped

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
            if row is not None and row.get("batch_id") is not None:
                await self.batch_repository.mark_processing(
                    cursor, batch_id=str(row["batch_id"])
                )
            await connection.commit()
            return row
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def _execute_claimed(self, row: dict[str, Any]) -> None:
        task_id = int(row["kid"])
        lease_token = str(row["lease_token"])
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(task_id, lease_token, lease_lost),
            name=f"file-build-heartbeat-{task_id}",
        )
        worker_task = asyncio.create_task(
            self.execution_service.execute_claimed(row),
            name=f"file-build-execution-{task_id}",
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
                await self.execution_service.finish_claimed(
                    row,
                    status="failed",
                    error_code="TASK_TIMEOUT",
                    error_message=(
                        f"task execution exceeded {self.task_timeout_seconds:g} seconds"
                    ),
                    failure_kind="INFRASTRUCTURE",
                    outcome_uncertain=True,
                )
                return
            if worker_task in done:
                lost_wait.cancel()
                with suppress(asyncio.CancelledError):
                    await lost_wait
                await worker_task
                return
            if lease_lost.is_set():
                worker_task.cancel()
                with suppress(asyncio.CancelledError):
                    await worker_task
                return
        except FileBuildLeaseLostError:
            return
        except FileBuildTerminalError as exc:
            await self.execution_service.finish_claimed(
                row,
                status=exc.status,
                error_code=exc.error_code,
                error_message=str(exc),
                failure_kind=exc.failure_kind,
                outcome_uncertain=exc.outcome_uncertain,
            )
        except asyncio.CancelledError:
            worker_task.cancel()
            raise
        except Exception as exc:
            logger.exception("file_build task execution failed: task_id=%s", task_id)
            await self.execution_service.finish_claimed(
                row,
                status="failed",
                error_code="BUILD_FAILED",
                error_message=str(exc) or "internal error",
                failure_kind="INFRASTRUCTURE",
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
                logger.exception("file_build heartbeat failed: task_id=%s", task_id)
            finally:
                await connection.close()

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                claimed = await self.run_claim_cycle()
            except Exception:
                logger.exception("file_build runner claim cycle failed")
                claimed = 0
            if claimed:
                await asyncio.sleep(0)
            else:
                await self._wait_or_stop(self.poll_seconds)

    async def _reaper_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_reaper_cycle()
            except Exception:
                logger.exception("file_build lease reaper cycle failed")
            await self._wait_or_stop(self.reaper_seconds)

    async def _status_loop(self) -> None:
        while not self._stop_event.is_set():
            await self._wait_or_stop(self.status_log_seconds)
            if not self._stop_event.is_set():
                self.log_status()

    def log_status(self) -> None:
        active = [task for task in self._active_tasks if not task.done()]
        logger.info(
            "file_build worker status: worker_id=%s concurrency=%s "
            "active_tasks=%s available_slots=%s",
            self.worker_id,
            self.concurrency,
            len(active),
            max(0, self.concurrency - len(active)),
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
                "file_build background task failed: error_type=%s",
                type(failure).__name__,
                exc_info=(type(failure), failure, failure.__traceback__),
            )
