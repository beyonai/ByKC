"""Single-source-of-truth checks for metadata value types."""

from by_qa.knowledge_base.api.metadata_schemas import CreateMetadataPropertyRequest
from by_qa.knowledge_base.dsl.compiler import _value_column
from by_qa.knowledge_base.metadata_types import (
    METADATA_VALUE_TYPES,
    VALUE_TYPE_TO_COLUMN,
)
from by_qa.knowledge_base.repositories.file_metadata_value_repository import (
    FileMetadataValueRepository,
)


def test_value_type_constant_covers_compiler_and_repo_mappings():
    """All declared value types must have a column in both consumers."""
    repo = FileMetadataValueRepository()
    for vt in METADATA_VALUE_TYPES:
        assert vt in VALUE_TYPE_TO_COLUMN
        assert _value_column(vt) == VALUE_TYPE_TO_COLUMN[vt]
        assert repo._value_column(vt) == VALUE_TYPE_TO_COLUMN[vt]


def test_value_type_mapping_has_no_orphan_columns():
    """The column mapping should not declare types missing from the canonical set."""
    assert set(VALUE_TYPE_TO_COLUMN) == set(METADATA_VALUE_TYPES)


def test_create_property_request_accepts_every_declared_value_type():
    for vt in METADATA_VALUE_TYPES:
        req = CreateMetadataPropertyRequest(
            propertyName=f"prop_{vt}",
            valueType=vt,
        )
        assert req.value_type == vt


def test_create_property_request_rejects_unknown_value_type():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateMetadataPropertyRequest(propertyName="x", valueType="unknown")
