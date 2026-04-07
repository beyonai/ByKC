"""Pydantic schemas for knowledge base APIs."""

import json
from pathlib import PurePosixPath
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload

Status = Literal["ACTIVE", "INACTIVE"]


class CreateKnowledgeBaseRequest(BaseModel):
    """Request body for creating a knowledge base."""

    kb_code: str
    kb_name: str
    kb_description: str | None = None
    status: Status = "ACTIVE"
    metadata: dict[str, Any] | None = None


class CreateKnowledgeBaseResponse(BaseModel):
    """Business response for knowledge base creation."""

    kb_code: str
    kb_name: str
    kb_description: str | None = None
    status: Status
    metadata: dict[str, Any] | None = None


class DeleteKnowledgeBaseRequest(BaseModel):
    """Request body for logically deleting a knowledge base."""

    kb_code: str = Field(min_length=1)


class DeleteKnowledgeBaseResponse(BaseModel):
    """Business response for logically deleting a knowledge base."""

    kb_code: str
    is_deleted: bool


class WriteFileRequest(BaseModel):
    """Request body for writing a file into a knowledge base."""

    kb_code: str = Field(min_length=1)
    file_code: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    file_description: str | None = None
    file_content: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_code: str = Field(min_length=1)
    status: Status = "ACTIVE"
    metadata: dict[str, Any] | None = None


class WriteFileResponse(BaseModel):
    """Business response for successfully writing a file."""

    kb_code: str
    file_code: str
    type_code: str
    file_path: str
    file_description: str | None = None
    version: str
    status: Status
    metadata: dict[str, Any] | None = None


class DeleteKnowledgeItemRequest(BaseModel):
    """Request body for logically deleting a knowledge item."""

    kb_code: str = Field(min_length=1)
    file_code: str = Field(min_length=1)


class DeleteKnowledgeItemResponse(BaseModel):
    """Business response for logically deleting a knowledge item."""

    kb_code: str
    file_code: str
    is_deleted: bool


class WriteIndexRequest(BaseModel):
    """Request body for writing chunk indexes for an existing file version."""

    kb_code: str = Field(min_length=1)
    file_code: str = Field(min_length=1)
    version: str = Field(min_length=1)
    markdown_content: str = Field(min_length=1)
    chunks: list["KnowledgeItemChunkPayload"] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_chunk_numbers(self) -> "WriteIndexRequest":
        """Reject duplicate chunk numbers within one request."""
        chunk_numbers = [chunk.chunk_no for chunk in self.chunks]
        if len(chunk_numbers) != len(set(chunk_numbers)):
            raise ValueError("chunk_no must be unique within one write-index request")
        return self


class WriteIndexResponse(BaseModel):
    """Business response for successfully writing indexes."""

    class ChunkSummary(BaseModel):
        """Chunk summary for write-index responses."""

        count: int

    kb_code: str
    file_code: str
    version: str
    chunks: ChunkSummary


class KnowledgeItemImportRequest(BaseModel):
    """Request body for atomically importing a file, markdown sidecar, and chunks."""

    kb_code: str = Field(min_length=1)
    file_code: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    file_description: str | None = None
    file_content: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_code: str = Field(min_length=1)
    status: Status = "ACTIVE"
    metadata: dict[str, Any] | None = None
    markdown_content: str = Field(min_length=1)
    chunks: list["KnowledgeItemChunkPayload"] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_chunk_numbers(self) -> "KnowledgeItemImportRequest":
        """Reject duplicate chunk numbers within one combined import request."""
        chunk_numbers = [chunk.chunk_no for chunk in self.chunks]
        if len(chunk_numbers) != len(set(chunk_numbers)):
            raise ValueError("chunk_no must be unique within one import request")
        return self


class KnowledgeItemImportFileResponse(BaseModel):
    """Business response for the combined import endpoint."""

    class ChunkSummary(BaseModel):
        """Chunk summary for combined import responses."""

        count: int

    kb_code: str
    file_code: str
    type_code: str
    file_path: str
    file_description: str | None = None
    version: str
    status: Status
    metadata: dict[str, Any] | None = None
    chunks: ChunkSummary


class KnowledgeItemDocumentPayload(BaseModel):
    """Document metadata carried in the import manifest."""

    item_code: str
    full_path: str
    status: Status = "ACTIVE"
    source_code: str
    type_code: str
    version: str
    metadata: dict[str, Any] | None = None


class KnowledgeItemImportManifest(BaseModel):
    """Import manifest parsed from the multipart request."""

    kb_code: str
    document: KnowledgeItemDocumentPayload
    chunks: list[KnowledgeItemChunkPayload] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_chunk_numbers(self) -> "KnowledgeItemImportManifest":
        """Reject duplicate chunk numbers within one request."""
        chunk_numbers = [chunk.chunk_no for chunk in self.chunks]
        if len(chunk_numbers) != len(set(chunk_numbers)):
            raise ValueError("chunk_no must be unique within one import request")
        return self


