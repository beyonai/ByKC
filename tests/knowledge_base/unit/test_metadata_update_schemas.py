"""Validation tests for file metadata update requests."""

import pytest
from pydantic import ValidationError

from by_qa.knowledge_base.api.metadata_schemas import UpdateFileMetadataRequest


def test_update_metadata_request_accepts_supported_operations():
    request = UpdateFileMetadataRequest.model_validate(
        {
            "knCode": "2",
            "filePath": "/a.md",
            "operationList": [
                {
                    "propertyName": "status",
                    "operation": "set",
                    "valueType": "string",
                    "value": "active",
                },
                {
                    "propertyName": "tags",
                    "operation": "append",
                    "value": ["a"],
                },
                {"propertyName": "owner", "operation": "unset"},
            ],
        }
    )

    assert request.operation_list[0].value_type == "string"
    assert request.operation_list[1].value == ["a"]


@pytest.mark.parametrize(
    "operation",
    [
        {"propertyName": "a", "operation": "set", "value": "missing type"},
        {
            "propertyName": "a",
            "operation": "set",
            "valueType": "number",
            "value": True,
        },
        {"propertyName": "a", "operation": "append", "value": []},
        {"propertyName": "a", "operation": "remove", "value": [1]},
        {"propertyName": "a", "operation": "clear", "value": []},
        {"propertyName": "a", "operation": "unset", "value": None},
        {
            "propertyName": "a",
            "operation": "unset",
            "valueType": "string",
        },
    ],
)
def test_update_metadata_request_rejects_invalid_operation_shape(operation):
    with pytest.raises(ValidationError):
        UpdateFileMetadataRequest.model_validate(
            {
                "knCode": "2",
                "filePath": "/a.md",
                "operationList": [operation],
            }
        )


def test_update_metadata_request_rejects_duplicate_property():
    with pytest.raises(ValidationError, match="duplicate metadata operation: status"):
        UpdateFileMetadataRequest.model_validate(
            {
                "knCode": "2",
                "filePath": "/a.md",
                "operationList": [
                    {"propertyName": "status", "operation": "unset"},
                    {"propertyName": "status", "operation": "unset"},
                ],
            }
        )


def test_update_metadata_request_requires_absolute_file_path():
    with pytest.raises(ValidationError, match="filePath must start"):
        UpdateFileMetadataRequest.model_validate(
            {
                "knCode": "2",
                "filePath": "a.md",
                "operationList": [{"propertyName": "status", "operation": "unset"}],
            }
        )


def test_update_metadata_request_does_not_limit_operation_count():
    request = UpdateFileMetadataRequest.model_validate(
        {
            "knCode": "2",
            "filePath": "/a.md",
            "operationList": [
                {"propertyName": f"field{index}", "operation": "unset"}
                for index in range(101)
            ],
        }
    )

    assert len(request.operation_list) == 101
