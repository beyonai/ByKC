"""Application orchestration for KnowledgeEntity processing tasks."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from by_qa.core import logger
from by_qa.knowledge_base.api.knowledge_entity_schemas import (
    DeleteKnowledgeEntityAliasRequest,
    DeleteKnowledgeEntityRequest,
    EntityDiscoveryRequest,
    EntityEnrichRequest,
    KnowledgeEntityDeleteResult,
    ProcessingBatchAccepted,
    ProcessingBatchStatus,
    ProcessingBatchStatusRequest,
    ProcessingBatchStatusResult,
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
from by_qa.knowledge_base.events import (
    KnowledgeEventPublisherInvoker,
    build_semantic_empty_batch_event,
    build_semantic_terminal_events,
    normalize_json_mapping,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.knowledge_entity_discovery import (
    DISCOVERY_PROMPT_HASH,
    DISCOVERY_PROTOCOL_VERSION,
)

DISCOVERY_METHOD_VERSION = f"discovery/2.0+{DISCOVERY_PROMPT_HASH[:12]}"
ENRICH_METHOD_VERSION = "enrich/2.0"
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


@dataclass
class KnowledgeEntityProcessingOrchestrator:
    connection_factory: Callable[[], Awaitable[Any]]
    knowledge_base_repository: Any
    knowledge_entity_repository: Any
    knowledge_semantic_processing_task_repository: Any
    knowledge_semantic_processing_batch_repository: Any
    knowledge_file_reference_repository: Any
    worker: Any
    event_publisher_invoker: KnowledgeEventPublisherInvoker = field(
        default_factory=KnowledgeEventPublisherInvoker
    )
    background_runner: Any | None = None
    knowledge_entity_asset_service: Any | None = None

    async def start(self) -> None:
        if self.background_runner is not None:
            await self.background_runner.start()

    async def stop(self) -> None:
        if self.background_runner is not None:
            await self.background_runner.stop()

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
    ) -> ProcessingBatchAccepted:
        return await self._accept(
            request=request,
            capability=ProcessingCapability.ENTITY_DISCOVERY,
            task_type=ProcessingTaskType.ENTITY_DISCOVERY,
        )

    async def enrich_knowledge_entities(
        self,
        request: EntityEnrichRequest,
    ) -> ProcessingBatchAccepted:
        return await self._accept(
            request=request,
            capability=ProcessingCapability.ENTITY_ENRICH,
            task_type=ProcessingTaskType.DOCUMENT_ENRICH,
        )

    async def delete_knowledge_entity(
        self, request: DeleteKnowledgeEntityRequest
    ) -> KnowledgeEntityDeleteResult:
        if self.knowledge_entity_asset_service is None:
            raise KnowledgeBaseValidationError(
                "knowledge entity asset service is not configured"
            )
        result = await self.knowledge_entity_asset_service.delete_entity(
            kb_code=request.kb_code,
            entity_id=request.entity_id,
        )
        return KnowledgeEntityDeleteResult.model_validate(result)

    async def delete_knowledge_entity_alias(
        self, request: DeleteKnowledgeEntityAliasRequest
    ) -> KnowledgeEntityDeleteResult:
        if self.knowledge_entity_asset_service is None:
            raise KnowledgeBaseValidationError(
                "knowledge entity asset service is not configured"
            )
        result = await self.knowledge_entity_asset_service.delete_alias(
            kb_code=request.kb_code,
            entity_id=request.entity_id,
            alias_id=request.alias_id,
        )
        return KnowledgeEntityDeleteResult.model_validate(result)

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

    async def get_processing_batch_status(
        self, request: ProcessingBatchStatusRequest
    ) -> ProcessingBatchStatusResult:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            knowledge_base_id = await self._resolve_kb(cursor, request.kb_code)
            batch = await self.knowledge_semantic_processing_batch_repository.get_batch(
                cursor,
                batch_id=request.batch_id,
                knowledge_base_id=knowledge_base_id,
            )
            if batch is None:
                raise KnowledgeBaseValidationError(
                    f"processing batch not found: {request.batch_id}"
                )
            counts = await self.knowledge_semantic_processing_batch_repository.count_tasks_by_status(
                cursor, batch_id=request.batch_id
            )
            rows = await self.knowledge_semantic_processing_task_repository.list_processing_tasks(
                cursor,
                knowledge_base_id=knowledge_base_id,
                batch_id=request.batch_id,
                latest_only=False,
                limit=request.page_size,
                offset=(request.page_num - 1) * request.page_size,
            )
            total_count = int(batch["total_count"])
            completed_count = int(batch["completed_count"])
            return ProcessingBatchStatusResult(
                batch_id=str(batch["batch_id"]),
                knowledge_base_id=str(knowledge_base_id),
                kb_code=request.kb_code,
                task_type=ProcessingTaskType(str(batch["task_type"]).upper()),
                scope=ProcessingScope(str(batch["scope"]).upper()),
                status=ProcessingBatchStatus(str(batch["status"]).upper()),
                version=int(batch["version"]),
                total_count=total_count,
                completed_count=completed_count,
                pending_count=int(counts.get("pending", 0)),
                running_count=int(counts.get("running", 0)),
                succeeded_count=int(counts.get("succeeded", 0)),
                failed_count=int(counts.get("failed", 0)),
                skipped_count=int(counts.get("skipped", 0)),
                progress=(
                    100 if total_count == 0 else completed_count * 100 // total_count
                ),
                created_at=batch["created_at"],
                completed_at=batch.get("completed_at"),
                page_num=request.page_num,
                page_size=request.page_size,
                data=[self._task_item(row, request.include_details) for row in rows],
            )
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
    ) -> ProcessingBatchAccepted:
        batch_id = self._batch_id(task_type)
        acceptance_started_at = time.perf_counter()
        scope = (
            ProcessingScope.SINGLE_FILE
            if request.file_path
            else ProcessingScope.WHOLE_KB
        )
        logger.info(
            "knowledge_entity_processing_service batch acceptance started: batch_id=%s, kb_code=%s, file_path=%s, task_type=%s, scope=%s, force=%s",
            batch_id,
            request.kb_code,
            request.file_path,
            task_type.value,
            scope.value,
            request.force,
        )
        connection = await self.connection_factory()
        summaries: list[ProcessingTaskSummary] = []
        terminal_callbacks: list[
            tuple[dict[str, Any], dict[str, Any], dict[str, int]]
        ] = []
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
            batch = (
                await self.knowledge_semantic_processing_batch_repository.create_batch(
                    cursor,
                    batch_id=batch_id,
                    knowledge_base_id=knowledge_base_id,
                    task_type=task_type.value,
                    scope=scope.value,
                    total_count=len(files),
                )
            )
            if batch is None:
                raise RuntimeError("failed to create semantic processing batch")
            params = request.model_dump(
                mode="json",
                by_alias=True,
                exclude={"extra_params"},
            )
            for file_row in files:
                file_id = self._row_id(file_row)
                file_path = str(file_row["file_path"])
                skip_reason: str | None = None
                reused_task_id: int | None = None
                evaluation: _Evaluation | None = None
                if (
                    capability == ProcessingCapability.ENTITY_DISCOVERY
                    and self._inside_entity_directory(file_path)
                ):
                    skip_reason = "ENTITY_DIRECTORY_EXCLUDED"
                    reason_counts["ENTITY_DIRECTORY_EXCLUDED"] += 1
                else:
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
                    if (
                        evaluation.result.eligibility
                        == ProcessingEligibility.INELIGIBLE
                    ):
                        skip_reason = evaluation.result.reason_code or "INELIGIBLE"
                    else:
                        eligible_count += 1
                        await self._lock_file(cursor, file_id)
                        reusable = await self._find_active_task(
                            cursor,
                            knowledge_base_id,
                            file_id,
                            task_type,
                        )
                        if reusable is not None:
                            skip_reason = "ALREADY_PROCESSING"
                            reused_task_id = self._row_id(reusable)
                        elif not request.force:
                            reusable = await self._find_fresh_task(
                                cursor,
                                knowledge_base_id,
                                file_id,
                                task_type,
                                evaluation.input_fingerprint,
                            )
                            if reusable is not None:
                                skip_reason = "INPUT_UNCHANGED"
                                reused_task_id = self._row_id(reusable)
                status = "skipped" if skip_reason else "pending"
                if skip_reason:
                    skipped_count += 1
                if reused_task_id is not None:
                    reused_count += 1
                result_payload = None
                if skip_reason:
                    result_payload = {"reasonCode": skip_reason}
                    if reused_task_id is not None:
                        key = (
                            "activeTaskId"
                            if skip_reason == "ALREADY_PROCESSING"
                            else "reusedTaskId"
                        )
                        result_payload[key] = str(reused_task_id)
                created = await self.knowledge_semantic_processing_task_repository.create_processing_task(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    fs_entry_id=file_id,
                    task_type=task_type.value,
                    status=status,
                    batch_id=batch_id,
                    file_path_snapshot=file_path,
                    current_stage="skipped" if skip_reason else "accepted",
                    progress=100 if skip_reason else 0,
                    input_fingerprint=(
                        evaluation.input_fingerprint if evaluation is not None else None
                    ),
                    input_checksum=file_row.get("checksum"),
                    method_version=(
                        evaluation.method_version if evaluation is not None else None
                    ),
                    protocol_version=(
                        evaluation.protocol_version if evaluation is not None else None
                    ),
                    request_params=params,
                    result_payload=result_payload,
                )
                if created is None:
                    raise RuntimeError("failed to create processing task")
                logger.info(
                    "knowledge_entity_processing_service task created: batch_id=%s, task_id=%s, knowledge_base_id=%s, kb_code=%s, file_id=%s, file_path=%s, task_type=%s, status=%s",
                    batch_id,
                    self._row_id(created),
                    knowledge_base_id,
                    request.kb_code,
                    file_id,
                    file_path,
                    task_type.value,
                    status,
                )
                summaries.append(
                    ProcessingTaskSummary(
                        task_id=str(self._row_id(created)),
                        status=ProcessingTaskStatus(status.upper()),
                        file_id=str(file_id),
                        file_path=file_path,
                        reused=reused_task_id is not None,
                    )
                )
                if skip_reason:
                    batch = await self.knowledge_semantic_processing_batch_repository.advance_batch(
                        cursor, batch_id=batch_id
                    )
                    if batch is None:
                        raise RuntimeError("failed to advance batch for skipped task")
                    counts = await self.knowledge_semantic_processing_batch_repository.count_tasks_by_status(
                        cursor, batch_id=batch_id
                    )
                    terminal_callbacks.append((created, batch, counts))
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
            len(files) - skipped_count,
            reused_count,
            skipped_count,
            dict(eligibility_counts),
            dict(reason_counts),
            (time.perf_counter() - acceptance_started_at) * 1000,
        )
        for task, terminal_batch, counts in terminal_callbacks:
            await self.event_publisher_invoker.publish_all(
                build_semantic_terminal_events(task, terminal_batch, counts)
            )
        if not files:
            await self.event_publisher_invoker.publish(
                build_semantic_empty_batch_event(
                    batch=batch,
                    task_type=task_type.value,
                    kb_code=request.kb_code,
                )
            )
        return ProcessingBatchAccepted(
            batch_id=batch_id,
            scope=scope,
            task_type=task_type,
            eligible_count=eligible_count,
            accepted_count=len(files) - skipped_count,
            reused_count=reused_count,
            skipped_count=skipped_count,
            tasks=summaries,
        )

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
                reason = "INPUT_CHANGED"
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
            protocol_version=(
                DISCOVERY_PROTOCOL_VERSION
                if capability == ProcessingCapability.ENTITY_DISCOVERY
                else None
            ),
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
            (row for row in rows if row.get("input_fingerprint") == fingerprint),
            None,
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
            value = {"sourceFileChecksum": file_row.get("checksum")}
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
            file_id=(
                str(row["fs_entry_id"]) if row.get("fs_entry_id") is not None else None
            ),
            file_path=str(row["file_path"]),
            index_version=row.get("index_version"),
            created_at=row["created_at"],
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            result=(
                normalize_json_mapping(row.get("result_payload"))
                if include_details
                else None
            ),
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
    protocol_version: str | None
