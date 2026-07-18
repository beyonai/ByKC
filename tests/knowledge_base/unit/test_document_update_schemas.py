"""Contract tests for single-document update requests."""

import pytest
from pydantic import ValidationError

from by_qa.knowledge_base.api.schemas import DocumentUpdateRequest


def test_document_update_request_normalizes_file_path_and_tracks_omitted_description():
    request = DocumentUpdateRequest.model_validate(
        {
            "knCode": "hr-policy",
            "filePath": "//docs//readme.md",
            "fileContent": b"# Updated\n",
        }
    )

    assert request.file_path == "/docs/readme.md"
    assert request.file_description is None
    assert "file_description" not in request.model_fields_set


@pytest.mark.parametrize("description", ["", "updated description"])
def test_document_update_request_tracks_provided_description(description):
    request = DocumentUpdateRequest.model_validate(
        {
            "knCode": "hr-policy",
            "filePath": "/docs/readme.md",
            "fileContent": b"# Updated\n",
            "fileDescription": description,
        }
    )

    assert request.file_description == description
    assert "file_description" in request.model_fields_set


@pytest.mark.parametrize(
    "file_path",
    ["docs/readme.md", "/", "/docs/../readme.md", "/docs/./readme.md"],
)
def test_document_update_request_rejects_invalid_file_paths(file_path):
    with pytest.raises(ValidationError):
        DocumentUpdateRequest.model_validate(
            {
                "knCode": "hr-policy",
                "filePath": file_path,
                "fileContent": b"# Updated\n",
            }
        )
