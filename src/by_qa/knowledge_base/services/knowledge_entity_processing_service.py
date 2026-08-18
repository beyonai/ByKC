"""Application orchestration for KnowledgeEntity processing tasks."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any, Protocol
from uuid import uuid4

from by_qa.core import logger
from by_qa.knowledge_base.api.knowledge_entity_schemas import (
    EntityDiscoveryRequest,
    EntityEnrichRequest,
    ProcessingBatchAccepted,
    ProcessingCapability,
    ProcessingEligibility,
    ProcessingEligibilityRequest,
    ProcessingEligibilityResult,
    ProcessingScope,
    ProcessingTaskItem,
    ProcessingTaskPage,
    ProcessingTaskStatus,
    ProcessingTaskStatusRequest,
    ProcessingTaskSummary,
    ProcessingTaskType,
    RelationAssertionEvidence,
    SemanticRelationDirection,
    SemanticRelationEndpoint,
    SemanticRelationItem,
    SemanticRelationPage,
    SemanticRelationsRequest,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError

DISCOVERY_METHOD_VERSION = "discovery/1.3"
ENRICH_METHOD_VERSION = "enrich/1.0"
PROCESSING_POLICY_VERSION = "entity-policy/1.3"
MAX_RECENT_RELATION_EVIDENCE = 3

_TEXT_DOCUMENT_SUFFIXES = frozenset(
    {".csv", ".htm", ".html", ".markdown", ".md", ".txt"}
)
_MARKDOWN_DOCUMENT_SUFFIXES = frozenset({".markdown", ".md"})


def _canonical_subject_file_id(value: Any) -> int | None:
    """Normalize the EAV NUMERIC identity into the domain's positive bigint."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("subjectFileId must be an integer, not boolean")
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("subjectFileId numeric metadata must be an integer")
        normalized = int(value)
    elif isinstance(value, int):
        normalized = value
    elif isinstance(value, str):
        if not value.isascii() or not value.isdigit():
            raise ValueError("subjectFileId string metadata must be a positive integer")
        normalized = int(value)
    else:
        raise TypeError(
            "subjectFileId must be returned as int, Decimal, or digit string, got "
            f"{type(value).__name__}"
        )
    if normalized <= 0:
        raise ValueError("subjectFileId must be positive")
    return normalized


