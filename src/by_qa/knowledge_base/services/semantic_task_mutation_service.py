"""Terminate semantic work atomically when its knowledge base is deleted."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

from by_qa.core import logger
from by_qa.knowledge_base.events import build_semantic_terminal_events


@dataclass(slots=True)
class SemanticTaskMutationService:
    task_repository: Any
    batch_repository: Any
    event_publisher_invoker: Any | None = None

    async def terminate_for_knowledge_base(
        self, cursor: Any, *, knowledge_base_id: int
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[dict[str, Any], dict[str, int]]]]:
        tasks = await self.task_repository.terminate_active_for_knowledge_base(
            cursor, knowledge_base_id=knowledge_base_id
        )
        batch_counts = Counter(str(task["batch_id"]) for task in tasks)
        completed: dict[str, tuple[dict[str, Any], dict[str, int]]] = {}
        for batch_id, count in batch_counts.items():
            batch = await self.batch_repository.advance_batch(
                cursor, batch_id=batch_id, completed_delta=count
            )
            if batch is None:
                raise RuntimeError(f"failed to advance semantic batch: {batch_id}")
            counts = await self.batch_repository.count_tasks_by_status(
                cursor, batch_id=batch_id
            )
            if str(batch["status"]) == "completed":
                completed[batch_id] = (batch, counts)
        return tasks, completed

    async def publish(
        self,
        tasks: Sequence[dict[str, Any]],
        completed: dict[str, tuple[dict[str, Any], dict[str, int]]],
    ) -> None:
        if self.event_publisher_invoker is None:
            return
        first_task_by_batch: dict[str, dict[str, Any]] = {}
        for task in tasks:
            try:
                batch_id = str(task["batch_id"])
                first_task_by_batch.setdefault(batch_id, task)
                batch_and_counts = completed.get(batch_id)
                if batch_and_counts is None:
                    continue
                batch, counts = batch_and_counts
                events = build_semantic_terminal_events(task, batch, counts)
                await self.event_publisher_invoker.publish(events[0])
            except Exception:
                logger.warning(
                    "semantic mutation task event preparation failed", exc_info=True
                )
        for batch_id, (batch, counts) in completed.items():
            try:
                task = first_task_by_batch[batch_id]
                events = build_semantic_terminal_events(task, batch, counts)
                if len(events) > 1:
                    await self.event_publisher_invoker.publish(events[1])
            except Exception:
                logger.warning(
                    "semantic mutation batch event preparation failed: batch_id=%s",
                    batch_id,
                    exc_info=True,
                )
