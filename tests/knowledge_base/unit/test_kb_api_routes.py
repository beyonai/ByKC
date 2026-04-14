"""Tests for knowledge-base API routes."""

from fastapi.testclient import TestClient

from by_qa.knowledge_base.api import routes
from by_qa.knowledge_base.api.schemas import (
    CreateDirectoryResponse,
    CreateKnowledgeBaseResponse,
    DeleteDirectoryResponse,
    DeleteKnowledgeBaseResponse,
    DeleteKnowledgeItemResponse,
    KnowledgeItemFetchResponse,
    KnowledgeItemImportFileResponse,
    KnowledgeItemImportResponse,
    KnowledgeItemListDirItem,
    KnowledgeItemListDirResponse,
    KnowledgeItemSearchHit,
    KnowledgeItemSearchMeta,
    KnowledgeItemSearchResponse,
    UpdateDirectoryResponse,
    UpdateFileResponse,
    UpdateKnowledgeBaseResponse,
    WriteFileResponse,
    WriteIndexResponse,
)
from by_qa.knowledge_base.services.errors import (
    KnowledgeBaseConfigurationError,
    KnowledgeBaseValidationError,
)
from by_qa.main import app


class FakeKBService:
    """Service double used by route tests."""

    def __init__(self):
        self.created_requests = []
        self.created_directory_requests = []
        self.import_calls = []

    def create_knowledge_base(self, request):
        self.created_requests.append(request)
        return CreateKnowledgeBaseResponse(
            kb_code="7",
            kb_name=request.kb_name,
            kb_description=request.kb_description,
        )

    def create_directory(self, request):
        self.created_directory_requests.append(request)
        return CreateDirectoryResponse(
            kb_code=request.kb_code,
            directory_code=request.directory_code,
            directory_path=request.directory_path,
            directory_description=request.directory_description,
            status=request.status,
            metadata=request.metadata,
        )

    def delete_directory(self, request):
        return DeleteDirectoryResponse(
            kb_code=request.kb_code,
            directory_code=request.directory_code,
            is_deleted=True,
        )

    def update_directory(self, request):
        return UpdateDirectoryResponse(
            kb_code=request.kb_code,
            directory_code=request.directory_code,
            directory_path="/考勤制度/历史归档",
            directory_description=request.directory_description,
            metadata=request.metadata,
        )

    def update_file(self, request):
        return UpdateFileResponse(
            kb_code=request.kb_code,
            file_code=request.file_code,
            file_path="/考勤制度/异常考勤处理办法（正式版）.pdf",
            file_description=request.file_description,
            metadata=request.metadata,
        )

    def delete_knowledge_base(self, request):
        return DeleteKnowledgeBaseResponse(kb_code=request.kb_code, is_deleted=True)

    def update_knowledge_base(self, request):
        return UpdateKnowledgeBaseResponse(
            kb_code=request.kb_code,
            kb_name=request.kb_name or "人力制度知识库",
            kb_description=request.kb_description,
        )

    def delete_knowledge_item(self, request):
        return DeleteKnowledgeItemResponse(
            kb_code=request.kb_code,
            file_code=request.file_code,
            is_deleted=True,
        )

    def import_document(self, *, markdown_bytes, manifest):
        self.import_calls.append((markdown_bytes, manifest))
        return KnowledgeItemImportResponse(
            kb_code=manifest.kb_code,
            full_path=manifest.document.full_path,
            version=manifest.document.version,
            status=manifest.document.status,
            chunk_count=len(manifest.chunks),
        )

    def import_knowledge_item(self, request):
        self.import_calls.append(request)
        return KnowledgeItemImportFileResponse(
            kb_code=request.kb_code,
            file_code=request.file_code,
            type_code="pdf",
            file_path=request.file_path,
            file_description=request.file_description,
            version=request.version,
            status=request.status,
            metadata=request.metadata,
            chunks={"count": len(request.chunks)},
        )

    def write_file(self, request):
        return WriteFileResponse(
            kb_code=request.kb_code,
            file_code=request.file_code,
            type_code="pdf",
            file_path=request.file_path,
            file_description=request.file_description,
            version=request.version,
            status=request.status,
            metadata=request.metadata,
        )

    def write_index(self, request):
        return WriteIndexResponse(
            kb_code=request.kb_code,
            file_code=request.file_code,
            version=request.version,
            chunks={"count": len(request.chunks)},
        )

    def search(self, request):
        return KnowledgeItemSearchResponse(
            items=[
                KnowledgeItemSearchHit(
                    kb_code="hr-policy",
                    file_code="item-1",
                    version="v2",
                    chunk_no=1,
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
                query=request.query,
                top_k=request.top_k,
                vector_top_k=request.vector_top_k,
                text_top_k=request.text_top_k,
                returned_count=1,
            ),
        )

    def list_dir(self, request):
        return KnowledgeItemListDirResponse(
            items=[
                KnowledgeItemListDirItem(
                    kb_code="integration-kb",
                    name="/Integration KB/dir1/doc.md",
                    type="file",
                    size=100,
                )
            ]
        )

    def glob(self, request):
        return KnowledgeItemListDirResponse(
            items=[
                KnowledgeItemListDirItem(
                    kb_code="integration-kb",
                    name="/Integration KB/dir1/doc.md",
                    type="file",
                    size=100,
                )
            ]
        )

    def fetch(self, request):
        return KnowledgeItemFetchResponse(
            kb_code=request.kb_codes[0],
            path=request.path,
            content_type=request.content_type,
            start_line=request.start_line,
            end_line=request.end_line,
            data="line1\nline2\n",
            reached_eof=True,
        )

    def download_file(self, request):
        return {
            "filename": "doc.pdf",
            "media_type": "application/pdf",
            "content": b"%PDF-1.4 test payload",
        }


def make_test_client(monkeypatch, service):
    """Create a TestClient with unrelated startup dependencies stubbed out."""
    monkeypatch.setattr("by_qa.main.get_knowledge_base_service", lambda: service)
    monkeypatch.setattr(
        "by_qa.main.get_knowledge_item_ingestion_service", lambda: service
    )
    monkeypatch.setattr("by_qa.main.get_knowledge_item_search_service", lambda: service)
    monkeypatch.setattr("by_qa.main.get_adapter", lambda: object())
    monkeypatch.setattr("by_qa.main.get_instant_search_engine", lambda: object())
    return TestClient(app)


def test_create_knowledge_base_route_returns_business_response(monkeypatch):
    """POST /api/v1/knowledgeBases/create should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)
    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={
            "knName": "人力制度知识库",
            "knDescription": "公司人事制度与流程文档",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {
            "knCode": "7",
            "knName": "人力制度知识库",
            "knDescription": "公司人事制度与流程文档",
        },
    }
    assert service.created_requests[0].kb_name == "人力制度知识库"


def test_create_knowledge_base_route_emits_summary_logs(monkeypatch):
    """Create route should emit request, key-step, and response summary logs."""
    service = FakeKBService()
    info_messages: list[str] = []

    monkeypatch.setattr(
        routes.logger,
        "info",
        lambda message, *args, **kwargs: info_messages.append(
            message % args if args else message
        ),
    )
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={
            "knName": "人力制度知识库",
            "knDescription": "公司人事制度与流程文档",
        },
    )

    assert response.status_code == 200
    assert info_messages == [
        "create_knowledge_base request received: kb_name=人力制度知识库, has_description=True",
        "create_knowledge_base resolved service: service_class=FakeKBService",
        "create_knowledge_base service call succeeded: kb_code=7",
        "create_knowledge_base response ready: code=200, kb_code=7",
    ]


def test_create_knowledge_base_route_maps_request_validation_to_documented_error(
    monkeypatch,
):
    """Create-knowledge-base validation should use the documented error envelope."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={"knDescription": "公司人事制度与流程文档"},
    )

    assert response.status_code == 422
    assert response.json()["resultCode"] == "-1"
    assert response.json()["resultMsg"] == "request validation failed"
    assert response.json()["resultObject"]["errors"]


