"""Tests for knowledge item move API schemas."""

import pytest
from pydantic import ValidationError

from by_qa.knowledge_base.api.schemas import MoveKnowledgeItemsRequest


def _payload(**overrides):
    payload = {
        "knCode": "1",
        "sourcePath": ["/docs/a.md"],
        "targetDirectoryPath": "/archive",
    }
    payload.update(overrides)
    return payload


def test_move_request_accepts_multiple_sources_to_target_directory():
    request = MoveKnowledgeItemsRequest.model_validate(
        _payload(sourcePath=["/docs/a.md", "/docs/b.md"])
    )

    assert request.kb_code == "1"
    assert request.source_path == ["/docs/a.md", "/docs/b.md"]
    assert request.target_directory_path == "/archive"
    assert request.target_file_path is None
    assert request.overwrite is False


def test_move_request_accepts_single_source_to_target_file():
    request = MoveKnowledgeItemsRequest.model_validate(
        {
            "knCode": "1",
            "sourcePath": ["/docs/a.md"],
            "targetFilePath": "/archive/a.md",
        }
    )

    assert request.source_path == ["/docs/a.md"]
    assert request.target_file_path == "/archive/a.md"


@pytest.mark.parametrize(
    "payload",
    [
        _payload(sourcePath=[]),
        _payload(sourcePath=["docs/a.md"]),
        _payload(sourcePath=["/docs/../a.md"]),
        _payload(sourcePath=["/"]),
        _payload(sourcePath=["/docs/a.md", "/docs/a.md"]),
        _payload(
            targetDirectoryPath="/archive",
            targetFilePath="/archive/a.md",
        ),
        {
            "knCode": "1",
            "sourcePath": ["/docs/a.md"],
        },
        {
            "knCode": "1",
            "sourcePath": ["/docs/a.md", "/docs/b.md"],
            "targetFilePath": "/archive/a.md",
        },
        _payload(targetDirectoryPath="archive"),
        {
            "knCode": "1",
            "sourcePath": ["/docs/a.md"],
            "targetFilePath": "/",
        },
        _payload(overwrite=True),
    ],
)
def test_move_request_rejects_invalid_shapes(payload):
    with pytest.raises(ValidationError):
        MoveKnowledgeItemsRequest.model_validate(payload)


def test_move_request_normalizes_trailing_slashes_for_duplicate_detection():
    with pytest.raises(ValidationError):
        MoveKnowledgeItemsRequest.model_validate(
            _payload(sourcePath=["/docs/a.md", "/docs/a.md/"])
        )
