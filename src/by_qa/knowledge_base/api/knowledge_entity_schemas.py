"""API contracts for KnowledgeEntity discovery, enrichment, and relations."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class _ApiModel(BaseModel):
    """Base model shared by the strict KnowledgeEntity HTTP contracts."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def _validate_file_path(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized.startswith("/"):
        raise ValueError("filePath must start with '/'")
    if normalized == "/":
        raise ValueError("filePath must identify a file")
    if any(part == ".." for part in normalized.split("/")):
        raise ValueError("filePath must not contain '..'")
    return "/" + "/".join(part for part in normalized.split("/") if part)


class ProcessingCapability(StrEnum):
    ENTITY_DISCOVERY = "entityDiscovery"
    ENTITY_ENRICH = "entityEnrich"


class ProcessingEligibility(StrEnum):
    ELIGIBLE_AND_STALE = "ELIGIBLE_AND_STALE"
    ELIGIBLE_BUT_FRESH = "ELIGIBLE_BUT_FRESH"
    INELIGIBLE = "INELIGIBLE"


class ProcessingTaskType(StrEnum):
    ENTITY_DISCOVERY = "ENTITY_DISCOVERY"
    DOCUMENT_ENRICH = "DOCUMENT_ENRICH"


class ProcessingTaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class ProcessingScope(StrEnum):
    SINGLE_FILE = "SINGLE_FILE"
    WHOLE_KB = "WHOLE_KB"


class ProcessingBatchStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"


class SemanticRelationCode(StrEnum):
    MENTIONS = "MENTIONS"
    PART_OF = "PART_OF"
    IS_A = "IS_A"
    DEPENDS_ON = "DEPENDS_ON"


class SemanticRelationDirection(StrEnum):
    OUTGOING = "OUTGOING"
    INCOMING = "INCOMING"
    BOTH = "BOTH"


class ProcessingEligibilityRequest(_ApiModel):
    """Request for evaluating whether a single file needs processing."""

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("filePath", "file_path"),
    )
    capability: ProcessingCapability
    _normalize_file_path = field_validator("file_path")(_validate_file_path)


class EntityDiscoveryRequest(_ApiModel):
    """Request for single-file or whole-knowledge-base entity discovery."""

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("filePath", "file_path"),
    )
    max_entities: int = Field(
        default=12,
        ge=1,
        le=12,
        validation_alias=AliasChoices("maxEntities", "max_entities"),
    )
    force: bool = False
    _normalize_file_path = field_validator("file_path")(_validate_file_path)


class EntityEnrichRequest(_ApiModel):
    """Request for single-file or whole-knowledge-base entity enrichment."""

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("filePath", "file_path"),
    )
    top_k: int = Field(
        default=20,
        ge=1,
        le=100,
        validation_alias=AliasChoices("topK", "top_k"),
    )
    force: bool = False
    _normalize_file_path = field_validator("file_path")(_validate_file_path)


class DeleteKnowledgeEntityRequest(_ApiModel):
    """Delete one canonical entity and its optional file anchor."""

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    entity_id: int = Field(
        gt=0,
        validation_alias=AliasChoices("entityId", "entity_id"),
    )


class DeleteKnowledgeEntityAliasRequest(DeleteKnowledgeEntityRequest):
    """Delete one alias owned by a canonical entity."""

    alias_id: int = Field(
        gt=0,
        validation_alias=AliasChoices("aliasId", "alias_id"),
    )


class ProcessingTaskStatusRequest(_ApiModel):
    """Knowledge-base-scoped processing task query."""

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("filePath", "file_path"),
    )
    task_type: ProcessingTaskType | None = Field(
        default=None,
        validation_alias=AliasChoices("taskType", "task_type"),
    )
    batch_id: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("batchId", "batch_id"),
    )
    status_list: list[ProcessingTaskStatus] | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("statusList", "status_list"),
    )
    latest_only: bool = Field(
        default=True,
        validation_alias=AliasChoices("latestOnly", "latest_only"),
    )
    include_details: bool = Field(
        default=False,
        validation_alias=AliasChoices("includeDetails", "include_details"),
    )
    page_num: int = Field(
        default=1,
        ge=1,
        validation_alias=AliasChoices("pageNum", "page_num"),
    )
    page_size: int = Field(
        default=50,
        ge=1,
        le=500,
        validation_alias=AliasChoices("pageSize", "page_size"),
    )

    _normalize_file_path = field_validator("file_path")(_validate_file_path)

    @field_validator("status_list")
    @classmethod
    def validate_unique_statuses(
        cls, value: list[ProcessingTaskStatus] | None
    ) -> list[ProcessingTaskStatus] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("statusList must not contain duplicate values")
        return value


