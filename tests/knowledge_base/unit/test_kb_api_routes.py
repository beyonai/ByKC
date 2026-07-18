"""Tests for knowledge-base API routes."""

import io
import zipfile
from decimal import Decimal

from fastapi.testclient import TestClient

from by_qa.knowledge_base.api import routes
from by_qa.knowledge_base.api.metadata_schemas import SearchFileHit
from by_qa.knowledge_base.api.schemas import (
    CreateKnowledgeBaseResponse,
    KnowledgeItemListDirItem,
    KnowledgeItemListDirResponse,
    MoveKnowledgeItemResult,
    MoveKnowledgeItemsResponse,
    MoveKnowledgeItemsSummary,
    SearchHit,
)
from by_qa.knowledge_base.services.errors import (
    KnowledgeBaseConfigurationError,
    KnowledgeBaseValidationError,
)
from by_qa.knowledge_base.services.zip_batch_import_service import (
    ImportItem,
    ImportSummary,
    ZipBatchImportResult,
)
from by_qa.main import app


class FakeKBService:
    """Service double used by route tests."""

    def __init__(self):
        self.created_requests = []
        self.created_directory_requests = []
        self.import_calls = []
        self.metadata_get_requests = []
        self.file_to_markdown_calls = []
        self.file_build_task_requests = []
        self.file_build_task_runs = []
        self.move_requests = []
        self.document_update_requests = []

    async def create_knowledge_base(self, request):
        self.created_requests.append(request)
        return CreateKnowledgeBaseResponse(
            kb_code="7",
            kb_name=request.kb_name,
            kb_description=request.kb_description,
        )

    async def create_directory(self, request):
        self.created_directory_requests.append(request)
        return None

    async def delete_directory(self, request):
        return None

    async def update_directory(self, request):
        return None

    async def delete_knowledge_base(self, request):
        return None

    async def update_knowledge_base(self, request):
        return None

    async def delete_knowledge_item(self, request):
        return None

    async def move_knowledge_items(self, request):
        self.move_requests.append(request)
        return MoveKnowledgeItemsResponse(
            data=[
                MoveKnowledgeItemResult(
                    source_path=request.source_path[0],
                    target_path="/archive/a.md",
                    success=True,
                )
            ],
            summary=MoveKnowledgeItemsSummary(total=1, succeeded=1, failed=0),
        )

    async def upload_file(self, request):
        self.import_calls.append(request)
        return None

    async def update_file(self, request):
        self.document_update_requests.append(request)
        return None

    async def convert_uploaded_file_to_markdown(
        self, *, file_bytes, filename, document_chunking_service
    ):
        self.file_to_markdown_calls.append(
            {
                "file_bytes": file_bytes,
                "filename": filename,
                "document_chunking_service": document_chunking_service,
            }
        )
        return {
            "filename": "policy.md",
            "content": b"# Converted Policy\n",
        }

    async def create_file_to_markdown_index_task(self, request):
        self.file_build_task_requests.append(request)
        return 9901

    async def execute_file_to_markdown_index_task(  # pylint: disable=unused-argument
        self, request, *, document_chunking_service, build_task_id
    ):
        self.file_build_task_runs.append((request, build_task_id))
        return None

    async def file_build_status(self, request):
        return {
            "status": "running",
            "currentStep": "chunking",
            "statusDict": [
                {
                    "standCode": "complete",
                    "standDisplayValue": "已完成",
                    "standDisplayValueEn": "complete",
                },
                {
                    "standCode": "failed",
                    "standDisplayValue": "失败",
                    "standDisplayValueEn": "failed",
                },
                {
                    "standCode": "running",
                    "standDisplayValue": "构建中",
                    "standDisplayValueEn": "running",
                },
            ],
            "stepDict": [
                {
                    "standCode": "markdown",
                    "standDisplayValue": "原始文件转 Markdown",
                    "standDisplayValueEn": "markdown",
                },
                {
                    "standCode": "chunking",
                    "standDisplayValue": "文档切片",
                    "standDisplayValueEn": "chunking",
                },
                {
                    "standCode": "vectorizing",
                    "standDisplayValue": "切片向量化",
                    "standDisplayValueEn": "vectorizing",
                },
                {
                    "standCode": "complete",
                    "standDisplayValue": "已完成",
                    "standDisplayValueEn": "complete",
                },
            ],
        }

    async def search(self, request):
        return [
            SearchHit(
                kb_code="hr-policy",
                file_path="/employee-handbook.md",
                chunk_no=1,
                chunk_id=42,
                chunk_text="员工请假应至少提前一天提交申请。",
                score=0.91,
                image_path="",
                start_line=1,
                end_line=3,
            )
        ]

    async def list_dir(self, request):
        return KnowledgeItemListDirResponse(
            data=[
                KnowledgeItemListDirItem(
                    kb_code=request.kb_code,
                    name="/dir1/doc.md",
                    type="file",
                    size=100,
                )
            ]
        )

    async def glob(self, request):
        return KnowledgeItemListDirResponse(
            data=[
                KnowledgeItemListDirItem(
                    kb_code=request.kb_code,
                    name="/dir1/doc.md",
                    type="file",
                    size=100,
                )
            ]
        )

    async def read_file(self, request):
        return {
            "knCode": request.kb_code,
            "filePath": request.file_path,
            "startLine": request.start_line,
            "endLine": request.end_line,
            "data": "line1\nline2\n",
            "reachedEof": True,
        }

    async def download_file(self, request):
        return {
            "filename": "doc.pdf",
            "media_type": "application/pdf",
            "content": b"%PDF-1.4 test payload",
        }

    async def get_metadata(self, request):
        self.metadata_get_requests.append(request)
        return {
            "会议主题": {
                "valueType": "string",
                "value": "DataCloud平台需求确认会",
            },
            "会议日期": {
                "valueType": "datetime",
                "value": "2026-05-25",
            },
        }


