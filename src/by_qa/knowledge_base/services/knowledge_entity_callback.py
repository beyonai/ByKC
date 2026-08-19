"""Callback protocol for terminal KnowledgeEntity processing events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from importlib import import_module
from os import getenv
from typing import Any, Protocol, runtime_checkable

from by_qa.core import logger
from by_qa.knowledge_base.api.knowledge_entity_schemas import (
    ProcessingTaskStatus,
    ProcessingTaskType,
)


@dataclass(frozen=True, slots=True)
class BatchProgress:
    version: int
    total_count: int
    completed_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int


@dataclass(frozen=True, slots=True)
class ProcessingError:
    code: str
    message: str
    failure_kind: str | None
    outcome_uncertain: bool


@dataclass(frozen=True, slots=True)
class FileCompletedCallbackInput:
    batch_id: str
    task_id: str
    task_type: ProcessingTaskType
    status: ProcessingTaskStatus
    knowledge_base_id: str
    kb_code: str
    file_id: str | None
    file_path: str
    progress: BatchProgress
    result: Mapping[str, Any] | None
    error: ProcessingError | None
    extra_params: Mapping[str, Any] = field(default_factory=dict)
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BatchCompletedCallbackInput:
    batch_id: str
    task_type: ProcessingTaskType
    knowledge_base_id: str
    kb_code: str
    progress: BatchProgress
    extra_params: Mapping[str, Any] = field(default_factory=dict)
    completed_at: datetime | None = None


@runtime_checkable
class KnowledgeEntityProcessingCallback(Protocol):
    async def on_file_completed(self, event: FileCompletedCallbackInput) -> None: ...

    async def on_batch_completed(self, event: BatchCompletedCallbackInput) -> None: ...


class NoopKnowledgeEntityProcessingCallback:
    async def on_file_completed(self, event: FileCompletedCallbackInput) -> None:
        del event

    async def on_batch_completed(self, event: BatchCompletedCallbackInput) -> None:
        del event


def load_knowledge_entity_processing_callback() -> KnowledgeEntityProcessingCallback:
    """Load an injected callback provider, defaulting to a no-op implementation."""
    provider_path = (
        getenv("KNOWLEDGE_ENTITY_CALLBACK_PROVIDER", "")
        or getenv("BY_QA_KNOWLEDGE_ENTITY_CALLBACK_PROVIDER", "")
    ).strip()
    if not provider_path:
        return NoopKnowledgeEntityProcessingCallback()
    module_name, separator, attribute_name = provider_path.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError(
            "KNOWLEDGE_ENTITY_CALLBACK_PROVIDER must use the 'module:attribute' format."
        )
    module = import_module(module_name)
    factory = getattr(module, attribute_name)
    callback = factory() if callable(factory) else factory
    if not isinstance(callback, KnowledgeEntityProcessingCallback):
        raise TypeError(
            "KNOWLEDGE_ENTITY_CALLBACK_PROVIDER must resolve to a "
            "KnowledgeEntityProcessingCallback."
        )
    return callback


def build_batch_progress(
    batch: Mapping[str, Any], counts: Mapping[str, int]
) -> BatchProgress:
    return BatchProgress(
        version=int(batch.get("version") or 0),
        total_count=int(batch.get("total_count") or 0),
        completed_count=int(batch.get("completed_count") or 0),
        succeeded_count=int(counts.get("succeeded", 0)),
        failed_count=int(counts.get("failed", 0)),
        skipped_count=int(counts.get("skipped", 0)),
    )


def json_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, Mapping):
            return dict(decoded)
    raise TypeError(f"expected JSON object, got {type(value).__name__}")


@dataclass(slots=True)
class KnowledgeEntityCallbackInvoker:
    """Invoke a provider without allowing its failures to affect task state."""

    callback: KnowledgeEntityProcessingCallback = field(
        default_factory=NoopKnowledgeEntityProcessingCallback
    )

    async def file_completed(self, event: FileCompletedCallbackInput) -> None:
        try:
            await self.callback.on_file_completed(event)
        except Exception as exc:
            logger.warning(
                "knowledge_entity callback failed: method=on_file_completed batch_id=%s task_id=%s error_type=%s",
                event.batch_id,
                event.task_id,
                type(exc).__name__,
            )

    async def batch_completed(self, event: BatchCompletedCallbackInput) -> None:
        try:
            await self.callback.on_batch_completed(event)
        except Exception as exc:
            logger.warning(
                "knowledge_entity callback failed: method=on_batch_completed batch_id=%s error_type=%s",
                event.batch_id,
                type(exc).__name__,
            )


async def invoke_terminal_callbacks(
    invoker: KnowledgeEntityCallbackInvoker,
    task: Mapping[str, Any],
    batch: Mapping[str, Any],
    counts: Mapping[str, int],
) -> None:
    """Build stable Protocol inputs from committed terminal rows."""
    progress = build_batch_progress(batch, counts)
    status = ProcessingTaskStatus(str(task["status"]).upper())
    error = None
    if status == ProcessingTaskStatus.FAILED:
        error = ProcessingError(
            code=str(task.get("error_code") or "PROCESSING_FAILED"),
            message=str(task.get("error_message") or "processing failed"),
            failure_kind=task.get("failure_kind"),
            outcome_uncertain=bool(task.get("outcome_uncertain")),
        )
    await invoker.file_completed(
        FileCompletedCallbackInput(
            batch_id=str(task["batch_id"]),
            task_id=str(task["kid"]),
            task_type=ProcessingTaskType(str(task["task_type"])),
            status=status,
            knowledge_base_id=str(task["knowledge_base_id"]),
            kb_code=str(task["knowledge_base_id"]),
            file_id=str(task["fs_entry_id"])
            if task.get("fs_entry_id") is not None
            else None,
            file_path=str(task["file_path_snapshot"]),
            progress=progress,
            result=json_mapping(task.get("result_payload")),
            error=error,
            extra_params=json_mapping(task.get("extra_params")) or {},
            completed_at=task.get("finished_at"),
        )
    )
    if str(batch["status"]) == "completed":
        await invoker.batch_completed(
            BatchCompletedCallbackInput(
                batch_id=str(batch["batch_id"]),
                task_type=ProcessingTaskType(str(batch["task_type"])),
                knowledge_base_id=str(batch["knowledge_base_id"]),
                kb_code=str(batch["knowledge_base_id"]),
                progress=progress,
                extra_params=json_mapping(batch.get("extra_params")) or {},
                completed_at=batch.get("completed_at"),
            )
        )