class ProcessingBatchStatusRequest(_ApiModel):
    """Query one durable batch and its per-file progress."""

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    batch_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("batchId", "batch_id"),
    )
    include_details: bool = Field(
        default=False,
        validation_alias=AliasChoices("includeDetails", "include_details"),
    )
    page_num: int = Field(
        default=1,
        ge=1,
        validation_alias=AliasChoices("pageNum", "page_num"),
    )
    page_size: int = Field(
        default=50,
        ge=1,
        le=500,
        validation_alias=AliasChoices("pageSize", "page_size"),
    )


class SemanticRelationsRequest(_ApiModel):
    """Request for permission-filtered semantic document relations."""

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("filePath", "file_path"),
    )
    direction: SemanticRelationDirection = SemanticRelationDirection.BOTH
    relation_code_list: list[SemanticRelationCode] | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("relationCodeList", "relation_code_list"),
    )
    page_num: int = Field(
        default=1,
        ge=1,
        validation_alias=AliasChoices("pageNum", "page_num"),
    )
    page_size: int = Field(
        default=50,
        ge=1,
        le=500,
        validation_alias=AliasChoices("pageSize", "page_size"),
    )

    _normalize_file_path = field_validator("file_path")(_validate_file_path)

    @field_validator("relation_code_list")
    @classmethod
    def validate_unique_relation_codes(
        cls, value: list[SemanticRelationCode] | None
    ) -> list[SemanticRelationCode] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("relationCodeList must not contain duplicate values")
        return value


class ProcessingEligibilityResult(_ApiModel):
    file_id: str = Field(serialization_alias="fileId")
    kb_code: str = Field(serialization_alias="knCode")
    file_path: str = Field(serialization_alias="filePath")
    document_kind: str = Field(serialization_alias="documentKind")
    capability: ProcessingCapability
    eligibility: ProcessingEligibility
    reason_code: str = Field(serialization_alias="reasonCode")
    last_successful_task_id: str | None = Field(
        default=None, serialization_alias="lastSuccessfulTaskId"
    )
    last_successful_at: datetime | None = Field(
        default=None, serialization_alias="lastSuccessfulAt"
    )


class ProcessingTaskSummary(_ApiModel):
    task_id: str = Field(serialization_alias="taskId")
    status: ProcessingTaskStatus
    file_id: str = Field(serialization_alias="fileId")
    file_path: str = Field(serialization_alias="filePath")
    reused: bool = False


class ProcessingBatchAccepted(_ApiModel):
    batch_id: str = Field(serialization_alias="batchId")
    scope: ProcessingScope
    task_type: ProcessingTaskType = Field(serialization_alias="taskType")
    eligible_count: int = Field(ge=0, serialization_alias="eligibleCount")
    accepted_count: int = Field(ge=0, serialization_alias="acceptedCount")
    reused_count: int = Field(ge=0, serialization_alias="reusedCount")
    skipped_count: int = Field(ge=0, serialization_alias="skippedCount")
    tasks: list[ProcessingTaskSummary]


class ProcessingTaskItem(_ApiModel):
    task_id: str = Field(serialization_alias="taskId")
    batch_id: str | None = Field(default=None, serialization_alias="batchId")
    task_type: ProcessingTaskType = Field(serialization_alias="taskType")
    status: ProcessingTaskStatus
    current_stage: str | None = Field(default=None, serialization_alias="currentStage")
    progress: int | None = Field(default=None, ge=0, le=100)
    file_id: str | None = Field(serialization_alias="fileId")
    file_path: str = Field(serialization_alias="filePath")
    index_version: str | None = Field(default=None, serialization_alias="indexVersion")
    created_at: datetime = Field(serialization_alias="createdAt")
    started_at: datetime | None = Field(default=None, serialization_alias="startedAt")
    finished_at: datetime | None = Field(default=None, serialization_alias="finishedAt")
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class ProcessingTaskPage(_ApiModel):
    knowledge_base_id: str = Field(serialization_alias="knowledgeBaseId")
    kb_code: str = Field(serialization_alias="knCode")
    file_path: str | None = Field(default=None, serialization_alias="filePath")
    total: int = Field(ge=0)
    page_num: int = Field(ge=1, serialization_alias="pageNum")
    page_size: int = Field(ge=1, serialization_alias="pageSize")
    data: list[ProcessingTaskItem]


