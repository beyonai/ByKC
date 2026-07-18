"""Tests for Markdown reference relationship queries."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from by_qa.knowledge_base.api.routes import register_routes
from by_qa.knowledge_base.api.schemas import (
    KnowledgeItemReferenceQueryRequest,
    KnowledgeItemReferenceQueryResponse,
    KnowledgeItemReferenceSource,
)
from by_qa.knowledge_base.repositories.knowledge_file_reference_repository import (
    KnowledgeFileReferenceRepository,
)
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = object()
        self.closed = False

    def cursor(self) -> object:
        return self.cursor_obj

    async def close(self) -> None:
        self.closed = True


async def _async_return(value: Any) -> Any:
    return value


class FakeKnowledgeBaseRepository:
    async def get_by_code(self, cursor: Any, kb_code: str) -> dict[str, Any]:
        del cursor, kb_code
        return {"kid": 7}


class FakeFsEntryRepository:
    def __init__(self, target_row: dict[str, Any] | None) -> None:
        self.target_row = target_row
        self.calls: list[dict[str, Any]] = []

    async def get_file_by_path(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ) -> dict[str, Any] | None:
        del cursor
        self.calls.append(
            {"knowledge_base_id": knowledge_base_id, "full_path": full_path}
        )
        return self.target_row


class FakeReferenceRepository:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def list_sources_by_target(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_fs_entry_id: int | None = None,
        target_path: str | None = None,
    ) -> list[dict[str, Any]]:
        del cursor
        self.calls.append(
            {
                "knowledge_base_id": knowledge_base_id,
                "target_fs_entry_id": target_fs_entry_id,
                "target_path": target_path,
            }
        )
        return self.rows

    async def list_by_source(
        self,
        cursor: Any,
        *,
        source_fs_entry_id: int,
    ) -> list[dict[str, Any]]:
        del cursor
        self.calls.append({"source_fs_entry_id": source_fs_entry_id})
        return self.rows


class FakeSqlCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self.executed.append((sql, params))

    async def fetchall(self) -> list[dict[str, Any]]:
        return []


def _service(
    *,
    target_row: dict[str, Any] | None,
    reference_rows: list[dict[str, Any]],
) -> tuple[
    KnowledgeBaseService,
    FakeConnection,
    FakeFsEntryRepository,
    FakeReferenceRepository,
]:
    connection = FakeConnection()
    fs_repository = FakeFsEntryRepository(target_row)
    reference_repository = FakeReferenceRepository(reference_rows)
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(),
        knowledge_fs_entry_repository=fs_repository,
        knowledge_file_reference_repository=reference_repository,
    )
    return service, connection, fs_repository, reference_repository


@pytest.mark.asyncio
async def test_reference_query_inbound_uses_live_target_fs_entry_id():
    service, connection, fs_repository, reference_repository = _service(
        target_row={"kid": 99, "virtual_path": "/docs/target.md"},
        reference_rows=[
            {
                "source_virtual_path": "/docs/source.md",
                "source_is_deleted": False,
                "original_target": "../target.md",
                "target_suffix": "#section",
                "target_path": None,
                "status": "resolved",
            }
        ],
    )

    response = await service.list_inbound_references(
        KnowledgeItemReferenceQueryRequest(
            kb_code="kb-1",
            file_path="/docs/target.md",
            direction="inbound",
        )
    )

    assert response.inbound == [
        KnowledgeItemReferenceSource(
            source_path="/docs/source.md",
            original_target="../target.md",
            target_suffix="#section",
            target_path="/docs/target.md",
            status="resolved",
        )
    ]
    assert response.outbound == []
    assert fs_repository.calls == [
        {"knowledge_base_id": 7, "full_path": "docs/target.md"}
    ]
    assert reference_repository.calls == [
        {"knowledge_base_id": 7, "target_fs_entry_id": 99, "target_path": None}
    ]
    assert connection.closed is True


@pytest.mark.asyncio
async def test_reference_query_inbound_uses_target_path_for_unresolved_and_broken_rows():
    service_parts = _service(
        target_row=None,
        reference_rows=[
            {
                "source_virtual_path": "/docs/source.md",
                "source_is_deleted": False,
                "original_target": "../deleted.md",
                "target_suffix": "",
                "target_path": "/docs/deleted.md",
                "status": "broken",
            }
        ],
    )
    service = service_parts[0]
    reference_repository = service_parts[3]

    response = await service.list_inbound_references(
        KnowledgeItemReferenceQueryRequest(
            kb_code="kb-1",
            file_path="/docs/deleted.md",
            direction="inbound",
        )
    )

    assert response.inbound[0].source_path == "/docs/source.md"
    assert response.inbound[0].target_path == "/docs/deleted.md"
    assert response.inbound[0].status == "broken"
    assert response.outbound == []
    assert reference_repository.calls == [
        {
            "knowledge_base_id": 7,
            "target_fs_entry_id": None,
            "target_path": "/docs/deleted.md",
        }
    ]


@pytest.mark.asyncio
async def test_reference_query_inbound_excludes_deleted_source_files():
    service = _service(
        target_row={"kid": 99, "virtual_path": "/docs/target.md"},
        reference_rows=[
            {
                "source_virtual_path": "/docs/live.md",
                "source_is_deleted": False,
                "original_target": "./target.md",
                "target_suffix": "",
                "target_path": None,
                "status": "resolved",
            },
            {
                "source_virtual_path": "/docs/deleted.md",
                "source_is_deleted": True,
                "original_target": "./target.md",
                "target_suffix": "",
                "target_path": None,
                "status": "resolved",
            },
        ],
    )[0]

    response = await service.list_inbound_references(
        KnowledgeItemReferenceQueryRequest(
            kb_code="kb-1",
            file_path="/docs/target.md",
            direction="inbound",
        )
    )

    assert [item.source_path for item in response.inbound] == ["/docs/live.md"]


@pytest.mark.asyncio
async def test_reference_query_outbound_uses_source_fs_entry_id():
    service, connection, fs_repository, reference_repository = _service(
        target_row={"kid": 88, "virtual_path": "/docs/source.md"},
        reference_rows=[
            {
                "target_virtual_path": "/docs/target.md",
                "target_is_deleted": False,
                "original_target": "./target.md",
                "target_suffix": "#section",
                "target_path": None,
                "status": "resolved",
            },
            {
                "target_virtual_path": None,
                "target_is_deleted": None,
                "original_target": "./missing.md",
                "target_suffix": "",
                "target_path": "/docs/missing.md",
                "status": "unresolved",
            },
        ],
    )

    response = await service.list_inbound_references(
        KnowledgeItemReferenceQueryRequest(
            kb_code="kb-1",
            file_path="/docs/source.md",
            direction="outbound",
        )
    )

    assert response.inbound == []
    assert response.outbound == [
        KnowledgeItemReferenceSource(
            source_path="/docs/source.md",
            original_target="./target.md",
            target_suffix="#section",
            target_path="/docs/target.md",
            status="resolved",
        ),
        KnowledgeItemReferenceSource(
            source_path="/docs/source.md",
            original_target="./missing.md",
            target_suffix="",
            target_path="/docs/missing.md",
            status="unresolved",
        ),
    ]
    assert fs_repository.calls == [
        {"knowledge_base_id": 7, "full_path": "docs/source.md"}
    ]
    assert reference_repository.calls == [{"source_fs_entry_id": 88}]
    assert connection.closed is True


@pytest.mark.asyncio
async def test_reference_query_all_returns_inbound_and_outbound():
    service, _, _, reference_repository = _service(
        target_row={"kid": 99, "virtual_path": "/docs/file.md"},
        reference_rows=[
            {
                "source_virtual_path": "/docs/source.md",
                "source_is_deleted": False,
                "target_virtual_path": "/docs/target.md",
                "target_is_deleted": False,
                "original_target": "./file.md",
                "target_suffix": "",
                "target_path": None,
                "status": "resolved",
            }
        ],
    )

    response = await service.list_inbound_references(
        KnowledgeItemReferenceQueryRequest(
            kb_code="kb-1",
            file_path="/docs/file.md",
            direction="all",
        )
    )

    assert len(response.inbound) == 1
    assert len(response.outbound) == 1
    assert reference_repository.calls == [
        {"knowledge_base_id": 7, "target_fs_entry_id": 99, "target_path": None},
        {"source_fs_entry_id": 99},
    ]


def test_register_routes_is_backward_compatible_without_update_factory():
    app = FastAPI()

    async def get_unused_service():
        raise AssertionError("unused dependency should not be resolved")

    register_routes(
        app,
        get_knowledge_base_service=get_unused_service,
        get_knowledge_item_ingestion_service=get_unused_service,
        get_knowledge_item_search_service=get_unused_service,
        get_document_chunking_service=get_unused_service,
        get_metadata_search_service=get_unused_service,
        get_file_metadata_query_service=get_unused_service,
    )

    assert any(
        route.path == "/api/v1/knowledgeItems/references" for route in app.routes
    )


def test_references_route_returns_standard_success_envelope():
    class FakeRouteService:
        def __init__(self) -> None:
            self.requests: list[KnowledgeItemReferenceQueryRequest] = []

        async def list_inbound_references(self, request):
            self.requests.append(request)
            return KnowledgeItemReferenceQueryResponse(
                inbound=[
                    KnowledgeItemReferenceSource(
                        source_path="/docs/source.md",
                        original_target="../target.md",
                        target_suffix="#section",
                        target_path="/docs/target.md",
                        status="resolved",
                    )
                ],
                outbound=[],
            )

    service = FakeRouteService()
    app = FastAPI()

    async def get_service():
        return service

    async def get_unused_service():
        raise AssertionError("unused dependency should not be resolved")

    register_routes(
        app,
        get_knowledge_base_service=get_service,
        get_knowledge_item_ingestion_service=get_unused_service,
        get_knowledge_item_search_service=get_unused_service,
        get_document_chunking_service=get_unused_service,
        get_metadata_search_service=get_unused_service,
        get_file_metadata_query_service=get_unused_service,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/knowledgeItems/references",
        json={
            "knCode": "kb-1",
            "filePath": "/docs/target.md",
            "direction": "inbound",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {
            "inbound": [
                {
                    "sourcePath": "/docs/source.md",
                    "originalTarget": "../target.md",
                    "targetSuffix": "#section",
                    "targetPath": "/docs/target.md",
                    "status": "resolved",
                }
            ],
            "outbound": [],
        },
    }
    assert service.requests[0].kb_code == "kb-1"
    assert service.requests[0].file_path == "/docs/target.md"
    assert service.requests[0].direction == "inbound"


def test_references_route_rejects_unsupported_direction():
    app = FastAPI()

    async def get_service():
        raise AssertionError("service should not be resolved")

    register_routes(
        app,
        get_knowledge_base_service=get_service,
        get_knowledge_item_ingestion_service=get_service,
        get_knowledge_item_search_service=get_service,
        get_document_chunking_service=get_service,
        get_metadata_search_service=get_service,
        get_file_metadata_query_service=get_service,
    )

    response = TestClient(app).post(
        "/api/v1/knowledgeItems/references",
        json={"knCode": "kb-1", "filePath": "/docs/file.md", "direction": "sideways"},
    )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"


def test_references_route_rejects_legacy_target_path_without_file_path():
    app = FastAPI()

    async def get_service():
        raise AssertionError("service should not be resolved")

    register_routes(
        app,
        get_knowledge_base_service=get_service,
        get_knowledge_item_ingestion_service=get_service,
        get_knowledge_item_search_service=get_service,
        get_document_chunking_service=get_service,
        get_metadata_search_service=get_service,
        get_file_metadata_query_service=get_service,
    )

    response = TestClient(app).post(
        "/api/v1/knowledgeItems/references",
        json={"knCode": "kb-1", "targetPath": "/docs/file.md"},
    )

    assert response.status_code == 200
    assert response.json()["resultCode"] == "-1"


def test_kebab_references_route_alias_is_supported():
    class FakeRouteService:
        async def list_inbound_references(self, request):
            del request
            return KnowledgeItemReferenceQueryResponse(inbound=[], outbound=[])

    app = FastAPI()

    async def get_service():
        return FakeRouteService()

    async def get_unused_service():
        raise AssertionError("unused dependency should not be resolved")

    register_routes(
        app,
        get_knowledge_base_service=get_service,
        get_knowledge_item_ingestion_service=get_unused_service,
        get_knowledge_item_search_service=get_unused_service,
        get_document_chunking_service=get_unused_service,
        get_metadata_search_service=get_unused_service,
        get_file_metadata_query_service=get_unused_service,
    )

    response = TestClient(app).post(
        "/api/v1/knowledge-items/references",
        json={"knCode": "kb-1", "filePath": "/docs/deleted.md"},
    )

    assert response.status_code == 200
    assert response.json()["resultObject"] == {"inbound": [], "outbound": []}


@pytest.mark.asyncio
async def test_repository_default_source_query_excludes_deleted_source_files():
    repo = KnowledgeFileReferenceRepository()
    cursor = FakeSqlCursor()

    await repo.list_sources_by_target(
        cursor,
        knowledge_base_id=7,
        target_fs_entry_id=99,
    )

    sql, params = cursor.executed[0]
    normalized_sql = " ".join(sql.split())
    assert "JOIN knowledge_fs_entry source" in normalized_sql
    assert "source.is_deleted = FALSE" in normalized_sql
    assert params == {"knowledge_base_id": 7, "target_fs_entry_id": 99}


@pytest.mark.asyncio
async def test_repository_path_query_includes_unresolved_and_broken_sources():
    repo = KnowledgeFileReferenceRepository()
    cursor = FakeSqlCursor()

    await repo.list_sources_by_target(
        cursor,
        knowledge_base_id=7,
        target_path="/docs/missing.md",
    )

    sql, params = cursor.executed[0]
    normalized_sql = " ".join(sql.split())
    assert "kfr.status IN ('unresolved', 'broken')" in normalized_sql
    assert params == {"knowledge_base_id": 7, "target_path": "/docs/missing.md"}
