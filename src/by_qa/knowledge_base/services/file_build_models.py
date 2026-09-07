"""Domain values for durable file-build processing."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FileBuildTaskStatus(StrEnum):
    """Public lifecycle of one file-build attempt."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    UNSUPPORTED = "UNSUPPORTED"


class FileBuildStage(StrEnum):
    """Persisted build milestones."""

    ACCEPTED = "ACCEPTED"
    EXTRACTING = "EXTRACTING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    COMMITTING = "COMMITTING"


class FileBuildBatchStatus(StrEnum):
    """Aggregate lifecycle for newly accepted file tasks."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"


class FileBuildScope(StrEnum):
    """Target shape captured by one build request."""

    SINGLE_FILE = "SINGLE_FILE"
    DIRECTORY = "DIRECTORY"


class FileBuildOrigin(StrEnum):
    """The workflow that requested a file build."""

    API = "API"
    ENTITY_DISCOVERY = "ENTITY_DISCOVERY"
    ENTITY_ENRICH = "ENTITY_ENRICH"


class FileBuildExecutionMode(StrEnum):
    """Whether a task is claimed by the runner or executed by its owner."""

    BACKGROUND = "BACKGROUND"
    INLINE = "INLINE"


class FileBuildErrorCode(StrEnum):
    """Stable terminal reason codes introduced by background processing."""

    INPUT_STALE = "INPUT_STALE"
    SOURCE_DELETED = "SOURCE_DELETED"
    SOURCE_NOT_READY = "SOURCE_NOT_READY"
    SUPERSEDED = "SUPERSEDED"
    KNOWLEDGE_BASE_DELETED = "KNOWLEDGE_BASE_DELETED"
    TASK_TIMEOUT = "TASK_TIMEOUT"
    WORKER_LOST = "WORKER_LOST"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    BUILD_FAILED = "BUILD_FAILED"
    MIGRATION_INTERRUPTED = "MIGRATION_INTERRUPTED"


TERMINAL_FILE_BUILD_STATUSES = frozenset(
    {
        FileBuildTaskStatus.SUCCEEDED,
        FileBuildTaskStatus.FAILED,
        FileBuildTaskStatus.SKIPPED,
        FileBuildTaskStatus.UNSUPPORTED,
    }
)

REUSABLE_FILE_BUILD_STATUSES = frozenset(
    {
        FileBuildTaskStatus.PENDING,
        FileBuildTaskStatus.RUNNING,
        FileBuildTaskStatus.SUCCEEDED,
        FileBuildTaskStatus.UNSUPPORTED,
    }
)

FILE_BUILD_STAGE_PROGRESS = {
    FileBuildStage.ACCEPTED: 0,
    FileBuildStage.EXTRACTING: 10,
    FileBuildStage.CHUNKING: 35,
    FileBuildStage.EMBEDDING: 55,
    FileBuildStage.COMMITTING: 90,
}


class _BuildProfileModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ParserBuildProfile(_BuildProfileModel):
    name: str = "document_chunking_service"
    version: str = "1"


class ChunkingBuildProfile(_BuildProfileModel):
    size: int = Field(default=512, gt=0)
    overlap: int = Field(default=64, ge=0)

    @model_validator(mode="after")
    def validate_overlap(self) -> "ChunkingBuildProfile":
        if self.overlap >= self.size:
            raise ValueError("chunk overlap must be smaller than chunk size")
        return self


class EmbeddingBuildProfile(_BuildProfileModel):
    model: str = Field(min_length=1)
    dimension: int = Field(gt=0)


class RetrievalBuildProfile(_BuildProfileModel):
    schema_version: int = Field(
        default=1,
        ge=1,
        serialization_alias="schemaVersion",
    )


class FileBuildProfile(_BuildProfileModel):
    """Readable configuration snapshot used to identify equivalent builds."""

    profile_version: int = Field(
        default=1,
        ge=1,
        serialization_alias="profileVersion",
    )
    parser: ParserBuildProfile = Field(default_factory=ParserBuildProfile)
    chunking: ChunkingBuildProfile = Field(default_factory=ChunkingBuildProfile)
    embedding: EmbeddingBuildProfile
    retrieval: RetrievalBuildProfile = Field(default_factory=RetrievalBuildProfile)

    def canonical_json(self) -> str:
        """Return the stable JSON representation used by the reuse hash."""
        return json.dumps(
            self.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def sha256(self) -> str:
        """Hash the canonical UTF-8 representation."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def storage_value(self) -> dict[str, object]:
        """Return the readable JSONB value with all defaults materialized."""
        return self.model_dump(mode="json", by_alias=True)
