"""Pydantic schemas for knowledge base APIs."""

from typing import Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


def _normalize_api_path(path: str, *, allow_root: bool) -> str:
    value = str(path or "").strip()
    if not value.startswith("/"):
        raise ValueError("path must start with /")
    parts = [part for part in value.split("/") if part]
    if any(part == ".." for part in parts):
        raise ValueError("path must not contain ..")
    normalized = "/" + "/".join(parts) if parts else "/"
    if normalized == "/" and not allow_root:
        raise ValueError("root path is not allowed")
    return normalized


class CreateKnowledgeBaseRequest(BaseModel):
    """Request body for creating a knowledge base."""

    model_config = ConfigDict(populate_by_name=True)

    kb_name: str = Field(validation_alias=AliasChoices("knName", "kb_name"))
    kb_description: str | None = Field(
        default=None,
        validation_alias=AliasChoices("knDescription", "kb_description"),
    )


class CreateKnowledgeBaseResponse(BaseModel):
    """Business response for knowledge base creation."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(serialization_alias="knCode")
    kb_name: str = Field(serialization_alias="knName")
    kb_description: str | None = Field(
        default=None,
        serialization_alias="knDescription",
    )


class DeleteKnowledgeBaseRequest(BaseModel):
    """Request body for logically deleting a knowledge base."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )


class UpdateKnowledgeBaseRequest(BaseModel):
    """Request body for updating knowledge base business fields."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    kb_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("knName", "kb_name"),
    )
    kb_description: str | None = Field(
        default=None,
        validation_alias=AliasChoices("knDescription", "kb_description"),
    )


class CreateDirectoryRequest(BaseModel):
    """Request body for creating one directory in a knowledge base."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    directory_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("directoryPath", "directory_path"),
    )
    directory_description: str | None = Field(
        default=None,
        validation_alias=AliasChoices("directoryDescription", "directory_description"),
    )


class DeleteDirectoryRequest(BaseModel):
    """Request body for logically deleting one directory subtree."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    directory_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("directoryPath", "directory_path"),
    )


class UpdateDirectoryRequest(BaseModel):
    """Request body for renaming one directory by path."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    directory_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("directoryPath", "directory_path"),
    )
    directory_name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("directoryName", "directory_name"),
    )

    @model_validator(mode="after")
    def validate_directory_name(self) -> "UpdateDirectoryRequest":
        """Directory names must be a single segment, not a path."""
        normalized = self.directory_name.strip()
        if not normalized or "/" in normalized or normalized in {".", ".."}:
            raise ValueError("directory_name must be a single valid path segment")
        return self


class DeleteKnowledgeItemRequest(BaseModel):
    """Request body for logically deleting a knowledge item."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("filePath", "file_path"),
    )


class MoveKnowledgeItemsRequest(BaseModel):
    """Request body for moving files or directory subtrees."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    source_path: list[str] = Field(
        min_length=1,
        validation_alias=AliasChoices("sourcePath", "source_path"),
    )
    target_directory_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("targetDirectoryPath", "target_directory_path"),
    )
    target_file_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("targetFilePath", "target_file_path"),
    )
    overwrite: bool = False

    @model_validator(mode="after")
    def validate_move_request(self) -> "MoveKnowledgeItemsRequest":
        normalized_sources = [
            _normalize_api_path(path, allow_root=False) for path in self.source_path
        ]
        if len(set(normalized_sources)) != len(normalized_sources):
            raise ValueError("sourcePath must not contain duplicate paths")
        self.source_path = normalized_sources

        target_count = int(self.target_directory_path is not None) + int(
            self.target_file_path is not None
        )
        if target_count != 1:
            raise ValueError(
                "exactly one of targetDirectoryPath or targetFilePath is required"
            )

        if self.target_directory_path is not None:
            self.target_directory_path = _normalize_api_path(
                self.target_directory_path,
                allow_root=True,
            )

        if self.target_file_path is not None:
            if len(self.source_path) != 1:
                raise ValueError("targetFilePath requires exactly one file source")
            self.target_file_path = _normalize_api_path(
                self.target_file_path,
                allow_root=False,
            )

        if self.overwrite:
            raise ValueError("overwrite=true is not supported")
        return self


class MoveKnowledgeItemResult(BaseModel):
    """Single move result."""

    model_config = ConfigDict(populate_by_name=True)

    source_path: str = Field(serialization_alias="sourcePath")
    target_path: str | None = Field(default=None, serialization_alias="targetPath")
    success: bool
    error: str | None = None


class MoveKnowledgeItemsSummary(BaseModel):
    """Move batch summary."""

    total: int
    succeeded: int
    failed: int


class MoveKnowledgeItemsResponse(BaseModel):
    """Move API business response object."""

    model_config = ConfigDict(populate_by_name=True)

    data: list[MoveKnowledgeItemResult]
    summary: MoveKnowledgeItemsSummary


class FileToMarkdownIndexRequest(BaseModel):
    """Request body for triggering knowledge build on an uploaded file."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("filePath", "file_path"),
    )


class FileBuildStatusRequest(BaseModel):
    """Request body for querying one file's latest build status."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("filePath", "file_path"),
    )


