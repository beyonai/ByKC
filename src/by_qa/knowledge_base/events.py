"""Strict, unified best-effort knowledge event publishing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from importlib import import_module
from os import getenv
from typing import Annotated, Any, Literal, Protocol, TypeAlias, runtime_checkable
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)

from by_qa.core import logger

EVENT_VERSION = 1


class ResourceEventType(StrEnum):
    DIRECTORY_CREATED = "resource.directory.created"
    DIRECTORY_UPDATED = "resource.directory.updated"
    DIRECTORY_DELETED = "resource.directory.deleted"
    FILE_IMPORTED = "resource.file.imported"
    FILE_UPDATED = "resource.file.updated"
    FILE_DELETED = "resource.file.deleted"
    RESOURCE_MOVED = "resource.moved"


class SemanticEventType(StrEnum):
    DISCOVERY_FILE_COMPLETED = "semantic.discovery.file.completed"
    DISCOVERY_BATCH_COMPLETED = "semantic.discovery.batch.completed"
    ENRICH_FILE_COMPLETED = "semantic.enrich.file.completed"
    ENRICH_BATCH_COMPLETED = "semantic.enrich.batch.completed"


class BuildEventType(StrEnum):
    FILE_COMPLETED = "build.file.completed"


class _StrictEventModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, strict=True
    )


class MutationSummary(_StrictEventModel):
    total: int
    succeeded: int
    failed: int


class MutationOperationResult(_StrictEventModel):
    success: bool


class ImportMutationItem(_StrictEventModel):
    source_path: str = Field(alias="sourcePath")
    target_path: str | None = Field(alias="targetPath")
    resource_type: Literal["file"] = Field(default="file", alias="resourceType")
    success: bool
    error: str | None = None


class MoveMutationItem(_StrictEventModel):
    source_path: str = Field(alias="sourcePath")
    target_path: str | None = Field(alias="targetPath")
    success: bool
    error: str | None = None


class DirectoryCreatedPayload(_StrictEventModel):
    resource_type: Literal["directory"] = Field(
        default="directory", alias="resourceType"
    )
    target_path: str = Field(alias="targetPath")


class DirectoryUpdatedPayload(_StrictEventModel):
    resource_type: Literal["directory"] = Field(
        default="directory", alias="resourceType"
    )
    source_path: str = Field(alias="sourcePath")
    target_path: str = Field(alias="targetPath")


class DirectoryDeletedPayload(_StrictEventModel):
    resource_type: Literal["directory"] = Field(
        default="directory", alias="resourceType"
    )
    source_path: str = Field(alias="sourcePath")


class FileImportedPayload(_StrictEventModel):
    resource_type: Literal["file"] = Field(default="file", alias="resourceType")
    target_path: str = Field(alias="targetPath")
    items: tuple[ImportMutationItem, ...] = ()
    result: MutationSummary


class FileUpdatedPayload(_StrictEventModel):
    resource_type: Literal["file"] = Field(default="file", alias="resourceType")
    source_path: str = Field(alias="sourcePath")
    target_path: str = Field(alias="targetPath")
    result: MutationOperationResult


class FileDeletedPayload(_StrictEventModel):
    resource_type: Literal["file"] = Field(default="file", alias="resourceType")
    source_path: str = Field(alias="sourcePath")


class ResourceMovedPayload(_StrictEventModel):
    resource_type: Literal["mixed"] = Field(default="mixed", alias="resourceType")
    source_path: str | None = Field(alias="sourcePath")
    target_path: str = Field(alias="targetPath")
    items: tuple[MoveMutationItem, ...]
    result: MutationSummary


class SemanticBatchProgress(_StrictEventModel):
    version: int
    total_count: int = Field(alias="totalCount")
    completed_count: int = Field(alias="completedCount")
    succeeded_count: int = Field(alias="succeededCount")
    failed_count: int = Field(alias="failedCount")
    skipped_count: int = Field(alias="skippedCount")


class SemanticProcessingError(_StrictEventModel):
    code: str
    message: str
    failure_kind: str | None = Field(alias="failureKind")
    outcome_uncertain: bool = Field(alias="outcomeUncertain")


class SemanticFileCompletedPayload(_StrictEventModel):
    batch_id: str = Field(alias="batchId")
    task_id: str = Field(alias="taskId")
    task_type: str = Field(alias="taskType")
    status: Literal["SUCCEEDED", "FAILED", "SKIPPED"]
    knowledge_base_id: str = Field(alias="knowledgeBaseId")
    file_id: str | None = Field(alias="fileId")
    file_path: str = Field(alias="filePath")
    progress: SemanticBatchProgress
    result: dict[str, JsonValue] | None
    error: SemanticProcessingError | None


class SemanticBatchCompletedPayload(_StrictEventModel):
    batch_id: str = Field(alias="batchId")
    task_type: str = Field(alias="taskType")
    knowledge_base_id: str = Field(alias="knowledgeBaseId")
    progress: SemanticBatchProgress


class BuildFileCompletedResult(_StrictEventModel):
    chunk_count: int = Field(ge=0, alias="chunkCount")
    line_count: int = Field(ge=0, alias="lineCount")


class BuildProcessingError(_StrictEventModel):
    code: str
    message: str


class BuildFileCompletedPayload(_StrictEventModel):
    task_id: str = Field(alias="taskId")
    status: Literal["complete", "failed", "unsupported"]
    file_path: str = Field(alias="filePath")
    current_step: Literal["markdown", "chunking", "vectorizing", "complete"] = Field(
        alias="currentStep"
    )
    result: BuildFileCompletedResult | None
    error: BuildProcessingError | None

    @model_validator(mode="after")
    def _validate_terminal_shape(self) -> BuildFileCompletedPayload:
        if self.status == "complete":
            if self.result is None or self.error is not None:
                raise ValueError(
                    "complete build events require result and forbid error"
                )
        elif self.result is not None or self.error is None:
            raise ValueError(
                "non-complete build events require error and forbid result"
            )
        return self


class _KnowledgeEventBase(_StrictEventModel):
    event_id: str = Field(alias="eventId")
    event_version: Literal[1] = Field(default=EVENT_VERSION, alias="eventVersion")
    kb_code: str = Field(alias="knCode")
    occurred_at: datetime = Field(alias="occurredAt")


class DirectoryCreatedEvent(_KnowledgeEventBase):
    event_type: Literal["resource.directory.created"] = Field(
        default=ResourceEventType.DIRECTORY_CREATED.value, alias="eventType"
    )
    payload: DirectoryCreatedPayload


class DirectoryUpdatedEvent(_KnowledgeEventBase):
    event_type: Literal["resource.directory.updated"] = Field(
        default=ResourceEventType.DIRECTORY_UPDATED.value, alias="eventType"
    )
    payload: DirectoryUpdatedPayload


class DirectoryDeletedEvent(_KnowledgeEventBase):
    event_type: Literal["resource.directory.deleted"] = Field(
        default=ResourceEventType.DIRECTORY_DELETED.value, alias="eventType"
    )
    payload: DirectoryDeletedPayload


class FileImportedEvent(_KnowledgeEventBase):
    event_type: Literal["resource.file.imported"] = Field(
        default=ResourceEventType.FILE_IMPORTED.value, alias="eventType"
    )
    payload: FileImportedPayload


class FileUpdatedEvent(_KnowledgeEventBase):
    event_type: Literal["resource.file.updated"] = Field(
        default=ResourceEventType.FILE_UPDATED.value, alias="eventType"
    )
    payload: FileUpdatedPayload


class FileDeletedEvent(_KnowledgeEventBase):
    event_type: Literal["resource.file.deleted"] = Field(
        default=ResourceEventType.FILE_DELETED.value, alias="eventType"
    )
    payload: FileDeletedPayload


class ResourceMovedEvent(_KnowledgeEventBase):
    event_type: Literal["resource.moved"] = Field(
        default=ResourceEventType.RESOURCE_MOVED.value, alias="eventType"
    )
    payload: ResourceMovedPayload


class DiscoveryFileCompletedEvent(_KnowledgeEventBase):
    event_type: Literal["semantic.discovery.file.completed"] = Field(
        default=SemanticEventType.DISCOVERY_FILE_COMPLETED.value, alias="eventType"
    )
    payload: SemanticFileCompletedPayload


class DiscoveryBatchCompletedEvent(_KnowledgeEventBase):
    event_type: Literal["semantic.discovery.batch.completed"] = Field(
        default=SemanticEventType.DISCOVERY_BATCH_COMPLETED.value, alias="eventType"
    )
    payload: SemanticBatchCompletedPayload


class EnrichFileCompletedEvent(_KnowledgeEventBase):
    event_type: Literal["semantic.enrich.file.completed"] = Field(
        default=SemanticEventType.ENRICH_FILE_COMPLETED.value, alias="eventType"
    )
    payload: SemanticFileCompletedPayload


class EnrichBatchCompletedEvent(_KnowledgeEventBase):
    event_type: Literal["semantic.enrich.batch.completed"] = Field(
        default=SemanticEventType.ENRICH_BATCH_COMPLETED.value, alias="eventType"
    )
    payload: SemanticBatchCompletedPayload


class BuildFileCompletedEvent(_KnowledgeEventBase):
    event_type: Literal["build.file.completed"] = Field(
        default=BuildEventType.FILE_COMPLETED.value, alias="eventType"
    )
    payload: BuildFileCompletedPayload


_KnowledgeEventUnion: TypeAlias = (
    DirectoryCreatedEvent
    | DirectoryUpdatedEvent
    | DirectoryDeletedEvent
    | FileImportedEvent
    | FileUpdatedEvent
    | FileDeletedEvent
    | ResourceMovedEvent
    | DiscoveryFileCompletedEvent
    | DiscoveryBatchCompletedEvent
    | EnrichFileCompletedEvent
    | EnrichBatchCompletedEvent
    | BuildFileCompletedEvent
)
KnowledgeEvent: TypeAlias = Annotated[
    _KnowledgeEventUnion, Field(discriminator="event_type")
]
KNOWLEDGE_EVENT_ADAPTER = TypeAdapter(KnowledgeEvent)


def serialize_knowledge_event(event: KnowledgeEvent) -> dict[str, JsonValue]:
    """Serialize a validated event to the stable external camel-case contract."""
    return KNOWLEDGE_EVENT_ADAPTER.dump_python(event, by_alias=True, mode="json")


def parse_knowledge_event(value: Any) -> KnowledgeEvent:
    """Validate an external event using eventType as the payload discriminator."""
    if isinstance(value, str | bytes):
        return KNOWLEDGE_EVENT_ADAPTER.validate_json(value)
    return KNOWLEDGE_EVENT_ADAPTER.validate_json(
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    )


@runtime_checkable
class KnowledgeEventPublisher(Protocol):
    async def publish(self, event: KnowledgeEvent) -> None: ...


class NoopKnowledgeEventPublisher:
    async def publish(self, event: KnowledgeEvent) -> None:
        del event


def normalize_json_mapping(value: Any) -> dict[str, JsonValue] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise TypeError(f"expected JSON object, got {type(value).__name__}")
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        default=_json_default,
    )
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("expected JSON object")
    return decoded


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc_datetime(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, StrEnum):
        return value.value
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _utc_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _event_fields(
    *,
    kb_code: str,
    occurred_at: datetime | None,
) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "kb_code": str(kb_code),
        "occurred_at": _utc_datetime(occurred_at),
    }


def build_resource_event(
    *,
    event_type: ResourceEventType,
    kb_code: str,
    source_path: str | None,
    target_path: str | None,
    items: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
    result: Mapping[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> KnowledgeEvent:
    common = _event_fields(kb_code=kb_code, occurred_at=occurred_at)
    normalized_result = normalize_json_mapping(result)
    if event_type is ResourceEventType.DIRECTORY_CREATED:
        return DirectoryCreatedEvent(
            **common, payload=DirectoryCreatedPayload(target_path=target_path)
        )
    if event_type is ResourceEventType.DIRECTORY_UPDATED:
        return DirectoryUpdatedEvent(
            **common,
            payload=DirectoryUpdatedPayload(
                source_path=source_path, target_path=target_path
            ),
        )
    if event_type is ResourceEventType.DIRECTORY_DELETED:
        return DirectoryDeletedEvent(
            **common, payload=DirectoryDeletedPayload(source_path=source_path)
        )
    if event_type is ResourceEventType.FILE_IMPORTED:
        return FileImportedEvent(
            **common,
            payload=FileImportedPayload(
                target_path=target_path,
                items=tuple(ImportMutationItem.model_validate(item) for item in items),
                result=MutationSummary.model_validate(normalized_result),
            ),
        )
    if event_type is ResourceEventType.FILE_UPDATED:
        return FileUpdatedEvent(
            **common,
            payload=FileUpdatedPayload(
                source_path=source_path,
                target_path=target_path,
                result=MutationOperationResult.model_validate(normalized_result),
            ),
        )
    if event_type is ResourceEventType.FILE_DELETED:
        return FileDeletedEvent(
            **common, payload=FileDeletedPayload(source_path=source_path)
        )
    if event_type is ResourceEventType.RESOURCE_MOVED:
        return ResourceMovedEvent(
            **common,
            payload=ResourceMovedPayload(
                source_path=source_path,
                target_path=target_path,
                items=tuple(MoveMutationItem.model_validate(item) for item in items),
                result=MutationSummary.model_validate(normalized_result),
            ),
        )
    raise ValueError(f"unsupported resource event type: {event_type}")


def build_file_completed_event(
    *,
    kb_code: str,
    task_id: int | str,
    file_path: str,
    status: Literal["complete", "failed", "unsupported"],
    current_step: Literal["markdown", "chunking", "vectorizing", "complete"],
    chunk_count: int | None = None,
    line_count: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    occurred_at: datetime | None = None,
) -> BuildFileCompletedEvent:
    """Build one strict terminal event for an asynchronous file build."""
    result = None
    error = None
    if status == "complete":
        if chunk_count is None or line_count is None:
            raise ValueError("complete build event requires chunk_count and line_count")
        result = BuildFileCompletedResult(
            chunk_count=chunk_count,
            line_count=line_count,
        )
    else:
        if not error_code or not error_message:
            raise ValueError(
                "non-complete build event requires error_code and error_message"
            )
        error = BuildProcessingError(code=error_code, message=error_message)
    return BuildFileCompletedEvent(
        **_event_fields(kb_code=kb_code, occurred_at=occurred_at),
        payload=BuildFileCompletedPayload(
            task_id=str(task_id),
            status=status,
            file_path=file_path,
            current_step=current_step,
            result=result,
            error=error,
        ),
    )


def _semantic_event_classes(task_type: str):
    if task_type == "ENTITY_DISCOVERY":
        return DiscoveryFileCompletedEvent, DiscoveryBatchCompletedEvent
    if task_type in {"DOCUMENT_ENRICH", "ENTITY_ENRICH"}:
        return EnrichFileCompletedEvent, EnrichBatchCompletedEvent
    raise ValueError(f"unsupported semantic task type: {task_type}")


def _batch_progress(
    batch: Mapping[str, Any], counts: Mapping[str, int]
) -> SemanticBatchProgress:
    return SemanticBatchProgress(
        version=int(batch.get("version") or 0),
        total_count=int(batch.get("total_count") or 0),
        completed_count=int(batch.get("completed_count") or 0),
        succeeded_count=int(counts.get("succeeded", 0)),
        failed_count=int(counts.get("failed", 0)),
        skipped_count=int(counts.get("skipped", 0)),
    )


def build_semantic_terminal_events(
    task: Mapping[str, Any],
    batch: Mapping[str, Any],
    counts: Mapping[str, int],
) -> tuple[KnowledgeEvent, ...]:
    task_type = str(task["task_type"])
    file_event_class, batch_event_class = _semantic_event_classes(task_type)
    progress = _batch_progress(batch, counts)
    status = str(task["status"]).upper()
    error = None
    if status == "FAILED":
        error = SemanticProcessingError(
            code=str(task.get("error_code") or "PROCESSING_FAILED"),
            message=str(task.get("error_message") or "processing failed"),
            failure_kind=(
                str(task["failure_kind"])
                if task.get("failure_kind") is not None
                else None
            ),
            outcome_uncertain=bool(task.get("outcome_uncertain")),
        )
    file_event = file_event_class(
        **_event_fields(
            kb_code=str(task.get("kb_code") or task["knowledge_base_id"]),
            occurred_at=task.get("finished_at"),
        ),
        payload=SemanticFileCompletedPayload(
            batch_id=str(task["batch_id"]),
            task_id=str(task["kid"]),
            task_type=task_type,
            status=status,
            knowledge_base_id=str(task["knowledge_base_id"]),
            file_id=(
                str(task["fs_entry_id"])
                if task.get("fs_entry_id") is not None
                else None
            ),
            file_path=str(task["file_path_snapshot"]),
            progress=progress,
            result=normalize_json_mapping(task.get("result_payload")),
            error=error,
        ),
    )
    events: list[KnowledgeEvent] = [file_event]
    if str(batch["status"]) == "completed":
        events.append(
            batch_event_class(
                **_event_fields(
                    kb_code=str(batch.get("kb_code") or batch["knowledge_base_id"]),
                    occurred_at=batch.get("completed_at"),
                ),
                payload=SemanticBatchCompletedPayload(
                    batch_id=str(batch["batch_id"]),
                    task_type=task_type,
                    knowledge_base_id=str(batch["knowledge_base_id"]),
                    progress=progress,
                ),
            )
        )
    return tuple(events)


def build_semantic_empty_batch_event(
    *,
    batch: Mapping[str, Any],
    task_type: str,
    kb_code: str,
) -> KnowledgeEvent:
    _, batch_event_class = _semantic_event_classes(task_type)
    return batch_event_class(
        **_event_fields(
            kb_code=kb_code,
            occurred_at=batch.get("completed_at"),
        ),
        payload=SemanticBatchCompletedPayload(
            batch_id=str(batch["batch_id"]),
            task_type=task_type,
            knowledge_base_id=str(batch["knowledge_base_id"]),
            progress=_batch_progress(batch, {}),
        ),
    )


@dataclass(slots=True)
class KnowledgeEventPublisherInvoker:
    publisher: KnowledgeEventPublisher = field(
        default_factory=NoopKnowledgeEventPublisher
    )
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("event publisher timeout_seconds must be greater than 0")

    async def publish(self, event: KnowledgeEvent) -> None:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self.publisher.publish(event)
        except Exception as exc:
            logger.warning(
                "knowledge event publish failed: event_id=%s event_type=%s error_type=%s",
                event.event_id,
                event.event_type,
                type(exc).__name__,
            )

    async def publish_all(self, events: tuple[KnowledgeEvent, ...]) -> None:
        for event in events:
            await self.publish(event)


def load_knowledge_event_publisher(
    provider_path: str | None = None,
) -> KnowledgeEventPublisher:
    resolved_path = (
        provider_path
        if provider_path is not None
        else getenv("BY_QA_EVENT_PUBLISHER_PROVIDER", "")
    ).strip()
    if not resolved_path:
        return NoopKnowledgeEventPublisher()
    module_name, separator, attribute_name = resolved_path.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError(
            "BY_QA_EVENT_PUBLISHER_PROVIDER must use the 'module:attribute' format."
        )
    module = import_module(module_name)
    factory = getattr(module, attribute_name)
    publisher = factory() if callable(factory) else factory
    if not isinstance(publisher, KnowledgeEventPublisher):
        raise TypeError(
            "BY_QA_EVENT_PUBLISHER_PROVIDER must resolve to a KnowledgeEventPublisher."
        )
    return publisher