class FakeRouteDocumentChunkingService:
    """Document conversion double used by route tests."""

    def __init__(self):
        self.extract_calls = []

    def extract_text_from_file(self, file_bytes, file_type):
        self.extract_calls.append({"file_bytes": file_bytes, "file_type": file_type})
        return "# Converted Policy\n"


def make_test_client(monkeypatch, service):
    """Create a TestClient with unrelated startup dependencies stubbed out."""

    async def get_service():
        return service

    monkeypatch.setattr("by_qa.main._get_or_build_knowledge_base_service", get_service)
    monkeypatch.setattr(
        "by_qa.main._get_or_build_knowledge_item_ingestion_service", get_service
    )
    monkeypatch.setattr(
        "by_qa.main._get_or_build_knowledge_item_search_service", get_service
    )
    monkeypatch.setattr(
        "by_qa.main._get_or_build_file_metadata_query_service", get_service
    )

    chunking_service = FakeRouteDocumentChunkingService()

    async def get_document_chunking_service():
        return chunking_service

    monkeypatch.setattr(
        "by_qa.main._get_or_build_document_chunking_service",
        get_document_chunking_service,
    )
    monkeypatch.setattr("by_qa.main.get_adapter", lambda: object())
    monkeypatch.setattr("by_qa.main.get_instant_search_engine", lambda: object())
    client = TestClient(app)
    client.fake_document_chunking_service = chunking_service
    return client


def test_metadata_get_route_returns_file_metadata(monkeypatch):
    """POST /knowledgeItems/metadata/get should query file metadata only."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeItems/metadata/get",
        json={
            "knCode": "1",
            "filePath": "/1.md",
            "metadataFieldList": ["会议主题", "会议日期"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {
            "metadata": {
                "会议主题": {
                    "valueType": "string",
                    "value": "DataCloud平台需求确认会",
                },
                "会议日期": {
                    "valueType": "datetime",
                    "value": "2026-05-25",
                },
            }
        },
    }
    assert service.metadata_get_requests[0].kb_code == "1"
    assert service.metadata_get_requests[0].file_path == "/1.md"


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


def test_move_knowledge_items_route_delegates_to_service(monkeypatch):
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeItems/move",
        json={
            "knCode": "1",
            "sourcePath": ["/docs/a.md/"],
            "targetFilePath": "/archive/a.md",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {
            "data": [
                {
                    "sourcePath": "/docs/a.md",
                    "targetPath": "/archive/a.md",
                    "success": True,
                    "error": None,
                }
            ],
            "summary": {"total": 1, "succeeded": 1, "failed": 0},
        },
    }
    assert service.move_requests[0].source_path == ["/docs/a.md"]
    assert service.move_requests[0].target_file_path == "/archive/a.md"


def test_move_knowledge_items_route_maps_validation_error(monkeypatch):
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeItems/move",
        json={
            "knCode": "1",
            "sourcePath": ["/docs/a.md", "/docs/b.md"],
            "targetFilePath": "/archive/a.md",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resultCode"] == "-1"
    assert body["resultMsg"] == "request validation failed"
    assert service.move_requests == []


def test_create_knowledge_base_route_maps_request_validation_to_documented_error(
    monkeypatch,
):
    """Create-knowledge-base validation should use the documented error envelope."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={"knDescription": "公司人事制度与流程文档"},
    )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"
    assert response.json()["resultMsg"] == "request validation failed"
    assert response.json()["resultObject"]["errors"]


