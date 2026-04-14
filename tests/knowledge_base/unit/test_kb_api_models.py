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


def test_delete_directory_request_requires_kb_code_and_directory_code():
    """Delete-directory requests should require kb_code and directory_code."""
    from by_qa.knowledge_base.api.schemas import DeleteDirectoryRequest

    request = DeleteDirectoryRequest(
        kb_code="hr-policy",
        directory_code="attendance-archive",
    )

    assert request.kb_code == "hr-policy"
    assert request.directory_code == "attendance-archive"


def test_update_directory_request_accepts_partial_fields():
    """Update-directory requests should allow partial updates."""
    from by_qa.knowledge_base.api.schemas import UpdateDirectoryRequest

    request = UpdateDirectoryRequest(
        kb_code="hr-policy",
        directory_code="attendance-archive",
        directory_name="历史归档",
        metadata={"owner": "HR"},
    )

    assert request.kb_code == "hr-policy"
    assert request.directory_code == "attendance-archive"
    assert request.directory_name == "历史归档"
    assert request.directory_description is None


def test_update_directory_request_rejects_path_like_directory_name():
    """Directory rename should only accept a single path segment, not a path."""
    from by_qa.knowledge_base.api.schemas import UpdateDirectoryRequest

    with pytest.raises(ValidationError):
        UpdateDirectoryRequest(
            kb_code="hr-policy",
            directory_code="attendance-archive",
            directory_name="/demo",
        )


def test_update_file_request_accepts_partial_fields():
    """Update-file requests should allow partial updates."""
    from by_qa.knowledge_base.api.schemas import UpdateFileRequest

    request = UpdateFileRequest(
        kb_code="hr-policy",
        file_code="attendance-policy-pdf",
        file_name="异常考勤处理办法（正式版）.pdf",
        metadata={"owner": "HR"},
    )

    assert request.kb_code == "hr-policy"
    assert request.file_code == "attendance-policy-pdf"
    assert request.file_name == "异常考勤处理办法（正式版）.pdf"
    assert request.file_description is None


def test_update_file_request_rejects_path_like_file_name():
    """File rename should only accept a single path segment, not a path."""
    from by_qa.knowledge_base.api.schemas import UpdateFileRequest

    with pytest.raises(ValidationError):
        UpdateFileRequest(
            kb_code="hr-policy",
            file_code="attendance-policy-pdf",
            file_name="/demo.pdf",
        )


def test_delete_knowledge_item_request_requires_kb_code_and_file_code():
    """Delete-knowledge-item requests should require kb_code and file_code."""
    from by_qa.knowledge_base.api.schemas import DeleteKnowledgeItemRequest

    request = DeleteKnowledgeItemRequest(kb_code="hr-policy", file_code="file-001")

    assert request.kb_code == "hr-policy"
    assert request.file_code == "file-001"


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


def test_import_request_rejects_duplicate_chunk_numbers():
    """Combined import requests should reject duplicate chunk numbers in one request."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemImportRequest

    with pytest.raises(ValidationError):
        KnowledgeItemImportRequest(
            kb_code="hr-policy",
            file_code="file-001",
            file_path="/dir1/item-1.pdf",
            file_content="ZmFrZS1iYXNlNjQ=",
            version="v1",
            source_code="oa",
            markdown_content="# hello",
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


def test_search_request_rejects_empty_kb_codes():
    """Search requests should require at least one kb_code."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemSearchRequest

    with pytest.raises(ValidationError):
        KnowledgeItemSearchRequest(
            query="员工请假制度怎么规定",
            kb_codes=[],
        )


def test_search_request_requires_candidate_limits_not_smaller_than_top_k():
    """Candidate pool sizes should not be smaller than the final top_k."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemSearchRequest

    with pytest.raises(ValidationError):
        KnowledgeItemSearchRequest(
            query="员工请假制度怎么规定",
            kb_codes=["hr-policy"],
            top_k=10,
            vector_top_k=5,
            text_top_k=30,
        )


def test_search_response_serializes_chunk_items_and_meta():
    """Search responses should expose chunk-oriented retrieval fields."""
    from by_qa.knowledge_base.api.schemas import (
        KnowledgeItemSearchHit,
        KnowledgeItemSearchMeta,
        KnowledgeItemSearchResponse,
    )

    response = KnowledgeItemSearchResponse(
        items=[
            KnowledgeItemSearchHit(
                kb_code="hr-policy",
                file_code="item-1",
                version="v2",
                chunk_no=4,
                chunk_text="员工请假应至少提前一天提交申请。",
                score=0.91,
                text_score=0.62,
                vector_score=0.88,
                source_code="oa",
                type_code="policy_markdown",
                file_path="/employee-handbook.md",
            )
        ],
        meta=KnowledgeItemSearchMeta(
            query="员工请假制度怎么规定",
            top_k=10,
            vector_top_k=40,
            text_top_k=30,
            returned_count=1,
        ),
    )

    assert response.model_dump()["items"][0]["file_code"] == "item-1"
    assert response.model_dump()["items"][0]["file_path"] == "/employee-handbook.md"
    assert response.model_dump()["meta"]["returned_count"] == 1


def test_write_index_response_serializes_structured_chunk_summary():
    """Write-index responses should expose a typed chunk summary object."""
    from by_qa.knowledge_base.api.schemas import WriteIndexResponse

    response = WriteIndexResponse(
        kb_code="hr-policy",
        file_code="file-001",
        version="V1",
        chunks={"count": 1},
    )

    assert response.model_dump() == {
        "kb_code": "hr-policy",
        "file_code": "file-001",
        "version": "V1",
        "chunks": {"count": 1},
    }


def test_write_file_request_no_longer_requires_is_binary():
    """Write-file should accept the new original-file contract without is_binary."""
    from by_qa.knowledge_base.api.schemas import WriteFileRequest

    request = WriteFileRequest(
        kb_code="hr-policy",
        file_code="file-001",
        file_path="/dir1/item-1.pdf",
        file_content="ZmFrZS1iYXNlNjQ=",
        version="v1",
        source_code="oa",
    )

    assert request.kb_code == "hr-policy"
    assert request.file_path == "/dir1/item-1.pdf"


def test_write_index_request_requires_markdown_content():
    """Write-index requests should include the parsed markdown sidecar content."""
    from by_qa.knowledge_base.api.schemas import WriteIndexRequest

    with pytest.raises(ValidationError):
        WriteIndexRequest(
            kb_code="hr-policy",
            file_code="file-001",
            version="V1",
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


def test_fetch_response_serializes_content_type_and_eof_flag():
    """Fetch responses should expose content_type and EOF state for markdown reads."""
    from by_qa.knowledge_base.api.schemas import KnowledgeItemFetchResponse

    response = KnowledgeItemFetchResponse(
        kb_code="hr-policy",
        path="人力制度知识库/dir1/doc.md",
        content_type="markdown",
        start_line=1,
        end_line=10,
        data="line1\n",
        reached_eof=True,
    )

    assert response.model_dump()["content_type"] == "markdown"
    assert response.model_dump()["reached_eof"] is True
