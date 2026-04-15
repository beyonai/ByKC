"""Tests for knowledge-base API request and response models."""

import pytest
from pydantic import ValidationError


def test_create_knowledge_base_request_accepts_documented_field_names():
    """Knowledge base creation should accept documented knName fields."""
    from by_qa.knowledge_base.api.schemas import CreateKnowledgeBaseRequest

    request = CreateKnowledgeBaseRequest(
        knName="人力制度知识库",
        knDescription="公司人事制度与流程文档",
    )

    assert request.kb_name == "人力制度知识库"
    assert request.kb_description == "公司人事制度与流程文档"


def test_delete_knowledge_base_request_requires_kb_code():
    """Delete-knowledge-base requests should require the business kb_code."""
    from by_qa.knowledge_base.api.schemas import DeleteKnowledgeBaseRequest

    request = DeleteKnowledgeBaseRequest(knCode="hr-policy")

    assert request.kb_code == "hr-policy"


def test_update_knowledge_base_request_accepts_partial_fields():
    """Update-knowledge-base requests should allow partial documented fields."""
    from by_qa.knowledge_base.api.schemas import UpdateKnowledgeBaseRequest

    request = UpdateKnowledgeBaseRequest(
        knCode="hr-policy",
        knName="新知识库名称",
    )

    assert request.kb_code == "hr-policy"
    assert request.kb_name == "新知识库名称"
    assert request.kb_description is None


def test_create_directory_request_accepts_documented_fields():
    """Create-directory requests should accept full path based input."""
    from by_qa.knowledge_base.api.schemas import CreateDirectoryRequest

    request = CreateDirectoryRequest(
        knCode="hr-policy",
        directoryPath="/考勤制度/归档",
        directoryDescription="考勤制度归档目录",
    )

    assert request.kb_code == "hr-policy"
    assert request.directory_path == "/考勤制度/归档"


def test_delete_directory_request_requires_documented_fields():
    """Delete-directory requests should require documented path fields."""
    from by_qa.knowledge_base.api.schemas import DeleteDirectoryRequest

    request = DeleteDirectoryRequest(
        knCode="hr-policy",
        directoryPath="/考勤制度/归档",
    )

    assert request.kb_code == "hr-policy"
    assert request.directory_path == "/考勤制度/归档"


def test_update_directory_request_accepts_documented_fields():
    """Update-directory requests should accept documented path rename fields."""
    from by_qa.knowledge_base.api.schemas import UpdateDirectoryRequest

    request = UpdateDirectoryRequest(
        knCode="hr-policy",
        directoryPath="/考勤制度/归档",
        directoryName="历史归档",
    )

    assert request.kb_code == "hr-policy"
    assert request.directory_path == "/考勤制度/归档"
    assert request.directory_name == "历史归档"


def test_update_directory_request_rejects_path_like_directory_name():
    """Directory rename should only accept a single path segment, not a path."""
    from by_qa.knowledge_base.api.schemas import UpdateDirectoryRequest

    with pytest.raises(ValidationError):
        UpdateDirectoryRequest(
            knCode="hr-policy",
            directoryPath="/考勤制度/归档",
            directoryName="/demo",
        )


def test_delete_knowledge_item_request_accepts_documented_fields():
    """Delete-knowledge-item requests should accept knCode and filePath."""
    from by_qa.knowledge_base.api.schemas import DeleteKnowledgeItemRequest

    request = DeleteKnowledgeItemRequest(
        knCode="hr-policy",
        filePath="/考勤制度/异常考勤处理办法.pdf",
    )

    assert request.kb_code == "hr-policy"
    assert request.file_path == "/考勤制度/异常考勤处理办法.pdf"


def test_import_manifest_rejects_duplicate_chunk_numbers():
    """Import manifest should reject duplicate chunk numbers in one request."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemImportManifest

    with pytest.raises(ValidationError):
        KnowledgeItemImportManifest(
            kb_code="hr-policy",
            document={
                "item_code": "item-1",
                "full_path": "dir1/item-1.md",
                "title": "操作手册.pdf",
                "status": "ACTIVE",
                "source_code": "oa",
                "type_code": "policy_markdown",
                "version": "v1",
            },
            chunks=[
                {
                    "chunk_no": 1,
                    "start_line": 1,
                    "end_line": 10,
                    "chunk_text": "hello",
                    "embedding": [0.1, 0.2],
                },
                {
                    "chunk_no": 1,
                    "start_line": 11,
                    "end_line": 20,
                    "chunk_text": "world",
                    "embedding": [0.3, 0.4],
                },
            ],
        )


def test_import_manifest_rejects_invalid_document_status():
    """Import manifest should only accept ACTIVE or INACTIVE document status."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemImportManifest

    with pytest.raises(ValidationError):
        KnowledgeItemImportManifest(
            kb_code="hr-policy",
            document={
                "item_code": "item-1",
                "full_path": "dir1/item-1.md",
                "title": "操作手册.pdf",
                "status": "DELETED",
                "source_code": "oa",
                "type_code": "policy_markdown",
                "version": "v1",
            },
            chunks=[
                {
                    "chunk_no": 1,
                    "start_line": 1,
                    "end_line": 10,
                    "chunk_text": "hello",
                    "embedding": [0.1, 0.2],
                }
            ],
        )