class ProcessingBatchStatusResult(_ApiModel):
    batch_id: str = Field(serialization_alias="batchId")
    knowledge_base_id: str = Field(serialization_alias="knowledgeBaseId")
    kb_code: str = Field(serialization_alias="knCode")
    task_type: ProcessingTaskType = Field(serialization_alias="taskType")
    scope: ProcessingScope
    status: ProcessingBatchStatus
    version: int = Field(ge=0)
    total_count: int = Field(ge=0, serialization_alias="totalCount")
    completed_count: int = Field(ge=0, serialization_alias="completedCount")
    pending_count: int = Field(ge=0, serialization_alias="pendingCount")
    running_count: int = Field(ge=0, serialization_alias="runningCount")
    succeeded_count: int = Field(ge=0, serialization_alias="succeededCount")
    failed_count: int = Field(ge=0, serialization_alias="failedCount")
    skipped_count: int = Field(ge=0, serialization_alias="skippedCount")
    progress: int = Field(ge=0, le=100)
    created_at: datetime = Field(serialization_alias="createdAt")
    completed_at: datetime | None = Field(
        default=None, serialization_alias="completedAt"
    )
    page_num: int = Field(ge=1, serialization_alias="pageNum")
    page_size: int = Field(ge=1, serialization_alias="pageSize")
    data: list[ProcessingTaskItem]


class SemanticRelationEndpoint(_ApiModel):
    file_id: str = Field(serialization_alias="fileId")
    kb_code: str = Field(serialization_alias="knCode")
    file_path: str = Field(serialization_alias="filePath")
    document_kind: str = Field(serialization_alias="documentKind")


class RelationAssertionEvidence(_ApiModel):
    """Representative lightweight evidence from one physical assertion."""

    producer_run_id: str | None = Field(
        default=None, serialization_alias="producerRunId"
    )
    evidence_fingerprint: str | None = Field(
        default=None, serialization_alias="evidenceFingerprint"
    )
    source_heading_path: str | None = Field(
        default=None, serialization_alias="sourceHeadingPath"
    )
    start_line: int | None = Field(default=None, ge=1, serialization_alias="startLine")
    end_line: int | None = Field(default=None, ge=1, serialization_alias="endLine")
    start_offset: int | None = Field(
        default=None, ge=0, serialization_alias="startOffset"
    )
    end_offset: int | None = Field(default=None, ge=0, serialization_alias="endOffset")


class SemanticRelationItem(_ApiModel):
    relation_id: str = Field(serialization_alias="relationId")
    relation_code: SemanticRelationCode = Field(serialization_alias="relationCode")
    direction: SemanticRelationDirection
    source: SemanticRelationEndpoint
    target: SemanticRelationEndpoint
    assertion_count: int = Field(ge=1, serialization_alias="assertionCount")
    confidence: float = Field(ge=0, le=1)
    discovered_by: str = Field(serialization_alias="discoveredBy")
    source_task_id: str | None = Field(default=None, serialization_alias="sourceTaskId")
    representative_evidence: RelationAssertionEvidence | None = Field(
        default=None, serialization_alias="representativeEvidence"
    )


class SemanticRelationPage(_ApiModel):
    file_id: str = Field(serialization_alias="fileId")
    total: int = Field(ge=0)
    page_num: int = Field(ge=1, serialization_alias="pageNum")
    page_size: int = Field(ge=1, serialization_alias="pageSize")
    data: list[SemanticRelationItem]


class KnowledgeEntityDeleteResult(_ApiModel):
    deleted_entity_count: int = Field(ge=0, serialization_alias="deletedEntityCount")
    deleted_alias_count: int = Field(ge=0, serialization_alias="deletedAliasCount")
    deleted_file_count: int = Field(ge=0, serialization_alias="deletedFileCount")


KnowledgeEntityServiceResult = (
    ProcessingEligibilityResult
    | ProcessingBatchAccepted
    | ProcessingBatchStatusResult
    | ProcessingTaskPage
    | SemanticRelationPage
    | KnowledgeEntityDeleteResult
    | dict[str, Any]
)


class KnowledgeEntityProcessingService(Protocol):
    """Dependency-provider contract consumed by the HTTP route adapter."""

    async def evaluate_processing_eligibility(
        self, request: ProcessingEligibilityRequest
    ) -> ProcessingEligibilityResult | dict[str, Any]: ...

    async def discover_knowledge_entities(
        self,
        request: EntityDiscoveryRequest,
    ) -> ProcessingBatchAccepted | dict[str, Any]: ...

    async def enrich_knowledge_entities(
        self,
        request: EntityEnrichRequest,
    ) -> ProcessingBatchAccepted | dict[str, Any]: ...

    async def get_processing_task_status(
        self, request: ProcessingTaskStatusRequest
    ) -> ProcessingTaskPage | dict[str, Any]: ...

    async def get_processing_batch_status(
        self, request: ProcessingBatchStatusRequest
    ) -> ProcessingBatchStatusResult | dict[str, Any]: ...

    async def get_semantic_relations(
        self, request: SemanticRelationsRequest
    ) -> SemanticRelationPage | dict[str, Any]: ...

    async def delete_knowledge_entity(
        self, request: DeleteKnowledgeEntityRequest
    ) -> KnowledgeEntityDeleteResult | dict[str, Any]: ...

    async def delete_knowledge_entity_alias(
        self, request: DeleteKnowledgeEntityAliasRequest
    ) -> KnowledgeEntityDeleteResult | dict[str, Any]: ...