def _canonical_relation_timestamp(value: Any) -> str | None:
    """Normalize OpenGauss TIMESTAMPTZ values to one UTC representation."""

    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(
            f"semantic relation updated_at must be datetime, got {type(value).__name__}"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("semantic relation updated_at must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _canonical_json_value(value: Any, *, path: str = "$") -> Any:
    """Accept only explicitly normalized JSON-native fingerprint values."""

    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"fingerprint mapping key at {path} must be str, got "
                    f"{type(key).__name__}"
                )
            normalized[key] = _canonical_json_value(item, path=f"{path}.{key}")
        return normalized
    if isinstance(value, list | tuple):
        return [
            _canonical_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(
        f"unsupported fingerprint value at {path}: {type(value).__name__}; "
        "normalize database types explicitly before hashing"
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True)
class TaskEvent:
    """Immutable, in-process callback event."""

    event_id: str
    task_id: str
    batch_id: str
    task_type: ProcessingTaskType
    event_type: str
    stage: str | None
    status: ProcessingTaskStatus
    sequence: int
    progress: int
    source_file_id: str | None
    target_file_ids: tuple[str, ...] = ()
    result_summary: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


TaskCallback = Callable[[TaskEvent], Awaitable[None] | None]


@dataclass(frozen=True)
class TaskExecutionContext:
    """Stable hand-off from orchestration to a processing worker."""

    task_id: int
    batch_id: str
    task_type: ProcessingTaskType
    kb_code: str
    knowledge_base_id: int
    source_file_id: int
    file_path: str
    input_fingerprint: str
    input_checksum: str | None
    request_params: Mapping[str, Any]


@dataclass(frozen=True)
class TaskWorkerResult:
    result_payload: Mapping[str, Any] | None = None
    target_file_ids: tuple[int, ...] = ()
    index_version: str | None = None


class KnowledgeEntityTaskWorker(Protocol):
    async def run_task(
        self, context: TaskExecutionContext
    ) -> TaskWorkerResult | Mapping[str, Any] | None: ...


class KnowledgeEntityTaskScheduler(Protocol):
    def schedule(self, task_factory: Callable[[], Awaitable[None]]) -> None: ...


class AsyncioTaskScheduler:
    """Default same-process scheduler, required for callable callbacks."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, task_factory: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.create_task(task_factory())
        self._tasks.add(task)
        logger.debug(
            "knowledge_entity_task_scheduler task scheduled: active_task_count=%s",
            len(self._tasks),
        )
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            logger.warning(
                "knowledge_entity_task_scheduler task cancelled: active_task_count=%s",
                len(self._tasks),
            )
            return
        failure = task.exception()
        if failure is not None:
            logger.error(
                "knowledge_entity_task_scheduler task failed: error_type=%s, active_task_count=%s",
                type(failure).__name__,
                len(self._tasks),
                exc_info=(type(failure), failure, failure.__traceback__),
            )
            return
        logger.debug(
            "knowledge_entity_task_scheduler task completed: active_task_count=%s",
            len(self._tasks),
        )


@dataclass
class KnowledgeEntityProcessingOrchestrator:
    connection_factory: Callable[[], Awaitable[Any]]
    knowledge_base_repository: Any
    knowledge_entity_repository: Any
    knowledge_semantic_processing_task_repository: Any
    knowledge_file_reference_repository: Any
    worker: KnowledgeEntityTaskWorker
    task_scheduler: KnowledgeEntityTaskScheduler = field(
        default_factory=AsyncioTaskScheduler
    )

    async def evaluate_processing_eligibility(
        self, request: ProcessingEligibilityRequest
    ) -> ProcessingEligibilityResult:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            knowledge_base_id = await self._resolve_kb(cursor, request.kb_code)
            file_row = await self._get_file(
                cursor, knowledge_base_id, request.file_path
            )
            evaluation = await self._evaluate_file(
                cursor,
                request.kb_code,
                knowledge_base_id,
                file_row,
                request.capability,
            )
            logger.info(
                "knowledge_entity_processing_service eligibility evaluated: knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, capability=%s, eligibility=%s, reason_code=%s",
                knowledge_base_id,
                request.kb_code,
                evaluation.result.file_id,
                evaluation.result.file_path,
                request.capability.value,
                evaluation.result.eligibility.value,
                evaluation.result.reason_code,
            )
            return evaluation.result
        finally:
            await connection.close()

    async def discover_knowledge_entities(
        self,
        request: EntityDiscoveryRequest,
        *,
        callback: TaskCallback | None = None,
    ) -> ProcessingBatchAccepted:
        return await self._accept(
            request=request,
            capability=ProcessingCapability.ENTITY_DISCOVERY,
            task_type=ProcessingTaskType.ENTITY_DISCOVERY,
            callback=callback,
        )

    async def enrich_knowledge_entities(
        self,
        request: EntityEnrichRequest,
        *,
        callback: TaskCallback | None = None,
    ) -> ProcessingBatchAccepted:
        return await self._accept(
            request=request,
            capability=ProcessingCapability.ENTITY_ENRICH,
            task_type=ProcessingTaskType.DOCUMENT_ENRICH,
            callback=callback,
        )

    async def get_processing_task_status(
        self, request: ProcessingTaskStatusRequest
    ) -> ProcessingTaskPage:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            knowledge_base_id = await self._resolve_kb(cursor, request.kb_code)
            fs_entry_id = None
            if request.file_path is not None:
                fs_entry_id = self._row_id(
                    await self._get_file(cursor, knowledge_base_id, request.file_path)
                )
            filters = {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "batch_id": request.batch_id,
                "task_type": request.task_type.value if request.task_type else None,
                "statuses": [value.value.lower() for value in request.status_list]
                if request.status_list
                else None,
                "latest_only": request.latest_only,
            }
            total = await self.knowledge_semantic_processing_task_repository.count_processing_tasks(
                cursor, **filters
            )
            rows = await self.knowledge_semantic_processing_task_repository.list_processing_tasks(
                cursor,
                **filters,
                limit=request.page_size,
                offset=(request.page_num - 1) * request.page_size,
            )
            result = ProcessingTaskPage(
                knowledge_base_id=str(knowledge_base_id),
                kb_code=request.kb_code,
                file_path=request.file_path,
                total=total,
                page_num=request.page_num,
                page_size=request.page_size,
                data=[self._task_item(row, request.include_details) for row in rows],
            )
            logger.info(
                "knowledge_entity_processing_service task status queried: knowledge_base_id=%s, kb_code=%s, file_path=%s, batch_id=%s, task_type=%s, status_count=%s, latest_only=%s, page_num=%s, page_size=%s, total=%s, returned_count=%s",
                knowledge_base_id,
                request.kb_code,
                request.file_path,
                request.batch_id,
                request.task_type.value if request.task_type else None,
                len(request.status_list or []),
                request.latest_only,
                request.page_num,
                request.page_size,
                total,
                len(rows),
            )
            return result
        finally:
            await connection.close()

    async def get_semantic_relations(
        self, request: SemanticRelationsRequest
    ) -> SemanticRelationPage:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            knowledge_base_id = await self._resolve_kb(cursor, request.kb_code)
            file_row = await self._get_file(
                cursor, knowledge_base_id, request.file_path
            )
            file_id = self._row_id(file_row)
            codes = (
                [value.value for value in request.relation_code_list]
                if request.relation_code_list
                else None
            )
            offset = (request.page_num - 1) * request.page_size
            outgoing_count = incoming_count = 0
            outgoing: list[dict[str, Any]] = []
            incoming: list[dict[str, Any]] = []
            if request.direction in {
                SemanticRelationDirection.OUTGOING,
                SemanticRelationDirection.BOTH,
            }:
                outgoing_count = await self.knowledge_file_reference_repository.count_relations_by_source(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    source_fs_entry_id=file_id,
                    relation_code=codes,
                )
            if request.direction in {
                SemanticRelationDirection.INCOMING,
                SemanticRelationDirection.BOTH,
            }:
                incoming_count = await self.knowledge_file_reference_repository.count_relations_by_target(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    target_fs_entry_id=file_id,
                    relation_code=codes,
                )
            if request.direction == SemanticRelationDirection.OUTGOING:
                outgoing = await self.knowledge_file_reference_repository.list_relations_by_source(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    source_fs_entry_id=file_id,
                    relation_code=codes,
                    limit=request.page_size,
                    offset=offset,
                )
            elif request.direction == SemanticRelationDirection.INCOMING:
                incoming = await self.knowledge_file_reference_repository.list_relations_by_target(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    target_fs_entry_id=file_id,
                    relation_code=codes,
                    limit=request.page_size,
                    offset=offset,
                )
            else:
                end = offset + request.page_size
                outgoing = await self._relation_prefix(
                    cursor, "source", knowledge_base_id, file_id, codes, end
                )
                incoming = await self._relation_prefix(
                    cursor, "target", knowledge_base_id, file_id, codes, end
                )
            tagged = [(row, SemanticRelationDirection.OUTGOING) for row in outgoing] + [
                (row, SemanticRelationDirection.INCOMING) for row in incoming
            ]
            tagged.sort(key=lambda item: self._row_id(item[0]))
            if request.direction == SemanticRelationDirection.BOTH:
                tagged = tagged[offset : offset + request.page_size]
            endpoint_ids = sorted(
                {
                    int(row[key])
                    for row, _ in tagged
                    for key in ("source_fs_entry_id", "target_fs_entry_id")
                }
            )
            endpoint_rows = await self.knowledge_entity_repository.get_files_by_ids(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_ids=endpoint_ids,
            )
            endpoints = {self._row_id(row): row for row in endpoint_rows}
            data = [
                self._relation_item(row, direction, request.kb_code, endpoints)
                for row, direction in tagged
            ]
            result = SemanticRelationPage(
                file_id=str(file_id),
                total=outgoing_count + incoming_count,
                page_num=request.page_num,
                page_size=request.page_size,
                data=data,
            )
            logger.info(
                "knowledge_entity_processing_service semantic relations queried: knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, direction=%s, relation_code_count=%s, page_num=%s, page_size=%s, outgoing_count=%s, incoming_count=%s, total=%s, returned_count=%s",
                knowledge_base_id,
                request.kb_code,
                file_id,
                request.file_path,
                request.direction.value,
                len(codes or []),
                request.page_num,
                request.page_size,
                outgoing_count,
                incoming_count,
                result.total,
                len(data),
            )
            return result
        finally:
            await connection.close()

    async def _accept(
        self,
        *,
        request: EntityDiscoveryRequest | EntityEnrichRequest,
        capability: ProcessingCapability,
        task_type: ProcessingTaskType,
        callback: TaskCallback | None,
    ) -> ProcessingBatchAccepted:
        batch_id = self._batch_id(task_type)
        acceptance_started_at = time.perf_counter()
        scope = (
            ProcessingScope.SINGLE_FILE
            if request.file_path
            else ProcessingScope.WHOLE_KB
        )
        logger.info(
            "knowledge_entity_processing_service batch acceptance started: batch_id=%s, kb_code=%s, file_path=%s, task_type=%s, scope=%s, force=%s, callback_configured=%s",
            batch_id,
            request.kb_code,
            request.file_path,
            task_type.value,
            scope.value,
            request.force,
            callback is not None,
        )
        connection = await self.connection_factory()
        contexts: list[TaskExecutionContext] = []
        summaries: list[ProcessingTaskSummary] = []
        eligible_count = reused_count = skipped_count = 0
        eligibility_counts: Counter[str] = Counter()
        reason_counts: Counter[str] = Counter()
        knowledge_base_id: int | None = None
        files: list[Mapping[str, Any]] = []
        try:
            cursor = connection.cursor()
            knowledge_base_id = await self._resolve_kb(cursor, request.kb_code)
            if request.file_path:
                files = [
                    await self._get_file(cursor, knowledge_base_id, request.file_path)
                ]
            else:
                prefix = (
                    "/KnowledgeEntity"
                    if capability == ProcessingCapability.ENTITY_ENRICH
                    else None
                )
                files = await self.knowledge_entity_repository.list_files_with_metadata(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    path_prefix=prefix,
                )
            logger.info(
                "knowledge_entity_processing_service batch candidates selected: batch_id=%s, knowledge_base_id=%s, kb_code=%s, file_path=%s, task_type=%s, scope=%s, candidate_count=%s",
                batch_id,
                knowledge_base_id,
                request.kb_code,
                request.file_path,
                task_type.value,
                scope.value,
                len(files),
            )
            for file_row in files:
                if (
                    capability == ProcessingCapability.ENTITY_DISCOVERY
                    and self._inside_entity_directory(str(file_row["file_path"]))
                ):
                    skipped_count += 1
                    reason_counts["ENTITY_DIRECTORY_EXCLUDED"] += 1
                    continue
                evaluation = await self._evaluate_file(
                    cursor,
                    request.kb_code,
                    knowledge_base_id,
                    file_row,
                    capability,
                )
                eligibility_counts[evaluation.result.eligibility.value] += 1
                reason_counts[evaluation.result.reason_code or "NONE"] += 1
                logger.debug(
                    "knowledge_entity_processing_service file eligibility evaluated: batch_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s, eligibility=%s, reason_code=%s",
                    batch_id,
                    knowledge_base_id,
                    request.kb_code,
                    evaluation.result.file_id,
                    evaluation.result.file_path,
                    task_type.value,
                    evaluation.result.eligibility.value,
                    evaluation.result.reason_code,
                )
                if evaluation.result.eligibility == ProcessingEligibility.INELIGIBLE:
                    skipped_count += 1
                    continue
                eligible_count += 1
                await self._lock_file(cursor, self._row_id(file_row))
                reusable = await self._find_active_task(
                    cursor,
                    knowledge_base_id,
                    self._row_id(file_row),
                    task_type,
                )
                if reusable is None and not request.force:
                    reusable = await self._find_fresh_task(
                        cursor,
                        knowledge_base_id,
                        self._row_id(file_row),
                        task_type,
                        evaluation.input_fingerprint,
                    )
                if reusable is not None:
                    reused_count += 1
                    summaries.append(self._task_summary(reusable, reused=True))
                    logger.debug(
                        "knowledge_entity_processing_service task reused: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_path=%s, task_type=%s, status=%s",
                        batch_id,
                        self._row_id(reusable),
                        knowledge_base_id,
                        request.kb_code,
                        file_row["file_path"],
                        task_type.value,
                        reusable["status"],
                    )
                    continue
                params = request.model_dump(mode="json", by_alias=True)
                created = await self.knowledge_semantic_processing_task_repository.create_processing_task(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    fs_entry_id=self._row_id(file_row),
                    task_type=task_type.value,
                    status="pending",
                    batch_id=batch_id,
                    current_stage="accepted",
                    progress=0,
                    input_fingerprint=evaluation.input_fingerprint,
                    input_checksum=file_row.get("checksum"),
                    method_version=evaluation.method_version,
                    request_params=params,
                )
                if created is None:
                    raise RuntimeError("failed to create processing task")
                context = TaskExecutionContext(
                    task_id=self._row_id(created),
                    batch_id=batch_id,
                    task_type=task_type,
                    kb_code=request.kb_code,
                    knowledge_base_id=knowledge_base_id,
                    source_file_id=self._row_id(file_row),
                    file_path=str(file_row["file_path"]),
                    input_fingerprint=evaluation.input_fingerprint,
                    input_checksum=file_row.get("checksum"),
                    request_params=params,
                )
                contexts.append(context)
                logger.info(
                    "knowledge_entity_processing_service task created: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s, status=pending",
                    batch_id,
                    context.task_id,
                    knowledge_base_id,
                    request.kb_code,
                    context.source_file_id,
                    context.file_path,
                    task_type.value,
                )
                summaries.append(
                    ProcessingTaskSummary(
                        task_id=str(context.task_id),
                        status=ProcessingTaskStatus.PENDING,
                        file_id=str(context.source_file_id),
                        file_path=context.file_path,
                    )
                )
            await connection.commit()
        except Exception as exc:
            await connection.rollback()
            logger.exception(
                "knowledge_entity_processing_service batch acceptance rolled back: batch_id=%s, knowledge_base_id=%s, kb_code=%s, file_path=%s, task_type=%s, error_type=%s, elapsed_ms=%.2f",
                batch_id,
                knowledge_base_id,
                request.kb_code,
                request.file_path,
                task_type.value,
                type(exc).__name__,
                (time.perf_counter() - acceptance_started_at) * 1000,
            )
            raise
        finally:
            await connection.close()
        logger.info(
            "knowledge_entity_processing_service batch accepted: batch_id=%s, knowledge_base_id=%s, kb_code=%s, file_path=%s, task_type=%s, scope=%s, candidate_count=%s, eligible_count=%s, accepted_count=%s, reused_count=%s, skipped_count=%s, eligibility_counts=%s, reason_counts=%s, elapsed_ms=%.2f",
            batch_id,
            knowledge_base_id,
            request.kb_code,
            request.file_path,
            task_type.value,
            scope.value,
            len(files),
            eligible_count,
            len(contexts),
            reused_count,
            skipped_count,
            dict(eligibility_counts),
            dict(reason_counts),
            (time.perf_counter() - acceptance_started_at) * 1000,
        )
        for context in contexts:
            await self._notify(
                callback,
                self._event(context, "task.accepted", 0, 0),
                context=context,
            )
            try:
                self.task_scheduler.schedule(
                    lambda current=context: self._run_task(current, callback)
                )
                logger.info(
                    "knowledge_entity_processing_service task scheduled: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s",
                    context.batch_id,
                    context.task_id,
                    context.knowledge_base_id,
                    context.kb_code,
                    context.source_file_id,
                    context.file_path,
                    context.task_type.value,
                )
            except Exception as exc:
                await self._fail_scheduling(context, callback, exc)
        return ProcessingBatchAccepted(
            batch_id=batch_id,
            scope=scope,
            task_type=task_type,
            eligible_count=eligible_count,
            accepted_count=len(contexts),
            reused_count=reused_count,
            skipped_count=skipped_count,
            tasks=summaries,
        )

    async def _run_task(
        self, context: TaskExecutionContext, callback: TaskCallback | None
    ) -> None:
        task_started_at = time.perf_counter()
        logger.info(
            "knowledge_entity_processing_service task execution started: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s",
            context.batch_id,
            context.task_id,
            context.knowledge_base_id,
            context.kb_code,
            context.source_file_id,
            context.file_path,
            context.task_type.value,
        )
        await self._update_task(
            context,
            status="running",
            current_stage="started",
            progress=1,
            started=True,
        )
        await self._notify(
            callback,
            self._event(context, "task.started", 1, 1),
            context=context,
        )
        try:
            worker_result = await self.worker.run_task(context)
            result, targets, index_version = self._worker_result(worker_result)
            await self._update_task(
                context,
                status="succeeded",
                current_stage="completed",
                progress=100,
                result_payload=result,
                index_version=index_version,
                finished=True,
            )
            await self._notify(
                callback,
                self._event(
                    context,
                    "task.succeeded",
                    2,
                    100,
                    target_file_ids=targets,
                    result_summary=result,
                ),
                context=context,
            )
            logger.info(
                "knowledge_entity_processing_service task execution succeeded: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s, target_count=%s, index_version=%s, elapsed_ms=%.2f",
                context.batch_id,
                context.task_id,
                context.knowledge_base_id,
                context.kb_code,
                context.source_file_id,
                context.file_path,
                context.task_type.value,
                len(targets),
                index_version,
                (time.perf_counter() - task_started_at) * 1000,
            )
        except Exception as exc:
            error = {
                "errorCode": getattr(exc, "error_code", "PROCESSING_FAILED"),
                "message": str(exc),
                "retryable": bool(getattr(exc, "retryable", True)),
            }
            logger.exception(
                "knowledge_entity_processing_service task execution failed: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s, error_code=%s, error_type=%s, retryable=%s, elapsed_ms=%.2f",
                context.batch_id,
                context.task_id,
                context.knowledge_base_id,
                context.kb_code,
                context.source_file_id,
                context.file_path,
                context.task_type.value,
                error["errorCode"],
                type(exc).__name__,
                error["retryable"],
                (time.perf_counter() - task_started_at) * 1000,
            )
            try:
                await self._update_task(
                    context,
                    status="failed",
                    current_stage="failed",
                    progress=100,
                    error_code=str(error["errorCode"]),
                    error_message=str(error["message"]),
                    finished=True,
                )
            finally:
                await self._notify(
                    callback,
                    self._event(context, "task.failed", 2, 100, error=error),
                    context=context,
                )

    async def _fail_scheduling(
        self,
        context: TaskExecutionContext,
        callback: TaskCallback | None,
        exc: Exception,
    ) -> None:
        error = {
            "errorCode": "SCHEDULING_FAILED",
            "message": str(exc),
            "retryable": True,
        }
        logger.error(
            "knowledge_entity_processing_service task scheduling failed: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s, error_type=%s",
            context.batch_id,
            context.task_id,
            context.knowledge_base_id,
            context.kb_code,
            context.source_file_id,
            context.file_path,
            context.task_type.value,
            type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        await self._update_task(
            context,
            status="failed",
            current_stage="scheduling_failed",
            progress=100,
            error_code="SCHEDULING_FAILED",
            error_message=str(exc),
            finished=True,
        )
        await self._notify(
            callback,
            self._event(context, "task.failed", 1, 100, error=error),
            context=context,
        )

    async def _update_task(self, context: TaskExecutionContext, **updates: Any) -> None:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            await self.knowledge_semantic_processing_task_repository.update_processing_task(
                cursor,
                task_id=context.task_id,
                task_type=context.task_type.value,
                **updates,
            )
            await connection.commit()
            logger.debug(
                "knowledge_entity_processing_service task state persisted: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s, status=%s, current_stage=%s, progress=%s",
                context.batch_id,
                context.task_id,
                context.knowledge_base_id,
                context.kb_code,
                context.source_file_id,
                context.file_path,
                context.task_type.value,
                updates.get("status"),
                updates.get("current_stage"),
                updates.get("progress"),
            )
        except Exception as exc:
            await connection.rollback()
            logger.exception(
                "knowledge_entity_processing_service task state persistence failed: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s, target_status=%s, target_stage=%s, error_type=%s",
                context.batch_id,
                context.task_id,
                context.knowledge_base_id,
                context.kb_code,
                context.source_file_id,
                context.file_path,
                context.task_type.value,
                updates.get("status"),
                updates.get("current_stage"),
                type(exc).__name__,
            )
            raise
        finally:
            await connection.close()

    async def _evaluate_file(
        self,
        cursor: Any,
        kb_code: str,
        knowledge_base_id: int,
        file_row: Mapping[str, Any],
        capability: ProcessingCapability,
    ) -> _Evaluation:
        file_id = self._row_id(file_row)
        document_kind = self._effective_document_kind(file_row)
        expected_kind = (
            "original"
            if capability == ProcessingCapability.ENTITY_DISCOVERY
            else "knowledgeEntity"
        )
        task_type = (
            ProcessingTaskType.ENTITY_DISCOVERY
            if capability == ProcessingCapability.ENTITY_DISCOVERY
            else ProcessingTaskType.DOCUMENT_ENRICH
        )
        method_version = (
            DISCOVERY_METHOD_VERSION
            if capability == ProcessingCapability.ENTITY_DISCOVERY
            else ENRICH_METHOD_VERSION
        )
        reason = None
        if document_kind != expected_kind:
            reason = "DOCUMENT_KIND_MISMATCH"
        elif not self._capability_enabled(file_row, document_kind, capability):
            reason = "CAPABILITY_DISABLED"
        elif (
            capability == ProcessingCapability.ENTITY_DISCOVERY
            and not self._is_supported_discovery_document(file_row)
        ):
            reason = "UNSUPPORTED_FILE_FORMAT"
        elif (
            capability == ProcessingCapability.ENTITY_ENRICH
            and not self._inside_entity_directory(str(file_row.get("file_path") or ""))
        ):
            reason = "KNOWLEDGE_ENTITY_PATH_REQUIRED"
        elif (
            capability == ProcessingCapability.ENTITY_ENRICH
            and not self._is_markdown_document(file_row)
        ):
            reason = "UNSUPPORTED_CONTENT_TYPE"
        elif not self._content_ready(file_row):
            reason = "CONTENT_NOT_READY"
        elif (
            capability == ProcessingCapability.ENTITY_ENRICH
            and not self._identity_complete(file_row)
        ):
            reason = "IDENTITY_METADATA_INCOMPLETE"
        evidence_rows: list[dict[str, Any]] = []
        evidence_files: list[dict[str, Any]] = []
        if reason is None and capability == ProcessingCapability.ENTITY_ENRICH:
            evidence_rows = await self.knowledge_file_reference_repository.list_recent_assertions_by_target(
                cursor,
                knowledge_base_id=knowledge_base_id,
                target_fs_entry_id=file_id,
                relation_code=None,
                limit=MAX_RECENT_RELATION_EVIDENCE,
                offset=0,
            )
            source_ids = sorted(
                {int(row["source_fs_entry_id"]) for row in evidence_rows}
            )
            evidence_files = await self.knowledge_entity_repository.get_files_by_ids(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_ids=source_ids,
            )
            if not evidence_files:
                reason = "NO_EVIDENCE"
        fingerprint = (
            self._fingerprint(
                file_row,
                capability,
                evidence_rows,
            )
            if reason is None
            else ""
        )
        latest = await self._latest_successful(
            cursor, knowledge_base_id, file_id, task_type
        )
        if reason is not None:
            eligibility = ProcessingEligibility.INELIGIBLE
        elif latest is None:
            eligibility = ProcessingEligibility.ELIGIBLE_AND_STALE
            reason = "NEVER_PROCESSED"
        elif capability == ProcessingCapability.ENTITY_DISCOVERY:
            if latest.get("input_fingerprint") == fingerprint:
                eligibility = ProcessingEligibility.ELIGIBLE_BUT_FRESH
                reason = "INPUT_UNCHANGED"
            else:
                eligibility = ProcessingEligibility.ELIGIBLE_AND_STALE
                reason = (
                    "METHOD_VERSION_CHANGED"
                    if latest.get("method_version") != method_version
                    else "INPUT_CHANGED"
                )
        else:
            latest_relation_at = max(
                (row["created_at"] for row in evidence_rows), default=None
            )
            document_updated_at = file_row.get("updated_at")
            if (
                latest_relation_at is not None
                and document_updated_at is not None
                and latest_relation_at > document_updated_at
            ):
                eligibility = ProcessingEligibility.ELIGIBLE_AND_STALE
                reason = "NEW_RELATION"
            else:
                eligibility = ProcessingEligibility.ELIGIBLE_BUT_FRESH
                reason = "NO_NEW_RELATIONS"
        result = ProcessingEligibilityResult(
            file_id=str(file_id),
            kb_code=kb_code,
            file_path=str(file_row["file_path"]),
            document_kind=document_kind,
            capability=capability,
            eligibility=eligibility,
            reason_code=reason,
            last_successful_task_id=str(self._row_id(latest)) if latest else None,
            last_successful_at=latest.get("finished_at") if latest else None,
        )
        return _Evaluation(
            result=result,
            input_fingerprint=fingerprint,
            method_version=method_version,
        )

    async def _latest_successful(
        self,
        cursor: Any,
        knowledge_base_id: int,
        file_id: int,
        task_type: ProcessingTaskType,
    ) -> dict[str, Any] | None:
        rows = await self.knowledge_semantic_processing_task_repository.list_processing_tasks(
            cursor,
            knowledge_base_id=knowledge_base_id,
            fs_entry_id=file_id,
            task_type=task_type.value,
            statuses=["succeeded"],
            latest_only=False,
            limit=1,
            offset=0,
        )
        return rows[0] if rows else None

    async def _find_active_task(
        self,
        cursor: Any,
        knowledge_base_id: int,
        file_id: int,
        task_type: ProcessingTaskType,
    ) -> dict[str, Any] | None:
        rows = await self.knowledge_semantic_processing_task_repository.list_processing_tasks(
            cursor,
            knowledge_base_id=knowledge_base_id,
            fs_entry_id=file_id,
            task_type=task_type.value,
            statuses=["pending", "running"],
            latest_only=False,
            limit=1,
            offset=0,
        )
        return rows[0] if rows else None

    async def _find_fresh_task(
        self,
        cursor: Any,
        knowledge_base_id: int,
        file_id: int,
        task_type: ProcessingTaskType,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        rows = await self.knowledge_semantic_processing_task_repository.list_processing_tasks(
            cursor,
            knowledge_base_id=knowledge_base_id,
            fs_entry_id=file_id,
            task_type=task_type.value,
            statuses=["succeeded"],
            latest_only=False,
            limit=500,
            offset=0,
        )
        return next(
            (row for row in rows if row.get("input_fingerprint") == fingerprint), None
        )

    async def _relation_prefix(
        self,
        cursor: Any,
        direction: str,
        knowledge_base_id: int,
        file_id: int,
        codes: list[str] | None,
        count: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        while len(rows) < count:
            limit = min(500, count - len(rows))
            if direction == "source":
                chunk = await self.knowledge_file_reference_repository.list_relations_by_source(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    source_fs_entry_id=file_id,
                    relation_code=codes,
                    limit=limit,
                    offset=len(rows),
                )
            else:
                chunk = await self.knowledge_file_reference_repository.list_relations_by_target(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    target_fs_entry_id=file_id,
                    relation_code=codes,
                    limit=limit,
                    offset=len(rows),
                )
            rows.extend(chunk)
            if len(chunk) < limit:
                break
        return rows

    async def _resolve_kb(self, cursor: Any, kb_code: str) -> int:
        row = await self.knowledge_base_repository.get_by_code(cursor, kb_code)
        if row is None:
            raise KnowledgeBaseValidationError(f"knowledge base not found: {kb_code}")
        return self._row_id(row)

    async def _lock_file(self, cursor: Any, file_id: int) -> None:
        """Serialize the reuse-check and task creation for one file."""
        await cursor.execute(
            "SELECT kid FROM knowledge_fs_entry WHERE kid = %(file_id)s FOR UPDATE",
            {"file_id": file_id},
        )

    async def _get_file(
        self, cursor: Any, knowledge_base_id: int, file_path: str
    ) -> dict[str, Any]:
        row = await self.knowledge_entity_repository.get_file_with_metadata(
            cursor, knowledge_base_id=knowledge_base_id, file_path=file_path
        )
        if row is None:
            raise KnowledgeBaseValidationError(f"document not found: {file_path}")
        return row

    def _fingerprint(
        self,
        file_row: Mapping[str, Any],
        capability: ProcessingCapability,
        evidence_rows: list[dict[str, Any]],
    ) -> str:
        if capability == ProcessingCapability.ENTITY_DISCOVERY:
            value = {
                "sourceFileChecksum": file_row.get("checksum"),
                "discoveryMethodVersion": DISCOVERY_METHOD_VERSION,
                "processingPolicyVersion": PROCESSING_POLICY_VERSION,
            }
        else:
            latest_relation = evidence_rows[0] if evidence_rows else None
            value = {
                "entityFileId": self._row_id(file_row),
                "latestRelationId": self._row_id(latest_relation)
                if latest_relation
                else None,
                "latestRelationCreatedAt": _canonical_relation_timestamp(
                    latest_relation.get("created_at") if latest_relation else None
                ),
            }
        encoded = _canonical_json(value)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _capability_enabled(
        self,
        row: Mapping[str, Any],
        document_kind: str,
        capability: ProcessingCapability,
    ) -> bool:
        if row.get("processing_capabilities_configured"):
            return capability.value in (row.get("processing_capabilities") or [])
        defaults = {
            "original": ProcessingCapability.ENTITY_DISCOVERY.value,
            "knowledgeEntity": ProcessingCapability.ENTITY_ENRICH.value,
        }
        return defaults.get(document_kind) == capability.value

    def _effective_document_kind(self, row: Mapping[str, Any]) -> str:
        """Resolve a safe processing default for documents without metadata.

        Historical and newly imported ordinary documents may not have an EAV
        ``documentKind`` row.  They retain the original-document default.  Only
        files in the reserved entity directory resolve to ``knowledgeEntity``;
        identity metadata alone does not change the kind.  An explicitly
        configured blank/invalid value remains ``unknown`` and is rejected
        instead of silently receiving a default.
        """
        configured_value = str(row.get("document_kind") or "").strip()
        if configured_value:
            return configured_value
        if row.get("document_kind_configured"):
            return "unknown"
        file_path = str(row.get("file_path") or "")
        if self._inside_entity_directory(file_path):
            return "knowledgeEntity"
        return "original"

    def _content_ready(self, row: Mapping[str, Any]) -> bool:
        return bool(
            row.get("markdown_bucket_name")
            and row.get("markdown_object_key")
            and int(row.get("line_count") or 0) > 0
        )

    def _is_supported_discovery_document(self, row: Mapping[str, Any]) -> bool:
        """Allow original text documents, never converted binary sidecars.

        A present suffix is authoritative and must belong to the build
        pipeline's current text allowlist.  MIME is only a fallback for a
        suffixless file, so an Office/PDF document cannot become eligible
        merely because a Markdown sidecar exists.
        """
        suffix = PurePosixPath(str(row.get("file_path") or "")).suffix.lower()
        if suffix:
            return suffix in _TEXT_DOCUMENT_SUFFIXES
        mime_type = (
            str(row.get("mime_type") or "").split(";", maxsplit=1)[0].strip().lower()
        )
        return mime_type.startswith("text/")

    def _is_markdown_document(self, row: Mapping[str, Any]) -> bool:
        suffix = PurePosixPath(str(row.get("file_path") or "")).suffix.lower()
        return suffix in _MARKDOWN_DOCUMENT_SUFFIXES

    def _identity_complete(self, row: Mapping[str, Any]) -> bool:
        basics_complete = bool(
            str(row.get("entity_name") or "").strip()
            and isinstance(row.get("aliases"), list)
        )
        if not basics_complete:
            return False
        try:
            _canonical_subject_file_id(row.get("subject_file_id"))
        except (TypeError, ValueError):
            return False
        return True

    def _task_item(
        self, row: Mapping[str, Any], include_details: bool
    ) -> ProcessingTaskItem:
        error = None
        if include_details and (row.get("error_code") or row.get("error_message")):
            error = {
                "errorCode": row.get("error_code"),
                "message": row.get("error_message"),
            }
        return ProcessingTaskItem(
            task_id=str(self._row_id(row)),
            batch_id=row.get("batch_id"),
            task_type=ProcessingTaskType(str(row["task_type"]).upper()),
            status=ProcessingTaskStatus(str(row["status"]).upper()),
            current_stage=row.get("current_stage"),
            progress=row.get("progress"),
            file_id=str(row["fs_entry_id"]),
            file_path=str(row["file_path"]),
            index_version=row.get("index_version"),
            created_at=row["created_at"],
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            result=row.get("result_payload") if include_details else None,
            error=error,
        )

    def _task_summary(
        self, row: Mapping[str, Any], *, reused: bool
    ) -> ProcessingTaskSummary:
        return ProcessingTaskSummary(
            task_id=str(self._row_id(row)),
            status=ProcessingTaskStatus(str(row["status"]).upper()),
            file_id=str(row["fs_entry_id"]),
            file_path=str(row["file_path"]),
            reused=reused,
        )

    def _relation_item(
        self,
        row: Mapping[str, Any],
        direction: SemanticRelationDirection,
        kb_code: str,
        endpoints: Mapping[int, Mapping[str, Any]],
    ) -> SemanticRelationItem:
        def endpoint(file_id: int) -> SemanticRelationEndpoint:
            value = endpoints[file_id]
            return SemanticRelationEndpoint(
                file_id=str(file_id),
                kb_code=kb_code,
                file_path=str(value["file_path"]),
                document_kind=self._effective_document_kind(value),
            )

        source_file_id = int(row["source_fs_entry_id"])
        target_file_id = int(row["target_fs_entry_id"])
        relation_code = str(row["relation_code"])
        evidence_values = {
            "producer_run_id": row.get("producer_run_id"),
            "evidence_fingerprint": row.get("evidence_fingerprint"),
            "source_heading_path": row.get("source_heading_path"),
            "start_line": row.get("start_line"),
            "end_line": row.get("end_line"),
            "start_offset": row.get("start_offset"),
            "end_offset": row.get("end_offset"),
        }
        representative_evidence = (
            RelationAssertionEvidence(**evidence_values)
            if any(value is not None for value in evidence_values.values())
            else None
        )
        logical_key = f"{source_file_id}:{relation_code}:{target_file_id}"
        relation_id = (
            "lr_" + hashlib.sha256(logical_key.encode("utf-8")).hexdigest()[:24]
        )
        return SemanticRelationItem(
            relation_id=relation_id,
            relation_code=relation_code,
            direction=direction,
            source=endpoint(source_file_id),
            target=endpoint(target_file_id),
            assertion_count=int(row.get("assertion_count") or 1),
            confidence=float(
                1.0 if row.get("confidence") is None else row["confidence"]
            ),
            discovered_by=str(row.get("discovered_by") or "UNKNOWN"),
            source_task_id=str(row["source_task_id"])
            if row.get("source_task_id") is not None
            else None,
            representative_evidence=representative_evidence,
        )

    def _worker_result(
        self, value: TaskWorkerResult | Mapping[str, Any] | None
    ) -> tuple[dict[str, Any] | None, tuple[int, ...], str | None]:
        if value is None:
            return None, (), None
        if isinstance(value, TaskWorkerResult):
            return (
                dict(value.result_payload)
                if value.result_payload is not None
                else None,
                value.target_file_ids,
                value.index_version,
            )
        if all(
            hasattr(value, attribute)
            for attribute in ("result_payload", "target_file_ids", "index_version")
        ):
            result_payload = getattr(value, "result_payload")
            return (
                dict(result_payload) if result_payload is not None else None,
                tuple(int(item) for item in getattr(value, "target_file_ids")),
                getattr(value, "index_version"),
            )
        payload = dict(value)
        targets = payload.pop("target_file_ids", payload.pop("targetFileIds", ()))
        index_version = payload.pop("index_version", payload.pop("indexVersion", None))
        return payload, tuple(int(item) for item in targets), index_version

    def _event(
        self,
        context: TaskExecutionContext,
        event_type: str,
        sequence: int,
        progress: int,
        *,
        target_file_ids: tuple[int, ...] = (),
        result_summary: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> TaskEvent:
        status_by_event = {
            "task.accepted": ProcessingTaskStatus.PENDING,
            "task.started": ProcessingTaskStatus.RUNNING,
            "task.succeeded": ProcessingTaskStatus.SUCCEEDED,
            "task.failed": ProcessingTaskStatus.FAILED,
        }
        return TaskEvent(
            event_id=uuid4().hex,
            task_id=str(context.task_id),
            batch_id=context.batch_id,
            task_type=context.task_type,
            event_type=event_type,
            stage=None,
            status=status_by_event[event_type],
            sequence=sequence,
            progress=progress,
            source_file_id=str(context.source_file_id),
            target_file_ids=tuple(str(item) for item in target_file_ids),
            result_summary=result_summary,
            error=error,
        )

    async def _notify(
        self,
        callback: TaskCallback | None,
        event: TaskEvent,
        *,
        context: TaskExecutionContext,
    ) -> None:
        if callback is None:
            return
        try:
            result = callback(event)
            if inspect.isawaitable(result):
                await result
            logger.debug(
                "knowledge_entity_processing_service callback delivered: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s, event_type=%s, status=%s",
                context.batch_id,
                context.task_id,
                context.knowledge_base_id,
                context.kb_code,
                context.source_file_id,
                context.file_path,
                context.task_type.value,
                event.event_type,
                event.status.value,
            )
        except Exception as exc:
            logger.warning(
                "knowledge_entity_processing_service callback failed: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s, event_type=%s, status=%s, error_type=%s",
                context.batch_id,
                context.task_id,
                context.knowledge_base_id,
                context.kb_code,
                context.source_file_id,
                context.file_path,
                context.task_type.value,
                event.event_type,
                event.status.value,
                type(exc).__name__,
                exc_info=True,
            )

    def _batch_id(self, task_type: ProcessingTaskType) -> str:
        prefix = "ed" if task_type == ProcessingTaskType.ENTITY_DISCOVERY else "ee"
        return f"{prefix}-{uuid4().hex}"

    def _inside_entity_directory(self, path: str) -> bool:
        return path == "/KnowledgeEntity" or path.startswith("/KnowledgeEntity/")

    def _row_id(self, row: Mapping[str, Any]) -> int:
        return int(row["kid"] if "kid" in row else row["id"])


@dataclass(frozen=True)
class _Evaluation:
    result: ProcessingEligibilityResult
    input_fingerprint: str
    method_version: str
