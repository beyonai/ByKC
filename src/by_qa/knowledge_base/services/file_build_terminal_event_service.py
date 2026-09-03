"""Publish committed external File Build terminal events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from by_qa.core import logger
from by_qa.knowledge_base.events import (
    build_file_build_batch_terminal_event,
    build_file_build_terminal_event,
)


@dataclass(slots=True)
class FileBuildTerminalEventService:
    """Load committed batch aggregates and publish best-effort callbacks."""

    connection_factory: Any
    batch_repository: Any
    event_publisher_invoker: Any

    async def publish_tasks(
        self,
        tasks: Iterable[Mapping[str, Any]],
        *,
        completed_batch_ids: Iterable[str] = (),
    ) -> None:
        try:
            external_tasks = [
                dict(task)
                for task in tasks
                if task.get("batch_id") is not None
                and str(task.get("origin") or "").upper() == "API"
                and str(task.get("execution_mode") or "").upper() == "BACKGROUND"
            ]
            if not external_tasks:
                return
            for task in external_tasks:
                await self.event_publisher_invoker.publish(
                    build_file_build_terminal_event(task)
                )
            for batch_id in dict.fromkeys(completed_batch_ids):
                await self.publish_completed_batch(batch_id)
        except Exception:
            logger.warning(
                "file Build terminal event preparation failed", exc_info=True
            )

    async def publish_completed_batch(self, batch_id: str) -> None:
        try:
            connection = await self.connection_factory()
            try:
                cursor = connection.cursor()
                batch = await self.batch_repository.get_batch(cursor, batch_id=batch_id)
                if batch is None or str(batch["status"]) != "completed":
                    raise RuntimeError(f"Build batch is not completed: {batch_id}")
                counts = await self.batch_repository.count_tasks_by_status(
                    cursor, batch_id=batch_id
                )
            finally:
                await connection.close()
            await self.event_publisher_invoker.publish(
                build_file_build_batch_terminal_event(batch, counts)
            )
        except Exception:
            logger.warning(
                "file Build batch event preparation failed: batch_id=%s",
                batch_id,
                exc_info=True,
            )