def test_create_knowledge_base_route_maps_duplicate_name_to_documented_error(
    monkeypatch,
):
    """Create-knowledge-base duplicate names should use the documented error envelope."""

    class DuplicateNameKBService(FakeKBService):
        async def create_knowledge_base(self, request):
            raise KnowledgeBaseValidationError(
                f"knowledge base name already exists: {request.kb_name}"
            )

    client = make_test_client(monkeypatch, DuplicateNameKBService())
    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={"knName": "人力制度知识库"},
    )

    assert response.status_code == 200
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
        async def update_knowledge_base(self, request):
            raise KnowledgeBaseValidationError(
                f"knowledge base name already exists: {request.kb_name}"
            )

    client = make_test_client(monkeypatch, DuplicateNameKBService())
    response = client.post(
        "/api/v1/knowledgeBases/update",
        json={"knCode": "hr-policy", "knName": "人力制度知识库"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "knowledge base name already exists: 人力制度知识库",
        "resultObject": {},
    }


def test_update_knowledge_base_route_maps_unexpected_exception_to_documented_error(
    monkeypatch,
):
    """Update-knowledge-base unexpected failures should use the documented error envelope."""

    class BrokenKBService(FakeKBService):
        async def update_knowledge_base(self, request):
            raise RuntimeError('column "kb_code" does not exist')

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/knowledgeBases/update",
        json={"knCode": "7", "knName": "人力制度知识库"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": 'column "kb_code" does not exist',
        "resultObject": {},
    }


def test_create_directory_route_returns_business_response(monkeypatch):
    """Create-directory route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/directories/create",
        json={
            "knCode": "hr-policy",
            "directoryPath": "/考勤制度/归档",
            "directoryDescription": "考勤制度归档目录",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {},
    }


def test_delete_directory_route_returns_business_response(monkeypatch):
    """Delete-directory route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/directories/delete",
        json={
            "knCode": "hr-policy",
            "directoryPath": "/考勤制度/归档",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {},
    }


def test_delete_directory_route_maps_missing_directory_to_documented_error(monkeypatch):
    """Delete-directory should use the documented error envelope when the directory does not exist."""

    class BrokenKBService(FakeKBService):
        async def delete_directory(self, request):
            raise KnowledgeBaseValidationError(
                f"directory not found: {request.directory_path}"
            )

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/directories/delete",
        json={
            "knCode": "hr-policy",
            "directoryPath": "/考勤制度/归档",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "directory not found: /考勤制度/归档",
        "resultObject": {},
    }


def test_update_directory_route_returns_business_response(monkeypatch):
    """Update-directory route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/directories/update",
        json={
            "knCode": "hr-policy",
            "directoryPath": "/考勤制度/归档",
            "directoryName": "历史归档",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {},
    }


def test_update_directory_route_maps_name_conflict_to_documented_error(monkeypatch):
    """Update-directory should use the documented error envelope for sibling name conflicts."""

    class BrokenKBService(FakeKBService):
        async def update_directory(self, request):
            raise KnowledgeBaseValidationError(
                f"directory name already exists under parent: {request.directory_name}"
            )

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/directories/update",
        json={
            "knCode": "hr-policy",
            "directoryPath": "/考勤制度/归档",
            "directoryName": "历史归档",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "directory name already exists under parent: 历史归档",
        "resultObject": {},
    }


def test_update_directory_route_maps_request_validation_to_standard_error(monkeypatch):
    """Update-directory validation should use the documented error envelope."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/directories/update",
        json={
            "knCode": "hr-policy",
            "directoryPath": "/考勤制度/归档",
            "directoryName": "/考勤制度",
        },
    )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"
    assert response.json()["resultMsg"] == "request validation failed"
    assert response.json()["resultObject"]["errors"]


def test_delete_knowledge_item_route_returns_business_response(monkeypatch):
    """Delete-knowledge-item route should delegate to the ingestion service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeItems/delete",
        json={
            "knCode": "hr-policy",
            "filePath": "/考勤制度/异常考勤处理办法.pdf",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {},
    }


def test_delete_knowledge_item_route_maps_request_validation_to_documented_error(
    monkeypatch,
):
    """Delete-knowledge-item route should return the documented validation envelope."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeItems/delete",
        json={"knCode": "hr-policy"},
    )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"
    assert response.json()["resultMsg"] == "request validation failed"
    assert response.json()["resultObject"]["errors"]


def test_list_dir_route_returns_filesystem_entries(monkeypatch):
    """List-dir route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/listDir",
        json={
            "knCode": "integration-kb",
            "directoryPath": "/dir1",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {
            "data": [
                {
                    "knCode": "integration-kb",
                    "name": "/dir1/doc.md",
                    "type": "file",
                    "size": 100,
                }
            ]
        },
    }


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
        "/api/v1/listDir",
        json={
            "knCode": "integration-kb",
            "directoryPath": "/dir1",
        },
    )

    assert response.status_code == 200
    assert info_messages == [
        "list_dir request received: kb_code=integration-kb, directory_path=/dir1",
        "list_dir resolved service: service_class=FakeKBService",
        "list_dir service call succeeded: directory_path=/dir1, item_count=1",
        "list_dir response ready: code=200, item_count=1",
    ]


def test_list_dir_route_requires_kb_codes(monkeypatch):
    """List-dir route should reject requests that omit knCode."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/listDir",
        json={
            "directoryPath": "/",
        },
    )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"
    assert response.json()["resultMsg"] == "request validation failed"
    assert response.json()["resultObject"]["errors"]


