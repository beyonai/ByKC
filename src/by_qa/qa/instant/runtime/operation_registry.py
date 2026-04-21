"""Operation type registry for the instant QA remote tool dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OperationType(str, Enum):
    SEARCH = "search"
    LIST_DIR = "listDir"
    GLOB = "glob"
    READ_FILE = "readFile"


class SearchInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    query: str = Field(description="Search query string")
    kn_code_list: list[str] | None = Field(
        default=None,
        alias="knCodeList",
        description="List of knowledge base codes to search; searches all configured KBs when omitted",
    )


class ListDirInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    kn_code: str = Field(alias="knCode", description="Knowledge base code")
    directory_path: str = Field(
        alias="directoryPath", description="Directory path to list"
    )


class GlobInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    kn_code: str = Field(alias="knCode", description="Knowledge base code")
    path_rule: str = Field(alias="pathRule", description="Glob pattern, e.g. **/*.py")


class ReadFileInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    kn_code: str = Field(alias="knCode", description="Knowledge base code")
    file_path: str = Field(alias="filePath", description="File path to read")
    start_line: int | None = Field(
        default=None, alias="startLine", description="Start line number (inclusive)"
    )
    end_line: int | None = Field(
        default=None, alias="endLine", description="End line number (inclusive)"
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
    OperationType.SEARCH: OperationSpec(
        operation_type=OperationType.SEARCH,
        tool_name="search_knowledge",
        description="Search knowledge bases for relevant content; supports parallel search across multiple KBs",
        input_schema=SearchInput,
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
    "GlobInput",
    "ListDirInput",
    "ListDirItem",
    "OperationSpec",
    "OperationType",
    "ReadFileInput",
    "ReadFileOutput",
    "SearchInput",
]