def test_create_knowledge_base_route_maps_duplicate_name_to_documented_error(
    monkeypatch,
):
    """Create-knowledge-base duplicate names should use the documented error envelope."""

    class DuplicateNameKBService(FakeKBService):
        def create_knowledge_base(self, request):
            raise KnowledgeBaseValidationError(
                f"knowledge base name already exists: {request.kb_name}"
            )

    client = make_test_client(monkeypatch, DuplicateNameKBService())
    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={"knName": "人力制度知识库"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "knowledge base name already exists: 人力制度知识库",
        "resultObject": {},
    }


def test_delete_knowledge_base_route_returns_business_response(monkeypatch):
    """Delete-knowledge-base route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeBases/delete",
        json={"knCode": "hr-policy"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {},
    }


def test_update_knowledge_base_route_returns_business_response(monkeypatch):
    """Update-knowledge-base route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeBases/update",
        json={
            "knCode": "hr-policy",
            "knName": "新知识库名称",
            "knDescription": "更新后的描述",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {},
    }


def test_update_knowledge_base_route_maps_duplicate_name_to_documented_error(
    monkeypatch,
):
    """Update-knowledge-base duplicate names should use the documented error envelope."""

    class DuplicateNameKBService(FakeKBService):
        def update_knowledge_base(self, request):
            raise KnowledgeBaseValidationError(
                f"knowledge base name already exists: {request.kb_name}"
            )

    client = make_test_client(monkeypatch, DuplicateNameKBService())
    response = client.post(
        "/api/v1/knowledgeBases/update",
        json={"knCode": "hr-policy", "knName": "人力制度知识库"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "knowledge base name already exists: 人力制度知识库",
        "resultObject": {},
    }


def test_update_knowledge_base_route_maps_unexpected_exception_to_documented_500(
    monkeypatch,
):
    """Update-knowledge-base unexpected failures should use the documented error envelope."""

    class BrokenKBService(FakeKBService):
        def update_knowledge_base(self, request):
            raise RuntimeError('column "kb_code" does not exist')

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/knowledgeBases/update",
        json={"knCode": "7", "knName": "人力制度知识库"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": 'column "kb_code" does not exist',
        "resultObject": {},
    }


def test_create_directory_route_maps_missing_parent_to_404(monkeypatch):
    """Create-directory should return 404 when the parent directory is missing."""

    class BrokenKBService(FakeKBService):
        def create_directory(self, request):
            raise KnowledgeBaseValidationError(
                "parent directory not found: missing-dir"
            )

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/directories/create",
        json={
            "kb_code": "hr-policy",
            "directory_code": "attendance-archive",
            "directory_path": "/missing-dir/归档",
            "directory_description": None,
            "source_code": "manual",
            "status": "ACTIVE",
            "metadata": None,
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == "KB_DIRECTORY_PARENT_NOT_FOUND"


def test_create_directory_route_returns_business_response(monkeypatch):
    """Create-directory route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/directories/create",
        json={
            "kb_code": "hr-policy",
            "directory_code": "attendance-archive",
            "directory_path": "/考勤制度/归档",
            "directory_description": "考勤制度归档目录",
            "source_code": "manual",
            "status": "ACTIVE",
            "metadata": {"owner": "HR"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "error": None,
        "data": {
            "kb_code": "hr-policy",
            "directory_code": "attendance-archive",
            "directory_path": "/考勤制度/归档",
            "directory_description": "考勤制度归档目录",
            "status": "ACTIVE",
            "metadata": {"owner": "HR"},
        },
    }


def test_delete_directory_route_returns_business_response(monkeypatch):
    """Delete-directory route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/directories/delete",
        json={
            "kb_code": "hr-policy",
            "directory_code": "attendance-archive",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "error": None,
        "data": {
            "kb_code": "hr-policy",
            "directory_code": "attendance-archive",
            "is_deleted": True,
        },
    }


def test_delete_directory_route_maps_missing_directory_to_404(monkeypatch):
    """Delete-directory should return 404 when the directory does not exist."""

    class BrokenKBService(FakeKBService):
        def delete_directory(self, request):
            raise KnowledgeBaseValidationError(
                f"directory not found: {request.directory_code}"
            )

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/directories/delete",
        json={
            "kb_code": "hr-policy",
            "directory_code": "attendance-archive",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == "KB_DIRECTORY_NOT_FOUND"


def test_update_directory_route_returns_business_response(monkeypatch):
    """Update-directory route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/directories/update",
        json={
            "kb_code": "hr-policy",
            "directory_code": "attendance-archive",
            "directory_name": "历史归档",
            "directory_description": "更新后的目录说明",
            "metadata": {"owner": "HR"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "error": None,
        "data": {
            "kb_code": "hr-policy",
            "directory_code": "attendance-archive",
            "directory_path": "/考勤制度/历史归档",
            "directory_description": "更新后的目录说明",
            "metadata": {"owner": "HR"},
        },
    }


def test_update_directory_route_maps_name_conflict_to_409(monkeypatch):
    """Update-directory should return 409 for sibling name conflicts."""

    class BrokenKBService(FakeKBService):
        def update_directory(self, request):
            raise KnowledgeBaseValidationError(
                f"directory name already exists under parent: {request.directory_name}"
            )

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/directories/update",
        json={
            "kb_code": "hr-policy",
            "directory_code": "attendance-archive",
            "directory_name": "历史归档",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["error_code"] == "KB_DIRECTORY_NAME_CONFLICT"


def test_update_directory_route_maps_request_validation_to_standard_error(monkeypatch):
    """Update-directory validation should use the standard error envelope."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/directories/update",
        json={
            "kb_code": "hr-policy",
            "directory_code": "attendance-archive",
            "directory_name": "/考勤制度",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422
    assert response.json()["message"] == "error"
    assert response.json()["data"] is None
    assert response.json()["error"]["type"] == "request_invalid"
    assert response.json()["error"]["error_code"] == "REQUEST_VALIDATION_FAILED"
    assert response.json()["error"]["error_message"] == "request validation failed"
    assert response.json()["error"]["details"]["errors"]


def test_update_file_route_returns_business_response(monkeypatch):
    """Update-file route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledge-items/update",
        json={
            "kb_code": "hr-policy",
            "file_code": "attendance-policy-pdf",
            "file_name": "异常考勤处理办法（正式版）.pdf",
            "file_description": "更新后的文件说明",
            "metadata": {"owner": "HR"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "error": None,
        "data": {
            "kb_code": "hr-policy",
            "file_code": "attendance-policy-pdf",
            "file_path": "/考勤制度/异常考勤处理办法（正式版）.pdf",
            "file_description": "更新后的文件说明",
            "metadata": {"owner": "HR"},
        },
    }


def test_update_file_route_maps_name_conflict_to_409(monkeypatch):
    """Update-file should return 409 for sibling name conflicts."""

    class BrokenKBService(FakeKBService):
        def update_file(self, request):
            raise KnowledgeBaseValidationError(
                f"file name already exists under parent: {request.file_name}"
            )

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/knowledge-items/update",
        json={
            "kb_code": "hr-policy",
            "file_code": "attendance-policy-pdf",
            "file_name": "异常考勤处理办法（正式版）.pdf",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["error_code"] == "KB_FILE_NAME_CONFLICT"


def test_update_file_route_maps_request_validation_to_standard_error(monkeypatch):
    """Update-file validation should use the standard error envelope."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/knowledge-items/update",
        json={
            "kb_code": "hr-policy",
            "file_code": "attendance-policy-pdf",
            "file_name": "/demo.pdf",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422
    assert response.json()["message"] == "error"
    assert response.json()["data"] is None
    assert response.json()["error"]["type"] == "request_invalid"
    assert response.json()["error"]["error_code"] == "REQUEST_VALIDATION_FAILED"
    assert response.json()["error"]["error_message"] == "request validation failed"
    assert response.json()["error"]["details"]["errors"]


def test_delete_knowledge_item_route_returns_business_response(monkeypatch):
    """Delete-knowledge-item route should delegate to the ingestion service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledge-items/delete",
        json={"kb_code": "hr-policy", "file_code": "file-001"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "error": None,
        "data": {
            "kb_code": "hr-policy",
            "file_code": "file-001",
            "is_deleted": True,
        },
    }


def test_write_file_route_returns_business_response(monkeypatch):
    """Write-file route should delegate to the ingestion service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/write-file",
        json={
            "kb_code": "hr-policy",
            "file_code": "file-001",
            "file_path": "/考勤制度/异常考勤处理办法.pdf",
            "file_description": None,
            "file_content": "ZmFrZS1iYXNlNjQ=",
            "version": "V1",
            "source_code": "oa",
            "status": "ACTIVE",
            "metadata": {"owner_dept": "HR"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "error": None,
        "data": {
            "kb_code": "hr-policy",
            "file_code": "file-001",
            "type_code": "pdf",
            "file_path": "/考勤制度/异常考勤处理办法.pdf",
            "file_description": None,
            "version": "V1",
            "status": "ACTIVE",
            "metadata": {"owner_dept": "HR"},
        },
    }


def test_write_file_route_maps_duplicate_version_to_409(monkeypatch):
    """Duplicate file_code/version should surface as a conflict response."""

    class BrokenKBService(FakeKBService):
        def write_file(self, request):
            raise KnowledgeBaseValidationError(
                f"item_code/version already exists: {request.file_code}/{request.version}"
            )

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/write-file",
        json={
            "kb_code": "hr-policy",
            "file_code": "file-001",
            "file_path": "/考勤制度/异常考勤处理办法.pdf",
            "file_description": None,
            "file_content": "ZmFrZS1iYXNlNjQ=",
            "version": "V1",
            "source_code": "oa",
            "status": "ACTIVE",
            "metadata": {"owner_dept": "HR"},
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == 409
    assert response.json()["message"] == "error"
    assert response.json()["data"] is None
    assert response.json()["error"]["type"] == "conflict"
    assert response.json()["error"]["error_code"] == "KB_FILE_VERSION_CONFLICT"


def test_write_file_route_maps_soft_deleted_file_code_to_409(monkeypatch):
    """Soft-deleted file_code conflicts should return a standard business conflict response."""

    class BrokenKBService(FakeKBService):
        def write_file(self, request):
            raise KnowledgeBaseValidationError(
                f"file_code is occupied by a soft-deleted knowledge item: {request.file_code}"
            )

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/write-file",
        json={
            "kb_code": "hr-policy",
            "file_code": "file-001",
            "file_path": "/考勤制度/异常考勤处理办法.pdf",
            "file_description": None,
            "file_content": "ZmFrZS1iYXNlNjQ=",
            "version": "V1",
            "source_code": "oa",
            "status": "ACTIVE",
            "metadata": {"owner_dept": "HR"},
        },
    )

    assert response.status_code == 409
    assert (
        response.json()["error"]["error_code"] == "KB_FILE_CODE_SOFT_DELETED_CONFLICT"
    )


def test_write_file_route_maps_request_validation_to_standard_error(monkeypatch):
    """Write-file request validation should use the standard error envelope."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/write-file",
        json={
            "kb_code": "hr-policy",
            "file_path": "/考勤制度/异常考勤处理办法.pdf",
            "file_content": "ZmFrZS1iYXNlNjQ=",
            "version": "V1",
            "source_code": "oa",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422
    assert response.json()["message"] == "error"
    assert response.json()["data"] is None
    assert response.json()["error"]["type"] == "request_invalid"
    assert response.json()["error"]["error_code"] == "REQUEST_VALIDATION_FAILED"
    assert response.json()["error"]["error_message"] == "request validation failed"
    assert response.json()["error"]["details"]["errors"]


def test_write_index_route_returns_business_response(monkeypatch):
    """Write-index route should delegate to the ingestion service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/write-index",
        json={
            "kb_code": "hr-policy",
            "file_code": "file-001",
            "version": "V1",
            "markdown_content": "# 异常考勤处理办法",
            "chunks": [
                {
                    "chunk_no": 1,
                    "start_line": 1,
                    "end_line": 100,
                    "chunk_text": "xxxxx",
                    "embedding": [0.1, 0.2],
                    "char_start": 1,
                    "char_end": 732,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "error": None,
        "data": {
            "kb_code": "hr-policy",
            "file_code": "file-001",
            "version": "V1",
            "chunks": {"count": 1},
        },
    }


def test_write_index_route_maps_missing_file_to_404(monkeypatch):
    """Write-index should return 404 when the file does not exist."""

    class BrokenKBService(FakeKBService):
        def write_index(self, request):
            raise KnowledgeBaseValidationError(
                f"knowledge item not found: {request.file_code}"
            )

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/write-index",
        json={
            "kb_code": "hr-policy",
            "file_code": "file-001",
            "version": "V1",
            "markdown_content": "# missing",
            "chunks": [
                {
                    "chunk_no": 1,
                    "start_line": 1,
                    "end_line": 100,
                    "chunk_text": "xxxxx",
                    "embedding": [0.1, 0.2],
                }
            ],
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"
    assert response.json()["error"]["error_code"] == "KB_FILE_NOT_FOUND"
    assert response.json()["error"]["details"] == {"file_code": "file-001"}


def test_import_knowledge_item_route_returns_business_response(monkeypatch):
    """Combined import route should delegate to the ingestion service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledge-items/import",
        json={
            "kb_code": "hr-policy",
            "file_code": "file-001",
            "file_path": "/考勤制度/异常考勤处理办法.pdf",
            "file_description": "异常考勤制度原文",
            "file_content": "ZmFrZS1iYXNlNjQ=",
            "version": "V1",
            "source_code": "oa",
            "status": "ACTIVE",
            "metadata": {"owner_dept": "HR"},
            "markdown_content": "# 异常考勤处理办法",
            "chunks": [
                {
                    "chunk_no": 1,
                    "start_line": 1,
                    "end_line": 100,
                    "chunk_text": "xxxxx",
                    "embedding": [0.1, 0.2],
                    "char_start": 1,
                    "char_end": 732,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "error": None,
        "data": {
            "kb_code": "hr-policy",
            "file_code": "file-001",
            "type_code": "pdf",
            "file_path": "/考勤制度/异常考勤处理办法.pdf",
            "file_description": "异常考勤制度原文",
            "version": "V1",
            "status": "ACTIVE",
            "metadata": {"owner_dept": "HR"},
            "chunks": {"count": 1},
        },
    }


def test_import_knowledge_item_route_maps_duplicate_version_to_409(monkeypatch):
    """Combined import route should surface version conflicts as 409."""

    class BrokenKBService(FakeKBService):
        def import_knowledge_item(self, request):
            raise KnowledgeBaseValidationError(
                f"item_code/version already exists: {request.file_code}/{request.version}"
            )

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/knowledge-items/import",
        json={
            "kb_code": "hr-policy",
            "file_code": "file-001",
            "file_path": "/考勤制度/异常考勤处理办法.pdf",
            "file_content": "ZmFrZS1iYXNlNjQ=",
            "version": "V1",
            "source_code": "oa",
            "status": "ACTIVE",
            "markdown_content": "# 异常考勤处理办法",
            "chunks": [
                {
                    "chunk_no": 1,
                    "start_line": 1,
                    "end_line": 100,
                    "chunk_text": "xxxxx",
                    "embedding": [0.1, 0.2],
                }
            ],
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["type"] == "conflict"
    assert response.json()["error"]["error_code"] == "KB_FILE_VERSION_CONFLICT"
    assert response.json()["error"]["details"] == {
        "file_code": "file-001",
        "version": "V1",
    }


def test_import_knowledge_item_route_maps_request_validation_to_standard_error(
    monkeypatch,
):
    """Combined import request validation should use the standard error envelope."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/knowledge-items/import",
        json={
            "kb_code": "hr-policy",
            "file_path": "/考勤制度/异常考勤处理办法.pdf",
            "file_content": "ZmFrZS1iYXNlNjQ=",
            "version": "V1",
            "source_code": "oa",
            "markdown_content": "# 异常考勤处理办法",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422
    assert response.json()["message"] == "error"
    assert response.json()["data"] is None
    assert response.json()["error"]["type"] == "request_invalid"
    assert response.json()["error"]["error_code"] == "REQUEST_VALIDATION_FAILED"
    assert response.json()["error"]["error_message"] == "request validation failed"
    assert response.json()["error"]["details"]["errors"]


def test_list_dir_route_returns_filesystem_entries(monkeypatch):
    """List-dir route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/list_dir",
        json={
            "kb_codes": ["integration-kb"],
            "path": "Integration KB/dir1/",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == [
        {
            "kb_code": "integration-kb",
            "name": "/Integration KB/dir1/doc.md",
            "type": "file",
            "size": 100,
        }
    ]


def test_list_dir_route_emits_summary_logs(monkeypatch):
    """List-dir route should log request, service resolution, and response summary."""
    service = FakeKBService()
    info_messages: list[str] = []

    monkeypatch.setattr(
        routes.logger,
        "info",
        lambda message, *args, **kwargs: info_messages.append(
            message % args if args else message
        ),
    )
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/list_dir",
        json={
            "kb_codes": ["integration-kb"],
            "path": "*.md",
        },
    )

    assert response.status_code == 200
    assert info_messages == [
        "list_dir request received: path=*.md",
        "list_dir resolved service: service_class=FakeKBService",
        "list_dir service call succeeded: path=*.md, item_count=1",
        "list_dir response ready: code=200, item_count=1",
    ]


def test_list_dir_route_requires_kb_codes(monkeypatch):
    """List-dir route should reject requests that omit kb_codes."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/list_dir",
        json={
            "path": "/",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == "REQUEST_VALIDATION_FAILED"


def test_list_dir_route_maps_validation_error_to_standard_error(monkeypatch):
    """List-dir business validation should use the standardized error envelope."""

    class BrokenKBService(FakeKBService):
        def list_dir(self, request):
            raise KnowledgeBaseValidationError("path contains invalid segments")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/list_dir",
        json={
            "kb_codes": ["integration-kb"],
            "path": "../secret",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422
    assert response.json()["message"] == "error"
    assert response.json()["data"] is None
    assert response.json()["error"]["type"] == "business_validation"
    assert response.json()["error"]["error_code"] == "KB_LIST_DIR_INVALID"
    assert response.json()["error"]["details"] == {"path": "../secret"}


def test_list_dir_route_maps_missing_directory_to_404(monkeypatch):
    """List-dir missing paths should map to the standardized not-found response."""

    class BrokenKBService(FakeKBService):
        def list_dir(self, request):
            raise KnowledgeBaseValidationError(f"directory not found: {request.path}")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/list_dir",
        json={
            "kb_codes": ["integration-kb"],
            "path": "missing-dir",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"
    assert response.json()["error"]["error_code"] == "KB_DIRECTORY_NOT_FOUND"
    assert response.json()["error"]["details"] == {"path": "missing-dir"}


def test_list_dir_route_maps_configuration_error_to_503(monkeypatch):
    """List-dir configuration failures should use the standardized error envelope."""

    class BrokenKBService(FakeKBService):
        def list_dir(self, request):
            raise KnowledgeBaseConfigurationError("KB runtime is not configured")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/list_dir",
        json={
            "kb_codes": ["integration-kb"],
            "path": "/",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "configuration_error"
    assert response.json()["error"]["error_code"] == "KB_RUNTIME_CONFIG_ERROR"


def test_glob_route_returns_matching_entries(monkeypatch):
    """Glob route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/glob",
        json={
            "kb_codes": ["integration-kb"],
            "path": "Integration KB/*.md",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "kb_code": "integration-kb",
            "name": "/Integration KB/dir1/doc.md",
            "type": "file",
            "size": 100,
        }
    ]


def test_glob_route_requires_kb_codes(monkeypatch):
    """Glob route should reject requests that omit kb_codes."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/glob",
        json={
            "path": "*.md",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == "REQUEST_VALIDATION_FAILED"


def test_glob_route_maps_validation_error_to_standard_error(monkeypatch):
    """Glob business validation should use the standardized error envelope."""

    class BrokenKBService(FakeKBService):
        def glob(self, request):
            raise KnowledgeBaseValidationError("path contains invalid segments")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/glob",
        json={
            "kb_codes": ["integration-kb"],
            "path": "../secret",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422
    assert response.json()["message"] == "error"
    assert response.json()["data"] is None
    assert response.json()["error"]["type"] == "business_validation"
    assert response.json()["error"]["error_code"] == "KB_GLOB_INVALID"
    assert response.json()["error"]["details"] == {"path": "../secret"}


def test_glob_route_maps_configuration_error_to_503(monkeypatch):
    """Glob configuration failures should use the standardized error envelope."""

    class BrokenKBService(FakeKBService):
        def glob(self, request):
            raise KnowledgeBaseConfigurationError("KB runtime is not configured")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/glob",
        json={
            "kb_codes": ["integration-kb"],
            "path": "*.md",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "configuration_error"
    assert response.json()["error"]["error_code"] == "KB_RUNTIME_CONFIG_ERROR"


def test_read_file_route_returns_requested_text(monkeypatch):
    """Read-file route should delegate to the KB service and return the standard envelope."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/read-file",
        json={
            "kb_codes": ["hr-policy"],
            "path": "Integration KB/dir1/doc.md",
            "content_type": "markdown",
            "start_line": 1,
            "end_line": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "kb_code": "hr-policy",
        "path": "/Integration KB/dir1/doc.md",
        "content_type": "markdown",
        "start_line": 1,
        "end_line": 2,
        "data": "line1\nline2\n",
        "reached_eof": True,
    }


def test_read_file_route_returns_access_url_for_binary_files(monkeypatch):
    """Read-file should return a URL for binary objects."""

    class BinaryKBService(FakeKBService):
        def fetch(self, request):
            return KnowledgeItemFetchResponse(
                kb_code=request.kb_codes[0],
                path=request.path,
                content_type="original",
                url="https://minio.example/knowledge-base/7/dir1/doc.pdf/v1/doc.pdf?ttl=3600",
            )

    client = make_test_client(monkeypatch, BinaryKBService())
    response = client.post(
        "/api/v1/read-file",
        json={
            "kb_codes": ["hr-policy"],
            "path": "Integration KB/dir1/doc.pdf",
            "content_type": "original",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "kb_code": "hr-policy",
        "path": "/Integration KB/dir1/doc.pdf",
        "content_type": "original",
        "url": "https://minio.example/knowledge-base/7/dir1/doc.pdf/v1/doc.pdf?ttl=3600",
    }


def test_download_file_route_returns_binary_stream(monkeypatch):
    """Download-file should return raw file bytes with attachment headers."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/download-file",
        json={
            "kb_codes": ["hr-policy"],
            "path": "Integration KB/dir1/doc.pdf",
        },
    )

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 test payload"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="doc.pdf"'


def test_download_file_route_supports_non_ascii_filename(monkeypatch):
    """Download-file should encode non-ASCII filenames safely in headers."""

    class UnicodeFilenameKBService(FakeKBService):
        def download_file(self, request):
            return {
                "filename": "开源项目最佳实践汇报.md",
                "media_type": "text/markdown",
                "content": b"# hello\n",
            }

    client = make_test_client(monkeypatch, UnicodeFilenameKBService())
    response = client.post(
        "/api/v1/download-file",
        json={
            "kb_codes": ["demo"],
            "path": "DEMO知识库/考勤制度/开源项目最佳实践汇报.md",
        },
    )

    assert response.status_code == 200
    assert response.content == b"# hello\n"
    assert response.headers["content-type"].startswith("text/markdown")
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="download.md"; '
        "filename*=UTF-8''%E5%BC%80%E6%BA%90%E9%A1%B9%E7%9B%AE%E6%9C%80%E4%BD%B3%E5%AE%9E%E8%B7%B5%E6%B1%87%E6%8A%A5.md"
    )


def test_read_file_route_requires_kb_codes(monkeypatch):
    """Read-file route should reject requests that omit kb_codes."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/read-file",
        json={
            "path": "Integration KB/dir1/doc.md",
            "content_type": "markdown",
            "start_line": 1,
            "end_line": 2,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == "REQUEST_VALIDATION_FAILED"


def test_read_file_route_maps_validation_error_to_standard_error(monkeypatch):
    """Read-file business validation should use the standardized error envelope."""

    class BrokenKBService(FakeKBService):
        def fetch(self, request):
            raise KnowledgeBaseValidationError("start_line must be greater than 0")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/read-file",
        json={
            "kb_codes": ["hr-policy"],
            "path": "Integration KB/dir1/doc.md",
            "content_type": "markdown",
            "start_line": 0,
            "end_line": 2,
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422
    assert response.json()["message"] == "error"
    assert response.json()["data"] is None
    assert response.json()["error"]["type"] == "business_validation"
    assert response.json()["error"]["error_code"] == "KB_READ_FILE_INVALID"
    assert response.json()["error"]["details"] == {
        "path": "Integration KB/dir1/doc.md",
        "kb_codes": ["hr-policy"],
    }


def test_read_file_route_maps_not_found_to_404(monkeypatch):
    """Read-file missing files should map to the standardized not-found response."""

    class BrokenKBService(FakeKBService):
        def fetch(self, request):
            raise KnowledgeBaseValidationError(f"file not found: {request.path}")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/read-file",
        json={
            "kb_codes": ["hr-policy"],
            "path": "Integration KB/dir1/missing.pdf",
            "content_type": "original",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"
    assert response.json()["error"]["error_code"] == "KB_FILE_NOT_FOUND"
    assert response.json()["error"]["details"] == {
        "path": "Integration KB/dir1/missing.pdf",
        "kb_codes": ["hr-policy"],
    }


def test_read_file_route_maps_configuration_error_to_503(monkeypatch):
    """Read-file configuration failures should use the standardized error envelope."""

    class BrokenKBService(FakeKBService):
        def fetch(self, request):
            raise KnowledgeBaseConfigurationError("fetch runtime is not configured")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/read-file",
        json={
            "kb_codes": ["hr-policy"],
            "path": "Integration KB/dir1/doc.md",
            "content_type": "markdown",
            "start_line": 1,
            "end_line": 2,
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "configuration_error"
    assert response.json()["error"]["error_code"] == "KB_RUNTIME_CONFIG_ERROR"


def test_create_knowledge_base_route_maps_configuration_error_to_503(monkeypatch):
    """Missing KB runtime configuration should surface as a service-unavailable response."""

    class MisconfiguredKBService(FakeKBService):
        def create_knowledge_base(self, request):
            raise KnowledgeBaseConfigurationError("KB_OPENGAUSS_DSN is required")

    client = make_test_client(monkeypatch, MisconfiguredKBService())
    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={
            "knName": "人力制度知识库",
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "KB_OPENGAUSS_DSN is required",
        "resultObject": {},
    }


def test_create_knowledge_base_route_maps_unexpected_exception_to_standard_500(
    monkeypatch,
):
    """Unexpected route failures should still use the standard KB error envelope."""

    class BrokenKBService(FakeKBService):
        def create_knowledge_base(self, request):
            raise RuntimeError("duplicate key value violates unique constraint")

    monkeypatch.setattr(
        "by_qa.main.get_knowledge_base_service", lambda: BrokenKBService()
    )
    monkeypatch.setattr(
        "by_qa.main.get_knowledge_item_ingestion_service", lambda: BrokenKBService()
    )
    monkeypatch.setattr(
        "by_qa.main.get_knowledge_item_search_service", lambda: BrokenKBService()
    )
    monkeypatch.setattr("by_qa.main.get_adapter", lambda: object())
    monkeypatch.setattr("by_qa.main.get_instant_search_engine", lambda: object())
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={"knName": "Demo KB"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "duplicate key value violates unique constraint",
        "resultObject": {},
    }


def test_search_route_returns_chunk_oriented_business_response(monkeypatch):
    """Search route should delegate to the KB retrieval service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledge-items/search",
        json={
            "query": "员工请假制度怎么规定",
            "kb_codes": ["hr-policy"],
            "top_k": 10,
            "vector_top_k": 40,
            "text_top_k": 30,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["data"]["items"][0]["file_code"] == "item-1"
    assert body["data"]["items"][0]["file_path"] == "/employee-handbook.md"
    assert body["data"]["meta"]["returned_count"] == 1


def test_search_route_emits_summary_logs(monkeypatch):
    """Search route should emit sanitized request and response summary logs."""
    service = FakeKBService()
    info_messages: list[str] = []

    monkeypatch.setattr(
        routes.logger,
        "info",
        lambda message, *args, **kwargs: info_messages.append(
            message % args if args else message
        ),
    )
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledge-items/search",
        json={
            "query": "员工请假制度怎么规定",
            "kb_codes": ["hr-policy"],
            "top_k": 10,
            "vector_top_k": 40,
            "text_top_k": 30,
        },
    )

    assert response.status_code == 200
    assert info_messages == [
        "search_knowledge_items request received: query=员工请假制度怎么规定, kb_code_count=1, top_k=10, vector_top_k=40, text_top_k=30, source_code_count=0, type_code_count=0",
        "search_knowledge_items resolved service: service_class=FakeKBService",
        "search_knowledge_items service call succeeded: returned_count=1, top_k=10",
        "search_knowledge_items response ready: code=200, returned_count=1",
    ]


def test_search_route_maps_validation_error_to_standard_error(monkeypatch):
    """Search business validation should use the standardized error envelope."""

    class BrokenSearchService(FakeKBService):
        def search(self, request):
            raise KnowledgeBaseValidationError("kb_codes must not be empty")

    client = make_test_client(monkeypatch, BrokenSearchService())
    response = client.post(
        "/api/v1/knowledge-items/search",
        json={
            "query": "员工请假制度怎么规定",
            "kb_codes": ["hr-policy"],
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == 422
    assert response.json()["message"] == "error"
    assert response.json()["data"] is None
    assert response.json()["error"]["type"] == "business_validation"
    assert response.json()["error"]["error_code"] == "KB_SEARCH_INVALID"


def test_search_route_maps_configuration_error_to_503(monkeypatch):
    """Search configuration failures should use the standardized error envelope."""

    class BrokenSearchService(FakeKBService):
        def search(self, request):
            raise KnowledgeBaseConfigurationError(
                "EMBEDDING_BASE_URL is required for retrieval"
            )

    client = make_test_client(monkeypatch, BrokenSearchService())
    response = client.post(
        "/api/v1/knowledge-items/search",
        json={
            "query": "员工请假制度怎么规定",
            "kb_codes": ["hr-policy"],
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "configuration_error"
    assert response.json()["error"]["error_code"] == "KB_RUNTIME_CONFIG_ERROR"