def test_list_dir_route_maps_validation_error_to_standard_error(monkeypatch):
    """List-dir business validation should use the standardized error envelope."""

    class BrokenKBService(FakeKBService):
        async def list_dir(self, request):
            raise KnowledgeBaseValidationError("path contains invalid segments")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/listDir",
        json={
            "knCode": "integration-kb",
            "directoryPath": "../secret",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "path contains invalid segments",
        "resultObject": {},
    }


def test_list_dir_route_maps_missing_directory_to_documented_error(monkeypatch):
    """List-dir missing paths should map to the standardized not-found response."""

    class BrokenKBService(FakeKBService):
        async def list_dir(self, request):
            raise KnowledgeBaseValidationError(
                f"directory not found: {request.directory_path}"
            )

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/listDir",
        json={
            "knCode": "integration-kb",
            "directoryPath": "/missing-dir",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "directory not found: /missing-dir",
        "resultObject": {},
    }


def test_list_dir_route_maps_configuration_error_to_documented_error(monkeypatch):
    """List-dir configuration failures should use the standardized error envelope."""

    class BrokenKBService(FakeKBService):
        async def list_dir(self, request):
            raise KnowledgeBaseConfigurationError("KB runtime is not configured")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/listDir",
        json={
            "knCode": "integration-kb",
            "directoryPath": "/",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "KB runtime is not configured",
        "resultObject": {},
    }


def test_glob_route_returns_matching_entries(monkeypatch):
    """Glob route should delegate to the KB service."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/glob",
        json={
            "knCode": "integration-kb",
            "pathRule": "/dir1/*.md",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {
            "data": [
                {
                    "knCode": "integration-kb",
                    "name": "/dir1/doc.md",
                    "type": "file",
                    "size": 100,
                }
            ]
        },
    }


def test_glob_route_requires_kb_codes(monkeypatch):
    """Glob route should reject requests that omit knCode."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/glob",
        json={
            "pathRule": "/*.md",
        },
    )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"
    assert response.json()["resultMsg"] == "request validation failed"
    assert response.json()["resultObject"]["errors"]


def test_glob_route_maps_validation_error_to_standard_error(monkeypatch):
    """Glob business validation should use the standardized error envelope."""

    class BrokenKBService(FakeKBService):
        async def glob(self, request):
            raise KnowledgeBaseValidationError("path contains invalid segments")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/glob",
        json={
            "knCode": "integration-kb",
            "pathRule": "../secret",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "path contains invalid segments",
        "resultObject": {},
    }


def test_glob_route_maps_configuration_error_to_documented_error(monkeypatch):
    """Glob configuration failures should use the standardized error envelope."""

    class BrokenKBService(FakeKBService):
        async def glob(self, request):
            raise KnowledgeBaseConfigurationError("KB runtime is not configured")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/glob",
        json={
            "knCode": "integration-kb",
            "pathRule": "/*.md",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "KB runtime is not configured",
        "resultObject": {},
    }


def test_read_file_route_returns_requested_text(monkeypatch):
    """Read-file route should delegate to the KB service and return the documented envelope."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/readFile",
        json={
            "knCode": "hr-policy",
            "filePath": "/dir1/doc.md",
            "startLine": 1,
            "endLine": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resultCode"] == "0"
    assert body["resultMsg"] == "success"
    assert body["resultObject"] == {
        "knCode": "hr-policy",
        "filePath": "/dir1/doc.md",
        "startLine": 1,
        "endLine": 2,
        "data": "line1\nline2\n",
        "reachedEof": True,
    }


def test_read_file_route_returns_full_content_without_line_range(monkeypatch):
    """Read-file should return all content when startLine/endLine are omitted."""

    class FullReadKBService(FakeKBService):
        async def read_file(self, request):
            return {
                "knCode": request.kb_code,
                "filePath": request.file_path,
                "data": "full content\n",
                "reachedEof": True,
            }

    client = make_test_client(monkeypatch, FullReadKBService())
    response = client.post(
        "/api/v1/readFile",
        json={
            "knCode": "hr-policy",
            "filePath": "/dir1/doc.md",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resultCode"] == "0"
    assert body["resultObject"]["data"] == "full content\n"
    assert body["resultObject"]["reachedEof"] is True


def test_download_file_route_returns_binary_stream(monkeypatch):
    """Download-file should return raw file bytes with attachment headers."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/downloadFile",
        json={
            "knCode": "hr-policy",
            "filePath": "/dir1/doc.pdf",
        },
    )

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 test payload"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="doc.pdf"'


