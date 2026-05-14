"""Pydantic schemas for metadata property and file metadata APIs."""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

# --- Property Definition Schemas ---


class CreateMetadataPropertyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    property_name: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("propertyName", "property_name"),
    )
    value_type: str = Field(
        validation_alias=AliasChoices("valueType", "value_type"),
    )
    description: str | None = Field(
        default=None,
    )
    ext_params: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("extParams", "ext_params"),
    )

    @model_validator(mode="after")
    def validate_value_type(self) -> "CreateMetadataPropertyRequest":
        allowed = {"string", "stringList", "number", "boolean", "datetime"}
        if self.value_type not in allowed:
            raise ValueError(f"valueType must be one of {', '.join(sorted(allowed))}")
        return self


class MetadataPropertyResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    property_name: str = Field(serialization_alias="propertyName")
    value_type: str = Field(serialization_alias="valueType")
    description: str | None = Field(default=None)
    ext_params: dict[str, Any] | None = Field(
        default=None, serialization_alias="extParams"
    )


class BatchCreateMetadataPropertyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    property_list: list[CreateMetadataPropertyRequest] = Field(
        min_length=1,
        validation_alias=AliasChoices("propertyList", "property_list"),
    )


class DeleteMetadataPropertyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    property_name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("propertyName", "property_name"),
    )


class ListMetadataPropertyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    property_name_list: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("propertyNameList", "property_name_list"),
    )


# --- File Metadata Schemas ---


class MetadataOperation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    property_name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("propertyName", "property_name"),
    )
    operation: str
    value: Any = None

    @model_validator(mode="after")
    def validate_operation(self) -> "MetadataOperation":
        allowed = {"set", "unset", "append", "remove", "clear"}
        if self.operation not in allowed:
            raise ValueError(f"operation must be one of {', '.join(sorted(allowed))}")
        if self.operation in {"set", "append", "remove"} and self.value is None:
            raise ValueError(f"value is required for operation '{self.operation}'")
        return self


class UpdateFileMetadataRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("filePath", "file_path"),
    )
    operation_list: list[MetadataOperation] = Field(
        min_length=1,
        validation_alias=AliasChoices("operationList", "operation_list"),
    )


class GetFileMetadataRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("knCode", "kb_code"),
    )
    file_path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("filePath", "file_path"),
    )
    metadata_field_list: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("metadataFieldList", "metadata_field_list"),
    )


class ListMetadataFieldsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kb_code_list: list[str] = Field(
        min_length=1,
        validation_alias=AliasChoices("knCodeList", "kb_code_list"),
    )


# --- Search Schemas ---


class MetadataSearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kb_code_list: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("knCodeList", "kb_code_list"),
    )
    where: dict[str, Any] | None = None
    metadata_field_list: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("metadataFieldList", "metadata_field_list"),
    )
    top_k: int = Field(
        validation_alias=AliasChoices("topK", "top_k"),
    )

    @model_validator(mode="after")
    def validate_top_k(self) -> "MetadataSearchRequest":
        if self.top_k <= 0:
            raise ValueError("topK must be greater than 0")
        return self


class AgentSearchRequest(BaseModel):
    """Upgraded search request with Agent DSL support."""

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=1)
    kb_code_list: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("knCodeList", "kb_code_list"),
    )
    where: dict[str, Any] | None = None
    search_mode: str = Field(
        validation_alias=AliasChoices("searchMode", "search_mode"),
    )
    metadata_field_list: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("metadataFieldList", "metadata_field_list"),
    )
    top_k: int = Field(
        validation_alias=AliasChoices("topK", "top_k"),
    )

    @model_validator(mode="after")
    def validate_fields(self) -> "AgentSearchRequest":
        if self.top_k <= 0:
            raise ValueError("topK must be greater than 0")
        allowed_modes = {"fullTextRecall", "embedding", "mixedRecall"}
        if self.search_mode not in allowed_modes:
            raise ValueError(
                f"searchMode must be one of {', '.join(sorted(allowed_modes))}"
            )
        return self


class SearchFileRequest(BaseModel):
    """File-level semantic search with Agent DSL."""

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=1)
    kb_code_list: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("knCodeList", "kb_code_list"),
    )
    where: dict[str, Any] | None = None
    search_mode: str = Field(
        validation_alias=AliasChoices("searchMode", "search_mode"),
    )
    metadata_field_list: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("metadataFieldList", "metadata_field_list"),
    )
    top_k: int = Field(
        validation_alias=AliasChoices("topK", "top_k"),
    )

    @model_validator(mode="after")
    def validate_fields(self) -> "SearchFileRequest":
        if self.top_k <= 0:
            raise ValueError("topK must be greater than 0")
        allowed_modes = {"fullTextRecall", "embedding", "mixedRecall"}
        if self.search_mode not in allowed_modes:
            raise ValueError(
                f"searchMode must be one of {', '.join(sorted(allowed_modes))}"
            )
        return self


# --- Response Hit Models ---


class MetadataSearchHit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(serialization_alias="knCode")
    file_path: str = Field(serialization_alias="filePath")
    metadata: dict[str, Any] | None = None


class AgentSearchHit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(serialization_alias="knCode")
    file_path: str = Field(serialization_alias="filePath")
    chunk_id: int = Field(serialization_alias="chunkId")
    chunk_no: int = Field(serialization_alias="chunkNo")
    chunk_text: str = Field(serialization_alias="chunkText")
    score: float
    start_line: int = Field(serialization_alias="startLine")
    end_line: int = Field(serialization_alias="endLine")
    metadata: dict[str, Any] | None = None


class SearchFileHit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kb_code: str = Field(serialization_alias="knCode")
    file_path: str = Field(serialization_alias="filePath")
    score: float
    metadata: dict[str, Any] | None = None
