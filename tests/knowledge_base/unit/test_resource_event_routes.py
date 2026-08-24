"""Route-level coverage for best-effort resource mutation events."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from by_qa.knowledge_base.api.routes import register_routes
from by_qa.knowledge_base.api.schemas import (
    MoveKnowledgeItemResult,
    MoveKnowledgeItemsResponse,
    MoveKnowledgeItemsSummary,
)
from by_qa.knowledge_base.events import (
    DirectoryCreatedEvent,
    DirectoryDeletedEvent,
    DirectoryUpdatedEvent,
    FileDeletedEvent,
    FileImportedEvent,
    FileUpdatedEvent,
    KnowledgeEventPublisherInvoker,
    ResourceMovedEvent,
)
from by_qa.knowledge_base.services.document_update_service import DocumentUpdateResult


class RecordingPublisher:
    def __init__(self, *, fail: bool = False):
        self.events = []
        self.fail = fail

    async def publish(self, event):
        self.events.append(event)
        if self.fail:
            raise RuntimeError("forced publisher failure")


class MutationService:
    async def create_directory(self, request):
        return None

    async def update_directory(self, request):
        return None

    async def delete_directory(self, request):
        return None

    async def upload_file(self, request):
        return {"fs_entry_id": 71}

    async def update_file(self, request):
        return DocumentUpdateResult(
            timeline_id=91,
            is_markdown=False,
            file_path=request.file_path,
        )

    async def delete_knowledge_item(self, request):
        return None

    async def move_knowledge_items(self, request):
        return MoveKnowledgeItemsResponse(
            data=[
                MoveKnowledgeItemResult(
                    source_path=request.source_path[0],
                    target_path=f"{request.target_directory_path}/a.md",
                    success=True,
                )
            ],
            summary=MoveKnowledgeItemsSummary(total=1, succeeded=1, failed=0),
        )


def _client(publisher):
    app = FastAPI()
    service = MutationService()

    async def get_service():
        return service

    async def get_invoker():
        return KnowledgeEventPublisherInvoker(publisher=publisher, timeout_seconds=1)

    register_routes(
        app,
        get_knowledge_base_service=get_service,
        get_knowledge_item_ingestion_service=get_service,
        get_knowledge_item_search_service=get_service,
        get_document_update_service=get_service,
        get_document_chunking_service=get_service,
        get_metadata_search_service=get_service,
        get_file_metadata_query_service=get_service,
        get_knowledge_event_publisher_invoker=get_invoker,
    )
    return TestClient(app)


def test_sync_mutation_routes_publish_one_unified_event_after_success():
    publisher = RecordingPublisher()
    client = _client(publisher)

    responses = [
        client.post(
            "/api/v1/directories/create",
            json={
                "knCode": "kb-1",
                "directoryPath": "/docs",
            },
        ),
        client.post(
            "/api/v1/directories/update",
            json={
                "knCode": "kb-1",
                "directoryPath": "/docs",
                "directoryName": "renamed",
            },
        ),
        client.post(
            "/api/v1/knowledgeItems/import",
            data={
                "knCode": "kb-1",
                "filePath": "/renamed/a.md",
            },
            files={"fileContent": ("a.md", b"# a\n", "text/markdown")},
        ),
        client.post(
            "/api/v1/knowledgeItems/update",
            data={"knCode": "kb-1", "filePath": "/renamed/a.md"},
            files={"fileContent": ("a.md", b"# b\n", "text/markdown")},
        ),
        client.post(
            "/api/v1/knowledgeItems/move",
            json={
                "knCode": "kb-1",
                "sourcePath": ["/renamed/a.md"],
                "targetDirectoryPath": "/archive",
            },
        ),
        client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": "kb-1", "filePath": "/archive/a.md"},
        ),
        client.post(
            "/api/v1/directories/delete",
            json={"knCode": "kb-1", "directoryPath": "/renamed"},
        ),
    ]

    assert all(response.json()["resultCode"] == "0" for response in responses)
    assert [event.event_type for event in publisher.events] == [
        "resource.directory.created",
        "resource.directory.updated",
        "resource.file.imported",
        "resource.file.updated",
        "resource.moved",
        "resource.file.deleted",
        "resource.directory.deleted",
    ]
    assert [type(event) for event in publisher.events] == [
        DirectoryCreatedEvent,
        DirectoryUpdatedEvent,
        FileImportedEvent,
        FileUpdatedEvent,
        ResourceMovedEvent,
        FileDeletedEvent,
        DirectoryDeletedEvent,
    ]
    assert "resourceId" not in publisher.events[2].payload.model_dump(by_alias=True)


def test_unknown_multipart_extra_params_is_not_part_of_event_contract():
    publisher = RecordingPublisher()
    response = _client(publisher).post(
        "/api/v1/knowledgeItems/import",
        data={
            "knCode": "kb-1",
            "filePath": "/a.md",
            "extraParams": "[]",
        },
        files={"fileContent": ("a.md", b"# a\n", "text/markdown")},
    )

    assert response.json()["resultCode"] == "0"
    assert len(publisher.events) == 1
    assert "extraParams" not in publisher.events[0].model_dump(by_alias=True)


def test_publisher_failure_does_not_change_success_response():
    publisher = RecordingPublisher(fail=True)
    response = _client(publisher).post(
        "/api/v1/directories/create",
        json={"knCode": "kb-1", "directoryPath": "/docs"},
    )

    assert response.json()["resultCode"] == "0"
    assert len(publisher.events) == 1