class KnowledgeItemImportResponse(BaseModel):
    """Business response for successful document import."""

    kb_code: str
    full_path: str
    version: str
    status: Status
    chunk_count: int


class KnowledgeItemListDirRequest(BaseModel):
    """Request body for virtual filesystem listing."""

    kb_codes: list[str] = Field(...)
    path: str = "/"
    source_codes: Optional[list[str]] = None
    type_codes: Optional[list[str]] = None


class KnowledgeItemListDirItem(BaseModel):
    """Single filesystem entry returned by list_dir."""

    kb_code: str
    name: str
    type: Literal["file", "directory"]
    size: int = 0


class KnowledgeItemListDirResponse(BaseModel):
    """Business response for list_dir."""

    items: list[KnowledgeItemListDirItem]


class KnowledgeItemGlobRequest(BaseModel):
    """Request body for path-pattern based filesystem matching."""

    kb_codes: list[str] = Field(min_length=1)
    path: str = Field(min_length=1)
    source_codes: Optional[list[str]] = None
    type_codes: Optional[list[str]] = None


class KnowledgeItemFetchRequest(BaseModel):
    """Request body for fetching file content by line range."""

    kb_codes: list[str] = Field(min_length=1)
    path: str = Field(min_length=1)
    content_type: Literal["original", "markdown"] = "markdown"
    start_line: int | None = None
    end_line: int | None = None

    @model_validator(mode="after")
    def validate_line_window(self) -> "KnowledgeItemFetchRequest":
        """Line windows are optional for markdown full reads and unused for originals."""
        has_start = self.start_line is not None
        has_end = self.end_line is not None
        if self.content_type == "original":
            return self
        if has_start != has_end:
            raise ValueError("start_line and end_line must be provided together")
        return self


class KnowledgeItemFetchResponse(BaseModel):
    """Business response for fetch."""

    kb_code: str
    path: str
    content_type: Literal["original", "markdown"]
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    data: Optional[str] = None
    reached_eof: Optional[bool] = None
    url: Optional[str] = None


class KnowledgeItemSearchRequest(BaseModel):
    """Request body for chunk-level hybrid retrieval."""

    query: str = Field(min_length=1)
    kb_codes: list[str] = Field(min_length=1)
    top_k: int = 10
    vector_top_k: int = 40
    text_top_k: int = 30
    source_codes: Optional[list[str]] = None
    type_codes: Optional[list[str]] = None

    @model_validator(mode="after")
    def validate_candidate_limits(self) -> "KnowledgeItemSearchRequest":
        """Candidate recall limits should not be smaller than top_k."""
        if self.top_k <= 0:
            raise ValueError("top_k must be greater than 0")
        if self.vector_top_k < self.top_k:
            raise ValueError("vector_top_k must be greater than or equal to top_k")
        if self.text_top_k < self.top_k:
            raise ValueError("text_top_k must be greater than or equal to top_k")
        return self


class KnowledgeItemSearchHit(BaseModel):
    """Single chunk hit returned by the hybrid retrieval API."""

    kb_code: str
    file_code: str
    version: str
    chunk_no: int
    chunk_text: str
    score: float
    text_score: Optional[float] = None
    vector_score: Optional[float] = None
    source_code: str
    type_code: str
    file_path: str


class KnowledgeItemSearchMeta(BaseModel):
    """Metadata accompanying a search response."""

    query: str
    top_k: int
    vector_top_k: int
    text_top_k: int
    returned_count: int


class KnowledgeItemSearchResponse(BaseModel):
    """Business response for chunk-level hybrid retrieval."""

    items: list[KnowledgeItemSearchHit]
    meta: KnowledgeItemSearchMeta


def build_knowledge_item_import_manifest(
    *,
    kb_code: str,
    item_code: str,
    full_path: str,
    status: Status,
    source_code: str,
    type_code: str,
    version: str,
    chunks_json: str,
    document_metadata_json: Optional[str] = None,
) -> KnowledgeItemImportManifest:
    """Build a validated import manifest from multipart form fields."""
    try:
        chunks = json.loads(chunks_json)
    except json.JSONDecodeError as exc:
        raise ValueError("chunks_json must be a valid JSON array") from exc
    if not isinstance(chunks, list):
        raise ValueError("chunks_json must be a JSON array")

    metadata: dict[str, Any] | None = None
    if document_metadata_json:
        try:
            metadata = json.loads(document_metadata_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "document_metadata_json must be a valid JSON object"
            ) from exc
        if not isinstance(metadata, dict):
            raise ValueError("document_metadata_json must be a JSON object")

    try:
        derived_title = PurePosixPath(full_path).name or full_path
        return KnowledgeItemImportManifest.model_validate(
            {
                "kb_code": kb_code,
                "document": {
                    "item_code": item_code,
                    "full_path": full_path,
                    "title": derived_title,
                    "status": status,
                    "source_code": source_code,
                    "type_code": type_code,
                    "version": version,
                    "metadata": metadata,
                },
                "chunks": chunks,
            }
        )
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
