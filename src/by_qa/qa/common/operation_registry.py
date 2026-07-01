"""Operation type registry for QA remote tool dispatchers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OperationType(str, Enum):
    KNOWLEDGE_SEARCH = "knowledgeSearch"
    DSL_GUIDE = "dslGuide"
    LIST_DIR = "listDir"
    GLOB = "glob"
    READ_FILE = "readFile"
    # CREATE_DIR = "createDir"
    # EDIT_DIR = "editDir"
    # DELETE_DIR = "deleteDir"
    # UPLOAD_FILE = "uploadFile"
    # DELETE_FILE = "deleteFile"
    # DOWNLOAD_FILE = "downloadFile"
    # KNOWLEDGE_BUILD = "knowledgeBuild"


class SearchInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    query: str = Field(description="Search query string")
    kn_code_list: list[str] | None = Field(
        default=None,
        alias="knCodeList",
        serialization_alias="knCodeList",
        description="List of knowledge base codes to search; searches all configured KBs when omitted",
    )
    where: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Agent DSL filter AST (optional). "
            "MUST call get_dsl_guide first to learn DSL syntax before using "
            "this parameter. Custom metadata field ownership lives outside "
            "this service."
        ),
    )
    metadata_field_list: list[str] | None = Field(
        default=None,
        alias="metadataFieldList",
        serialization_alias="metadataFieldList",
        description="List of metadata field names to return alongside results (optional)",
    )

    @field_validator("kn_code_list", mode="before")
    @classmethod
    def coerce_kn_code_list(cls, v: object) -> object:
        if isinstance(v, str):
            import json

            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            return [v]
        return v

    @field_validator("where", mode="before")
    @classmethod
    def coerce_where(cls, v: object) -> object:
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                pass
        return v


class DslGuideInput(BaseModel):
    """No-arg input for the built-in DSL guide tool."""

    model_config = ConfigDict(populate_by_name=True)


class ListDirInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    kn_code: str = Field(
        alias="knCode", serialization_alias="knCode", description="Knowledge base code"
    )
    directory_path: str = Field(
        alias="directoryPath",
        serialization_alias="directoryPath",
        description="Directory path to list",
    )


class GlobInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    kn_code: str = Field(
        alias="knCode", serialization_alias="knCode", description="Knowledge base code"
    )
    path_rule: str = Field(
        alias="pathRule",
        serialization_alias="pathRule",
        description="Glob pattern, e.g. **/*.py",
    )


class ReadFileInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    kn_code: str = Field(
        alias="knCode", serialization_alias="knCode", description="Knowledge base code"
    )
    file_path: str = Field(
        alias="filePath",
        serialization_alias="filePath",
        description="File path to read",
    )
    start_line: int | None = Field(
        default=None,
        alias="startLine",
        serialization_alias="startLine",
        description="Start line number (inclusive)",
    )
    end_line: int | None = Field(
        default=None,
        alias="endLine",
        serialization_alias="endLine",
        description="End line number (inclusive)",
    )


class ListDirItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)
    kn_code: str = Field(alias="knCode")
    name: str
    type: Literal["file", "directory"]
    size: int = 0


class ReadFileOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)
    kn_code: str = Field(alias="knCode")
    file_path: str = Field(alias="filePath")
    start_line: int = Field(alias="startLine")
    end_line: int = Field(alias="endLine")
    data: str
    reached_eof: bool = Field(alias="reachedEof")


@dataclass
class OperationSpec:
    operation_type: OperationType
    tool_name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel] | None = None


OPERATION_REGISTRY: dict[OperationType, OperationSpec] = {
    OperationType.KNOWLEDGE_SEARCH: OperationSpec(
        operation_type=OperationType.KNOWLEDGE_SEARCH,
        tool_name="search_knowledge",
        description="Search knowledge bases for relevant content with optional DSL filtering; "
        "supports parallel search across multiple KBs. "
        "Before using the 'where' parameter, MUST call get_dsl_guide "
        "to learn DSL syntax.",
        input_schema=SearchInput,
    ),
    OperationType.DSL_GUIDE: OperationSpec(
        operation_type=OperationType.DSL_GUIDE,
        tool_name="get_dsl_guide",
        description="Get the Agent DSL syntax reference, including available operators, "
        "type constraints, nesting rules, and usage examples. Must be called before "
        "using the 'where' parameter on any search tool.",
        input_schema=DslGuideInput,
    ),
    OperationType.LIST_DIR: OperationSpec(
        operation_type=OperationType.LIST_DIR,
        tool_name="list_directory",
        description="List files and subdirectories under a given path in a knowledge base",
        input_schema=ListDirInput,
        output_schema=ListDirItem,
    ),
    OperationType.GLOB: OperationSpec(
        operation_type=OperationType.GLOB,
        tool_name="glob_search",
        description="Match files in a knowledge base using a glob pattern, e.g. **/*.py",
        input_schema=GlobInput,
        output_schema=ListDirItem,
    ),
    OperationType.READ_FILE: OperationSpec(
        operation_type=OperationType.READ_FILE,
        tool_name="read_file",
        description="Read the content of a file in a knowledge base; optionally specify a line range",
        input_schema=ReadFileInput,
        output_schema=ReadFileOutput,
    ),
}


__all__ = [
    "OPERATION_REGISTRY",
    "DslGuideInput",
    "GlobInput",
    "ListDirInput",
    "ListDirItem",
    "OperationSpec",
    "OperationType",
    "ReadFileInput",
    "ReadFileOutput",
    "SearchInput",
]