def test_download_file_route_supports_non_ascii_filename(monkeypatch):
    """Download-file should encode non-ASCII filenames safely in headers."""

    class UnicodeFilenameKBService(FakeKBService):
        async def download_file(self, request):
            return {
                "filename": "开源项目最佳实践汇报.md",
                "media_type": "text/markdown",
                "content": b"# hello\n",
            }

    client = make_test_client(monkeypatch, UnicodeFilenameKBService())
    response = client.post(
        "/api/v1/downloadFile",
        json={
            "knCode": "demo",
            "filePath": "/考勤制度/开源项目最佳实践汇报.md",
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


def test_download_file_route_maps_validation_error_to_documented_error(monkeypatch):
    """Download-file validation errors should use the documented JSON envelope."""

    class BrokenKBService(FakeKBService):
        async def download_file(self, request):
            raise KnowledgeBaseValidationError(f"file not found: {request.file_path}")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/downloadFile",
        json={"knCode": "hr-policy", "filePath": "/missing.pdf"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "file not found: /missing.pdf",
        "resultObject": {},
    }


# ---------------------------------------------------------------------------
# upload route tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# document update route tests
# ---------------------------------------------------------------------------


def test_document_update_route_returns_documented_success_shape(monkeypatch):
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeItems/update",
        data={
            "knCode": "hr-policy",
            "filePath": "//docs//readme.md",
            "fileDescription": "updated description",
            "processFrontMatter": "false",
        },
        files={"fileContent": ("README.md", b"# Updated\n", "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {
            "data": [
                {
                    "knCode": "hr-policy",
                    "filePath": "/docs/readme.md",
                    "success": True,
                    "error": None,
                }
            ]
        },
    }
    request = service.document_update_requests[0]
    assert request.file_path == "/docs/readme.md"
    assert request.file_description == "updated description"
    assert request.process_front_matter is False


def test_document_update_route_rejects_invalid_target_paths(monkeypatch):
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    for file_path in ("docs/readme.md", "/", "/docs/../readme.md", "/docs/./readme.md"):
        response = client.post(
            "/api/v1/knowledge-items/update",
            data={"knCode": "hr-policy", "filePath": file_path},
            files={"fileContent": ("readme.md", b"# Updated\n", "text/markdown")},
        )

        assert response.status_code == 200
        assert response.json()["resultCode"] == "-1"
        assert response.json()["resultMsg"] == "request validation failed"
        assert response.json()["resultObject"]

    assert service.document_update_requests == []


def test_document_update_route_rejects_zip_upload(monkeypatch):
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeItems/update",
        data={"knCode": "hr-policy", "filePath": "/docs/readme.md"},
        files={"fileContent": ("payload.zip", b"not a zip", "application/zip")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "zip uploads are not supported for document update",
        "resultObject": {},
    }
    assert service.document_update_requests == []


def test_document_update_route_rejects_uploaded_filename_suffix_mismatch(monkeypatch):
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeItems/update",
        data={"knCode": "hr-policy", "filePath": "/docs/readme.markdown"},
        files={"fileContent": ("readme.md", b"# Updated\n", "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "uploaded filename suffix must match filePath suffix",
        "resultObject": {},
    }
    assert service.document_update_requests == []


def test_document_update_route_rejects_empty_upload(monkeypatch):
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeItems/update",
        data={"knCode": "hr-policy", "filePath": "/docs/readme.md"},
        files={"fileContent": ("readme.md", b"", "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"
    assert response.json()["resultMsg"] == "request validation failed"
    assert response.json()["resultObject"]
    assert service.document_update_requests == []


def test_document_update_route_standardizes_service_validation_errors(monkeypatch):
    class InvalidUpdateService(FakeKBService):
        async def update_file(self, request):
            raise KnowledgeBaseValidationError("file not found: /docs/readme.md")

    service = InvalidUpdateService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeItems/update",
        data={"knCode": "hr-policy", "filePath": "/docs/readme.md"},
        files={"fileContent": ("readme.md", b"# Updated\n", "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "file not found: /docs/readme.md",
        "resultObject": {},
    }


def test_upload_file_route_passes_markdown_bytes_to_ingestion(monkeypatch):
    """Single-file upload should leave Markdown reference rewriting to ingestion."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/knowledgeItems/import",
        data={
            "knCode": "hr-policy",
            "filePath": "/docs/readme.md",
            "processFrontMatter": "true",
        },
        files={
            "fileContent": (
                "readme.md",
                b"![later](./later.png)\n",
                "text/markdown",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "0"
    assert len(service.import_calls) == 1
    assert service.import_calls[0].file_content == b"![later](./later.png)\n"


def test_upload_zip_route_includes_post_process_errors(monkeypatch):
    class FakeZipBatchImportService:
        def __init__(self, *, ingestion_service):
            self.ingestion_service = ingestion_service

        async def import_zip(self, **_kwargs):
            return ZipBatchImportResult(
                data=[
                    ImportItem(
                        file_path="/docs/readme.md",
                        success=True,
                        error=None,
                    )
                ],
                summary=ImportSummary(total=1, succeeded=1, failed=0),
                post_process_errors=["batch reference compensation failed: forced"],
            )

    monkeypatch.setattr(routes, "ZipBatchImportService", FakeZipBatchImportService)
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("readme.md", "# readme\n")

    response = client.post(
        "/api/v1/knowledgeItems/import",
        data={"knCode": "hr-policy", "filePath": "/docs"},
        files={"fileContent": ("docs.zip", payload.getvalue(), "application/zip")},
    )

    assert response.status_code == 200
    assert response.json()["resultObject"] == {
        "data": [
            {"filePath": "/docs/readme.md", "success": True, "error": None},
        ],
        "summary": {"total": 1, "succeeded": 1, "failed": 0},
        "postProcessErrors": ["batch reference compensation failed: forced"],
    }


# ---------------------------------------------------------------------------
# fileToMarkdown route tests
# ---------------------------------------------------------------------------


def test_file_to_markdown_route_returns_markdown_stream(monkeypatch):
    """POST /api/v1/fileToMarkdown should convert an uploaded file into an md download."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/fileToMarkdown",
        files={"fileContent": ("policy.txt", b"source text", "text/plain")},
    )

    assert response.status_code == 200
    assert response.content == b"# Converted Policy\n"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == 'attachment; filename="policy.md"'
    assert client.fake_document_chunking_service.extract_calls == [
        {"file_bytes": b"source text", "file_type": "txt"}
    ]


def test_file_to_markdown_route_maps_unsupported_type_to_documented_error(monkeypatch):
    """POST /api/v1/fileToMarkdown should reject unsupported upload file types."""

    class RejectingKBService(FakeKBService):
        async def convert_uploaded_file_to_markdown(
            self, *, file_bytes, filename, document_chunking_service
        ):
            raise KnowledgeBaseValidationError(
                "unsupported file type: exe. Supported types: csv, doc, docx, "
                "markdown, md, pdf, ppt, pptx, txt, xls, xlsx"
            )

    client = make_test_client(monkeypatch, RejectingKBService())

    response = client.post(
        "/api/v1/fileToMarkdown",
        files={
            "fileContent": (
                "installer.exe",
                b"not a document",
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resultCode"] == "-1"
    assert body["resultMsg"].startswith("unsupported file type: exe")
    assert body["resultObject"] == {}


def test_read_file_route_requires_kn_code(monkeypatch):
    """Read-file route should reject requests that omit knCode."""
    client = make_test_client(monkeypatch, FakeKBService())

    response = client.post(
        "/api/v1/readFile",
        json={
            "filePath": "/dir1/doc.md",
            "startLine": 1,
            "endLine": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resultCode"] == "-1"
    assert body["resultMsg"] == "request validation failed"


def test_read_file_route_maps_validation_error_to_documented_error(monkeypatch):
    """Read-file business validation should use the documented error envelope."""

    class BrokenKBService(FakeKBService):
        async def read_file(self, request):
            raise KnowledgeBaseValidationError("startLine must be greater than 0")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/readFile",
        json={
            "knCode": "hr-policy",
            "filePath": "/dir1/doc.md",
            "startLine": 0,
            "endLine": 2,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "startLine must be greater than 0",
        "resultObject": {},
    }


def test_read_file_route_maps_not_found_to_documented_error(monkeypatch):
    """Read-file missing files should use the documented error envelope."""

    class BrokenKBService(FakeKBService):
        async def read_file(self, request):
            raise KnowledgeBaseValidationError(f"file not found: {request.file_path}")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/readFile",
        json={
            "knCode": "hr-policy",
            "filePath": "/dir1/missing.pdf",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "file not found: /dir1/missing.pdf",
        "resultObject": {},
    }


def test_read_file_route_maps_file_not_built_to_documented_error(monkeypatch):
    """Read-file should return an error when the file has not been built."""

    class NotBuiltKBService(FakeKBService):
        async def read_file(self, request):
            raise KnowledgeBaseValidationError(f"file not built: {request.file_path}")

    client = make_test_client(monkeypatch, NotBuiltKBService())
    response = client.post(
        "/api/v1/readFile",
        json={
            "knCode": "hr-policy",
            "filePath": "/dir1/doc.pdf",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "file not built: /dir1/doc.pdf",
        "resultObject": {},
    }


def test_read_file_route_maps_configuration_error_to_documented_error(monkeypatch):
    """Read-file configuration failures should use the documented error envelope."""

    class BrokenKBService(FakeKBService):
        async def read_file(self, request):
            raise KnowledgeBaseConfigurationError("read file runtime is not configured")

    client = make_test_client(monkeypatch, BrokenKBService())
    response = client.post(
        "/api/v1/readFile",
        json={
            "knCode": "hr-policy",
            "filePath": "/dir1/doc.md",
            "startLine": 1,
            "endLine": 2,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "read file runtime is not configured",
        "resultObject": {},
    }


def test_create_knowledge_base_route_maps_configuration_error_to_documented_error(
    monkeypatch,
):
    """Missing KB runtime configuration should surface as a documented error response."""

    class MisconfiguredKBService(FakeKBService):
        async def create_knowledge_base(self, request):
            raise KnowledgeBaseConfigurationError("DB_HOST/DB_USER/DB_PASS is required")

    client = make_test_client(monkeypatch, MisconfiguredKBService())
    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={
            "knName": "人力制度知识库",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "DB_HOST/DB_USER/DB_PASS is required",
        "resultObject": {},
    }


def test_create_knowledge_base_route_maps_unexpected_exception_to_standard_error(
    monkeypatch,
):
    """Unexpected route failures should still use the standard KB error envelope."""

    class BrokenKBService(FakeKBService):
        async def create_knowledge_base(self, request):
            raise RuntimeError("duplicate key value violates unique constraint")

    async def get_broken():
        return BrokenKBService()

    monkeypatch.setattr("by_qa.main._get_or_build_knowledge_base_service", get_broken)
    monkeypatch.setattr(
        "by_qa.main._get_or_build_knowledge_item_ingestion_service", get_broken
    )
    monkeypatch.setattr(
        "by_qa.main._get_or_build_knowledge_item_search_service", get_broken
    )
    monkeypatch.setattr("by_qa.main.get_adapter", lambda: object())
    monkeypatch.setattr("by_qa.main.get_instant_search_engine", lambda: object())
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/knowledgeBases/create",
        json={"knName": "Demo KB"},
    )

    assert response.status_code == 200
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
        "/api/v1/knowledgeItems/search",
        json={
            "query": "员工请假制度怎么规定",
            "knCodeList": ["hr-policy"],
            "topK": 10,
            "searchMode": "mixedRecall",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resultCode"] == "0"
    assert body["resultMsg"] == "success"
    hit = body["resultObject"]["data"][0]
    assert hit["knCode"] == "hr-policy"
    assert hit["filePath"] == "/employee-handbook.md"
    assert hit["chunkNo"] == 1
    assert hit["chunkId"] == 42
    assert hit["score"] == 0.91
    assert hit["imagePath"] == ""
    assert hit["startLine"] == 1
    assert hit["endLine"] == 3


def test_search_file_route_serializes_decimal_metadata_values(monkeypatch):
    """searchFile should return JSON-safe numeric metadata values."""

    class SearchFileService(FakeKBService):
        async def search_file_with_dsl(self, request):
            return [
                SearchFileHit(
                    kb_code="hr-policy",
                    file_path="/employee-handbook.md",
                    score=0.91,
                    metadata={
                        "amount": {
                            "valueType": "number",
                            "value": Decimal("12.5"),
                        }
                    },
                )
            ]

    client = make_test_client(monkeypatch, SearchFileService())

    response = client.post(
        "/api/v1/knowledgeItems/searchFile",
        json={
            "query": "员工补贴",
            "knCodeList": ["hr-policy"],
            "topK": 10,
            "searchMode": "mixedRecall",
            "metadataFieldList": ["amount"],
        },
    )

    assert response.status_code == 200
    assert (
        response.json()["resultObject"]["data"][0]["metadata"]["amount"]["value"]
        == 12.5
    )


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
        "/api/v1/knowledgeItems/search",
        json={
            "query": "员工请假制度怎么规定",
            "knCodeList": ["hr-policy"],
            "topK": 10,
            "searchMode": "mixedRecall",
        },
    )

    assert response.status_code == 200
    assert info_messages == [
        "search_knowledge_items request received: query=员工请假制度怎么规定, kb_code_count=1, top_k=10, search_mode=mixedRecall, where=None",
        "search_knowledge_items service call succeeded: returned_count=1, top_k=10",
        "search_knowledge_items response ready: code=200, returned_count=1",
    ]


def test_search_route_maps_validation_error_to_documented_error(monkeypatch):
    """Search business validation should use the documented error envelope."""

    class BrokenSearchService(FakeKBService):
        async def search(self, request):
            raise KnowledgeBaseValidationError("knCodeList must not be empty")

    client = make_test_client(monkeypatch, BrokenSearchService())
    response = client.post(
        "/api/v1/knowledgeItems/search",
        json={
            "query": "员工请假制度怎么规定",
            "knCodeList": ["hr-policy"],
            "topK": 5,
            "searchMode": "mixedRecall",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "knCodeList must not be empty",
        "resultObject": {},
    }


def test_search_route_maps_configuration_error_to_documented_error(monkeypatch):
    """Search configuration failures should use the documented error envelope."""

    class BrokenSearchService(FakeKBService):
        async def search(self, request):
            raise KnowledgeBaseConfigurationError(
                "EMBEDDING_BASE_URL is required for retrieval"
            )

    client = make_test_client(monkeypatch, BrokenSearchService())
    response = client.post(
        "/api/v1/knowledgeItems/search",
        json={
            "query": "员工请假制度怎么规定",
            "knCodeList": ["hr-policy"],
            "topK": 5,
            "searchMode": "mixedRecall",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "EMBEDDING_BASE_URL is required for retrieval",
        "resultObject": {},
    }


def test_search_route_rejects_invalid_search_mode(monkeypatch):
    """Search should reject requests with invalid searchMode."""
    client = make_test_client(monkeypatch, FakeKBService())
    response = client.post(
        "/api/v1/knowledgeItems/search",
        json={
            "query": "test",
            "knCodeList": ["kb1"],
            "topK": 5,
            "searchMode": "invalidMode",
        },
    )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"
    assert response.json()["resultMsg"] == "request validation failed"


def test_search_route_rejects_non_positive_top_k(monkeypatch):
    """Search should reject requests with topK <= 0."""
    client = make_test_client(monkeypatch, FakeKBService())
    response = client.post(
        "/api/v1/knowledgeItems/search",
        json={
            "query": "test",
            "knCodeList": ["kb1"],
            "topK": 0,
            "searchMode": "mixedRecall",
        },
    )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"
    assert response.json()["resultMsg"] == "request validation failed"


# ---------------------------------------------------------------------------
# fileToMarkdownIndex route tests
# ---------------------------------------------------------------------------


def test_file_to_markdown_index_success(monkeypatch):
    """POST /api/v1/fileToMarkdownIndex returns success and schedules background work."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)
    response = client.post(
        "/api/v1/fileToMarkdownIndex",
        json={"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resultCode"] == "0"
    assert body["resultMsg"] == "success"
    assert len(service.file_build_task_requests) == 1
    assert len(service.file_build_task_runs) == 1
    assert service.file_build_task_runs[0][1] == 9901


def test_file_to_markdown_index_kb_not_found(monkeypatch):
    """POST /api/v1/fileToMarkdownIndex returns error when KB not found."""

    class FailingService(FakeKBService):
        async def create_file_to_markdown_index_task(self, request):
            raise KnowledgeBaseValidationError(
                f"knowledge base not found: {request.kb_code}"
            )

    client = make_test_client(monkeypatch, FailingService())
    response = client.post(
        "/api/v1/fileToMarkdownIndex",
        json={"knCode": "999", "filePath": "/doc.pdf"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resultCode"] == "-1"
    assert "knowledge base not found" in body["resultMsg"]


def test_file_to_markdown_index_validation_error(monkeypatch):
    """POST /api/v1/fileToMarkdownIndex returns error on bad input."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)
    response = client.post(
        "/api/v1/fileToMarkdownIndex",
        json={"knCode": "", "filePath": "/doc.pdf"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resultCode"] == "-1"
    assert body["resultMsg"] == "request validation failed"


def test_file_to_markdown_index_running_task_returns_error(monkeypatch):
    """POST /api/v1/fileToMarkdownIndex returns an error when a running task exists."""

    class FailingService(FakeKBService):
        async def create_file_to_markdown_index_task(self, request):
            raise KnowledgeBaseValidationError(
                f"build task already exists for file: {request.file_path}"
            )

    client = make_test_client(monkeypatch, FailingService())
    response = client.post(
        "/api/v1/fileToMarkdownIndex",
        json={"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resultCode"] == "-1"
    assert (
        body["resultMsg"]
        == "build task already exists for file: /制度/人事/请假制度.pdf"
    )


def test_file_build_status_success(monkeypatch):
    """POST /api/v1/fileBuildStatus returns the latest build status envelope."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/fileBuildStatus",
        json={"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {
            "status": "running",
            "currentStep": "chunking",
            "statusDict": [
                {
                    "standCode": "complete",
                    "standDisplayValue": "已完成",
                    "standDisplayValueEn": "complete",
                },
                {
                    "standCode": "failed",
                    "standDisplayValue": "失败",
                    "standDisplayValueEn": "failed",
                },
                {
                    "standCode": "running",
                    "standDisplayValue": "构建中",
                    "standDisplayValueEn": "running",
                },
            ],
            "stepDict": [
                {
                    "standCode": "markdown",
                    "standDisplayValue": "原始文件转 Markdown",
                    "standDisplayValueEn": "markdown",
                },
                {
                    "standCode": "chunking",
                    "standDisplayValue": "文档切片",
                    "standDisplayValueEn": "chunking",
                },
                {
                    "standCode": "vectorizing",
                    "standDisplayValue": "切片向量化",
                    "standDisplayValueEn": "vectorizing",
                },
                {
                    "standCode": "complete",
                    "standDisplayValue": "已完成",
                    "standDisplayValueEn": "complete",
                },
            ],
        },
    }


def test_file_build_status_validation_error(monkeypatch):
    """POST /api/v1/fileBuildStatus returns request validation failures."""
    service = FakeKBService()
    client = make_test_client(monkeypatch, service)

    response = client.post(
        "/api/v1/fileBuildStatus",
        json={"knCode": "", "filePath": "/制度/人事/请假制度.pdf"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resultCode"] == "-1"
    assert body["resultMsg"] == "request validation failed"