class KnowledgeItemUploadRequest(BaseModel):
    """Request body for multipart file upload aligned with the public API."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("filePath", "file_path"),
    )
    file_description: str | None = Field(
        default=None,
        validation_alias=AliasChoices("fileDescription", "file_description"),
    )
    file_content: bytes = Field(
        min_length=1,
        validation_alias=AliasChoices("fileContent", "file_content"),
    )
    process_front_matter: bool = Field(
        default=True,
        validation_alias=AliasChoices("processFrontMatter", "process_front_matter"),
    )


class KnowledgeItemListDirRequest(BaseModel):
    """Request body for virtual filesystem listing."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    directory_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("directoryPath", "directory_path"),
    )


class KnowledgeItemListDirItem(BaseModel):
    """Single filesystem entry returned by list_dir."""

    kb_code: str = Field(serialization_alias="knCode")
    name: str
    type: Literal["file", "directory"]
    size: int = 0


class KnowledgeItemListDirResponse(BaseModel):
    """Business response for list_dir and glob."""

    data: list[KnowledgeItemListDirItem]


class KnowledgeItemGlobRequest(BaseModel):
    """Request body for path-pattern based filesystem matching."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    path_rule: str = Field(
        min_length=1,
        validation_alias=AliasChoices("pathRule", "path_rule"),
    )


class KnowledgeItemReferenceQueryRequest(BaseModel):
    """Request body for querying inbound Markdown file references."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    target_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "targetPath",
            "filePath",
            "target_path",
            "file_path",
        ),
    )

    @model_validator(mode="after")
    def validate_target_path(self) -> "KnowledgeItemReferenceQueryRequest":
        self.target_path = _normalize_api_path(self.target_path, allow_root=False)
        return self


class KnowledgeItemReferenceSource(BaseModel):
    """Single inbound reference source row."""

    model_config = ConfigDict(populate_by_name=True)

    source_path: str = Field(serialization_alias="sourcePath")
    original_target: str = Field(serialization_alias="originalTarget")
    target_suffix: str = Field(default="", serialization_alias="targetSuffix")
    target_path: str = Field(serialization_alias="targetPath")
    status: str


class KnowledgeItemReferenceQueryResponse(BaseModel):
    """Business response for inbound reference queries."""

    data: list[KnowledgeItemReferenceSource]


class ReadFileRequest(BaseModel):
    """Request body for reading built markdown content by line range."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("filePath", "file_path"),
    )
    start_line: int | None = Field(
        default=None,
        validation_alias=AliasChoices("startLine", "start_line"),
    )
    end_line: int | None = Field(
        default=None,
        validation_alias=AliasChoices("endLine", "end_line"),
    )

    @model_validator(mode="after")
    def validate_line_window(self) -> "ReadFileRequest":
        """start_line and end_line must be provided together."""
        has_start = self.start_line is not None
        has_end = self.end_line is not None
        if has_start != has_end:
            raise ValueError("startLine and endLine must be provided together")
        return self


class KnowledgeItemDownloadRequest(BaseModel):
    """Request body for downloading the original file content."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("filePath", "file_path"),
    )


class SearchRequest(BaseModel):
    """Request body for chunk-level retrieval (documented spec)."""

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=1)
    kb_code_list: list[str] = Field(
        min_length=1,
        validation_alias=AliasChoices("knCodeList", "kb_code_list", "kb_codes"),
    )
    top_k: int = Field(
        validation_alias=AliasChoices("topK", "top_k"),
    )
    search_mode: str = Field(
        validation_alias=AliasChoices("searchMode", "search_mode"),
    )
    where: dict | None = None
    metadata_field_list: Optional[list[str]] = Field(
        default=None,
        validation_alias=AliasChoices("metadataFieldList", "metadata_field_list"),
    )
    # Legacy alias: fileTypeList is folded into where as an `in` filter on
    # the fileType system field. Kept for backwards compatibility with
    # existing clients that haven't migrated to the DSL form.
    file_type_list: Optional[list[str]] = Field(
        default=None,
        validation_alias=AliasChoices("fileTypeList", "file_type_list"),
    )

    @model_validator(mode="after")
    def validate_fields(self) -> "SearchRequest":
        """Validate topK and searchMode."""
        if self.top_k <= 0:
            raise ValueError("topK must be greater than 0")
        allowed_modes = {"fullTextRecall", "embedding", "mixedRecall"}
        if self.search_mode not in allowed_modes:
            raise ValueError(
                f"searchMode must be one of {', '.join(sorted(allowed_modes))}"
            )
        return self


class SearchHit(BaseModel):
    """Single chunk hit returned by the documented search API."""

    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(serialization_alias="knCode")
    file_path: str = Field(serialization_alias="filePath")
    chunk_no: int = Field(serialization_alias="chunkNo")
    chunk_id: int = Field(serialization_alias="chunkId")
    chunk_text: str = Field(serialization_alias="chunkText")
    score: float
    image_path: str = Field(default="", serialization_alias="imagePath")
    start_line: int = Field(serialization_alias="startLine")
    end_line: int = Field(serialization_alias="endLine")
    metadata: dict | None = None