def test_import_manifest_ignores_legacy_content_hash_field():
    """Import manifest schema should no longer expose client-provided content_hash."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemImportManifest

    manifest = KnowledgeItemImportManifest(
        kb_code="hr-policy",
        document={
            "item_code": "item-1",
            "full_path": "dir1/item-1.md",
            "title": "操作手册.pdf",
            "status": "ACTIVE",
            "source_code": "oa",
            "type_code": "policy_markdown",
            "version": "v1",
            "content_hash": "client-controlled-value",
        },
        chunks=[
            {
                "chunk_no": 1,
                "start_line": 1,
                "end_line": 10,
                "chunk_text": "hello",
                "embedding": [0.1, 0.2],
            }
        ],
    )

    assert not hasattr(manifest.document, "content_hash")


def test_import_response_excludes_internal_ids():
    """Import success responses should only expose business fields."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemImportResponse

    response = KnowledgeItemImportResponse(
        kb_code="hr-policy",
        full_path="dir1/item-1.md",
        version="v1",
        status="ACTIVE",
        chunk_count=2,
    )

    assert response.model_dump() == {
        "kb_code": "hr-policy",
        "full_path": "dir1/item-1.md",
        "version": "v1",
        "status": "ACTIVE",
        "chunk_count": 2,
    }


def test_upload_request_accepts_documented_form_fields():
    """Multipart upload requests should accept documented field names."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemUploadRequest

    request = KnowledgeItemUploadRequest(
        knCode="hr-policy",
        filePath="/dir1/item-1.pdf",
        fileDescription="操作手册",
        fileContent=b"hello",
        fileName="item-1.pdf",
        contentType="application/pdf",
    )

    assert request.kb_code == "hr-policy"
    assert request.file_path == "/dir1/item-1.pdf"
    assert request.file_description == "操作手册"
    assert request.file_content == b"hello"


def test_import_response_serializes_combined_file_and_chunk_summary():
    """Combined import responses should expose file metadata and chunk summary."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemImportFileResponse

    response = KnowledgeItemImportFileResponse(
        kb_code="hr-policy",
        file_code="file-001",
        type_code="pdf",
        file_path="/dir1/item-1.pdf",
        file_description="操作手册",
        version="v1",
        status="ACTIVE",
        metadata={"owner": "HR"},
        chunks={"count": 2},
    )

    assert response.model_dump() == {
        "kb_code": "hr-policy",
        "file_code": "file-001",
        "type_code": "pdf",
        "file_path": "/dir1/item-1.pdf",
        "file_description": "操作手册",
        "version": "v1",
        "status": "ACTIVE",
        "metadata": {"owner": "HR"},
        "chunks": {"count": 2},
    }


def test_glob_request_accepts_documented_fields():
    """Glob requests should accept knCode and pathRule."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemGlobRequest

    request = KnowledgeItemGlobRequest(
        knCode="hr-policy",
        pathRule="/制度/*/*.pdf",
    )

    assert request.kb_code == "hr-policy"
    assert request.path_rule == "/制度/*/*.pdf"


def test_download_request_accepts_documented_fields():
    """Download requests should accept knCode and filePath."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemDownloadRequest

    request = KnowledgeItemDownloadRequest(
        knCode="hr-policy",
        filePath="/制度/人事/请假制度.pdf",
    )

    assert request.kb_code == "hr-policy"
    assert request.file_path == "/制度/人事/请假制度.pdf"


def test_file_to_markdown_index_request_accepts_camel_case():
    from by_qa.knowledge_base.api.schemas import FileToMarkdownIndexRequest

    req = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"}
    )
    assert req.kb_code == "1"
    assert req.file_path == "/制度/人事/请假制度.pdf"


def test_file_to_markdown_index_request_accepts_snake_case():
    from by_qa.knowledge_base.api.schemas import FileToMarkdownIndexRequest

    req = FileToMarkdownIndexRequest.model_validate(
        {"kb_code": "1", "file_path": "/制度/人事/请假制度.pdf"}
    )
    assert req.kb_code == "1"
    assert req.file_path == "/制度/人事/请假制度.pdf"


def test_file_to_markdown_index_request_rejects_empty_kb_code():
    from by_qa.knowledge_base.api.schemas import FileToMarkdownIndexRequest

    with pytest.raises(Exception):
        FileToMarkdownIndexRequest.model_validate(
            {"knCode": "", "filePath": "/制度/人事/请假制度.pdf"}
        )


def test_file_to_markdown_index_request_rejects_missing_file_path():
    from by_qa.knowledge_base.api.schemas import FileToMarkdownIndexRequest

    with pytest.raises(Exception):
        FileToMarkdownIndexRequest.model_validate({"knCode": "1"})
