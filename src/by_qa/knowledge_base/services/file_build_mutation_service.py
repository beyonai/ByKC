"""Atomically terminate File Build work affected by resource mutations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(slots=True)
class FileBuildMutationService:
    task_repository: Any
    batch_repository: Any
    terminal_event_service: Any | None = None

    async def terminate_active(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        error_code: str,
        error_message: str,
        fs_entry_ids: Sequence[int] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        tasks = await self.task_repository.terminate_active_tasks(
            cursor,
            knowledge_base_id=knowledge_base_id,
            error_code=error_code,
            error_message=error_message,
            fs_entry_ids=fs_entry_ids,
        )
        batch_counts = Counter(
            str(task["batch_id"]) for task in tasks if task.get("batch_id") is not None
        )
        completed_batch_ids: list[str] = []
        for batch_id, count in batch_counts.items():
            batch = await self.batch_repository.advance_batch(
                cursor, batch_id=batch_id, completed_delta=count
            )
            if batch is None:
                raise RuntimeError(f"failed to advance Build batch: {batch_id}")
            if str(batch["status"]) == "completed":
                completed_batch_ids.append(batch_id)
        return tasks, completed_batch_ids

    async def publish(
        self,
        tasks: Sequence[dict[str, Any]],
        completed_batch_ids: Sequence[str],
    ) -> None:
        if self.terminal_event_service is not None:
            await self.terminal_event_service.publish_tasks(
                tasks, completed_batch_ids=completed_batch_ids
            )
