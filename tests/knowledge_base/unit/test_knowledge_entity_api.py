"""Contract and route-adapter tests for KnowledgeEntity APIs."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from by_qa.knowledge_base.api import routes
from by_qa.knowledge_base.api.knowledge_entity_schemas import (
    EntityDiscoveryRequest,
    EntityEnrichRequest,
    ProcessingBatchStatusRequest,
    ProcessingTaskStatusRequest,
    SemanticRelationsRequest,
)


class FakeKnowledgeEntityService:
    def __init__(self):
        self.calls: list[tuple[str, object, object | None]] = []

    async def evaluate_processing_eligibility(self, request):
        self.calls.append(("eligibility", request, None))
        return {
            "fileId": "1024",
            "knCode": request.kb_code,
            "filePath": request.file_path,
            "documentKind": "original",
            "capability": request.capability,
            "eligibility": "ELIGIBLE_AND_STALE",
            "reasonCode": "NEVER_PROCESSED",
        }

    async def discover_knowledge_entities(self, request):
        self.calls.append(("discovery", request, None))
        scope = (
            "SINGLE_FILE"
            if request.file_path
            else ("DIRECTORY" if request.directory_path else "WHOLE_KB")
        )
        return {
            "batchId": "ed-1",
            "scope": scope,
            "targetPath": request.file_path or request.directory_path,
            "taskType": "ENTITY_DISCOVERY",
            "eligibleCount": 2,
            "acceptedCount": 2,
            "reusedCount": 0,
            "skippedCount": 0,
            "tasks": [],
        }

    async def enrich_knowledge_entities(self, request):
        self.calls.append(("enrich", request, None))
        return {
            "batchId": "ee-1",
            "scope": "WHOLE_KB" if request.file_path is None else "SINGLE_FILE",
            "taskType": "DOCUMENT_ENRICH",
            "eligibleCount": 1,
            "acceptedCount": 1,
            "reusedCount": 0,
            "skippedCount": 0,
            "tasks": [],
        }

    async def get_processing_task_status(self, request):
        self.calls.append(("status", request, None))
        return {
            "knowledgeBaseId": "11",
            "knCode": request.kb_code,
            "total": 0,
            "pageNum": request.page_num,
            "pageSize": request.page_size,
            "data": [],
        }

    async def get_processing_batch_status(self, request):
        self.calls.append(("batch_status", request, None))
        return {
            "batchId": request.batch_id,
            "knowledgeBaseId": "11",
            "knCode": request.kb_code,
            "taskType": "ENTITY_DISCOVERY",
            "scope": "WHOLE_KB",
            "status": "PROCESSING",
            "version": 1,
            "totalCount": 2,
            "completedCount": 1,
            "pendingCount": 1,
            "runningCount": 0,
            "succeededCount": 1,
            "failedCount": 0,
            "skippedCount": 0,
            "progress": 50,
            "createdAt": "2026-08-18T00:00:00Z",
            "completedAt": None,
            "pageNum": request.page_num,
            "pageSize": request.page_size,
            "data": [],
        }

    async def get_semantic_relations(self, request):
        self.calls.append(("relations", request, None))
        return {
            "fileId": "2048",
            "total": 0,
            "pageNum": request.page_num,
            "pageSize": request.page_size,
            "data": [],
        }

    async def delete_knowledge_entity(self, request):
        self.calls.append(("delete_entity", request, None))
        return {
            "deletedEntityCount": 1,
            "deletedAliasCount": 2,
            "deletedFileCount": 1,
        }

    async def delete_knowledge_entity_alias(self, request):
        self.calls.append(("delete_alias", request, None))
        return {
            "deletedEntityCount": 0,
            "deletedAliasCount": 1,
            "deletedFileCount": 0,
        }


def make_client(service: FakeKnowledgeEntityService | None) -> TestClient:
    app = FastAPI()

    async def get_unrelated_service():
        return object()

    async def get_entity_service():
        return service

    routes.register_routes(
        app,
        get_knowledge_base_service=get_unrelated_service,
        get_knowledge_item_ingestion_service=get_unrelated_service,
        get_knowledge_item_search_service=get_unrelated_service,
        get_document_chunking_service=get_unrelated_service,
        get_metadata_search_service=get_unrelated_service,
        get_file_metadata_query_service=get_unrelated_service,
        **(
            {"get_knowledge_entity_processing_service": get_entity_service}
            if service is not None
            else {}
        ),
    )
    return TestClient(app)


def test_discovery_and_enrich_requests_support_whole_kb_scope():
    discovery = EntityDiscoveryRequest.model_validate({"knCode": "1"})
    enrich = EntityEnrichRequest.model_validate({"knCode": "1"})

    assert discovery.file_path is None
    assert discovery.max_entities == 12
    assert enrich.file_path is None
    assert enrich.top_k == 20


def test_discovery_accepts_normalized_directory_but_enrich_rejects_it():
    discovery = EntityDiscoveryRequest.model_validate(
        {"knCode": "1", "directoryPath": "//Policies///2026/"}
    )

    assert discovery.file_path is None
    assert discovery.directory_path == "/Policies/2026"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EntityEnrichRequest.model_validate(
            {"knCode": "1", "directoryPath": "/KnowledgeEntity"}
        )


def test_discovery_directory_path_validation_allows_root_and_rejects_escape():
    assert (
        EntityDiscoveryRequest.model_validate(
            {"knCode": "1", "directoryPath": "/"}
        ).directory_path
        == "/"
    )
    with pytest.raises(ValidationError, match="must not contain"):
        EntityDiscoveryRequest.model_validate(
            {"knCode": "1", "directoryPath": "/docs/../secret"}
        )


@pytest.mark.parametrize("field_name", ["extraParams", "extra_params"])
def test_discovery_and_enrich_accept_deprecated_extra_params_unchanged(field_name):
    value = {"requestId": "req-1", "nested": {"values": [1, "two", None]}}

    discovery = EntityDiscoveryRequest.model_validate(
        {"knCode": "1", field_name: value}
    )
    enrich = EntityEnrichRequest.model_validate({"knCode": "1", field_name: value})

    assert discovery.model_dump()["extra_params"] == value
    assert enrich.model_dump()["extra_params"] == value


@pytest.mark.parametrize("request_type", [EntityDiscoveryRequest, EntityEnrichRequest])
def test_discovery_and_enrich_mark_extra_params_deprecated(request_type):
    field_schema = request_type.model_json_schema(by_alias=True)["properties"][
        "extraParams"
    ]

    assert field_schema["deprecated"] is True
    assert field_schema["default"] is None


@pytest.mark.parametrize("request_type", [EntityDiscoveryRequest, EntityEnrichRequest])
def test_discovery_and_enrich_reject_incorrect_ext_params(request_type):
    with pytest.raises(ValidationError, match="extra_forbidden"):
        request_type.model_validate(
            {"knCode": "1", "extParams": {"requestId": "req-1"}}
        )


@pytest.mark.parametrize(
    "request_type,payload",
    [
        (
            EntityDiscoveryRequest,
            {"knCode": "1", "targetKnCode": "2"},
        ),
        (
            EntityDiscoveryRequest,
            {"knCode": "1", "targetDirectoryPath": "/entities"},
        ),
        (
            EntityEnrichRequest,
            {"knCode": "1", "callback": "module:function"},
        ),
    ],
)
def test_http_request_contract_rejects_removed_or_internal_only_fields(
    request_type, payload
):
    with pytest.raises(ValidationError):
        request_type.model_validate(payload)


def test_task_status_is_scoped_by_kb_with_optional_file_path():
    whole_kb = ProcessingTaskStatusRequest.model_validate({"knCode": "1"})
    single_file = ProcessingTaskStatusRequest.model_validate(
        {"knCode": "1", "filePath": "/docs/a.md", "latestOnly": False}
    )

    assert whole_kb.kb_code == "1"
    assert whole_kb.file_path is None
    assert whole_kb.latest_only is True
    assert single_file.file_path == "/docs/a.md"
    assert single_file.latest_only is False

    with pytest.raises(ValidationError):
        ProcessingTaskStatusRequest.model_validate({"taskId": "9001"})


def test_batch_status_requires_kb_and_batch_identity():
    request = ProcessingBatchStatusRequest.model_validate(
        {"knCode": "1", "batchId": "ed-1", "pageSize": 20}
    )

    assert request.kb_code == "1"
    assert request.batch_id == "ed-1"
    assert request.page_size == 20


def test_semantic_relation_request_has_no_evidence_switch():
    request = SemanticRelationsRequest.model_validate(
        {"knCode": "1", "filePath": "/KnowledgeEntity/OSOT.md"}
    )
    assert request.direction.value == "BOTH"

    with pytest.raises(ValidationError):
        SemanticRelationsRequest.model_validate(
            {
                "knCode": "1",
                "filePath": "/KnowledgeEntity/OSOT.md",
                "includeEvidence": True,
            }
        )


def test_discovery_route_uses_provider_and_never_accepts_http_callback():
    service = FakeKnowledgeEntityService()
    client = make_client(service)

    response = client.post(
        "/api/v1/knowledgeItems/entityDiscovery",
        json={"knCode": "1", "extraParams": {"legacy": {"keep": True}}},
    )

    assert response.status_code == 200
    assert response.json()["resultMsg"] == "accepted"
    assert response.json()["resultObject"]["scope"] == "WHOLE_KB"
    operation, request, callback = service.calls[0]
    assert operation == "discovery"
    assert request.file_path is None
    assert request.model_dump()["extra_params"] == {"legacy": {"keep": True}}
    assert callback is None

    removed_field_response = client.post(
        "/api/v1/knowledgeItems/entityDiscovery",
        json={"knCode": "1", "definitionVersion": "ke/1.0"},
    )
    assert removed_field_response.json()["resultCode"] == "-1"

    invalid_response = client.post(
        "/api/v1/knowledgeItems/entityDiscovery",
        json={"knCode": "1", "callback": "module:function"},
    )
    assert invalid_response.json()["resultCode"] == "-1"
    assert len(service.calls) == 1


def test_discovery_route_passes_directory_scope_and_file_has_priority():
    service = FakeKnowledgeEntityService()
    client = make_client(service)

    directory_response = client.post(
        "/api/v1/knowledgeItems/entityDiscovery",
        json={"knCode": "1", "directoryPath": "/Policies"},
    )
    assert directory_response.json()["resultObject"]["scope"] == "DIRECTORY"
    assert service.calls[-1][1].directory_path == "/Policies"

    file_response = client.post(
        "/api/v1/knowledgeItems/entityDiscovery",
        json={
            "knCode": "1",
            "filePath": "/Policies/a.md",
            "directoryPath": "/ignored",
        },
    )
    assert file_response.json()["resultObject"]["scope"] == "SINGLE_FILE"


def test_enrich_route_supports_whole_kb_and_passes_no_callback():
    service = FakeKnowledgeEntityService()
    response = make_client(service).post(
        "/api/v1/knowledgeItems/entityEnrich",
        json={"knCode": "1", "extraParams": {"legacy": [1, "two"]}},
    )

    assert response.json()["resultMsg"] == "accepted"
    operation, request, callback = service.calls[0]
    assert operation == "enrich"
    assert request.file_path is None
    assert request.model_dump()["extra_params"] == {"legacy": [1, "two"]}
    assert callback is None

    removed_field = make_client(service).post(
        "/api/v1/knowledgeItems/entityEnrich",
        json={"knCode": "1", "evidenceKnCodeList": ["1", "2"]},
    )
    assert removed_field.json()["resultCode"] == "-1"


def test_status_route_queries_by_kb_and_optional_path():
    service = FakeKnowledgeEntityService()
    response = make_client(service).post(
        "/api/v1/knowledgeItems/processingTaskStatus",
        json={
            "knCode": "1",
            "filePath": "/docs/a.md",
            "taskType": "ENTITY_DISCOVERY",
            "pageNum": 2,
        },
    )

    assert response.json()["resultObject"] == {
        "knowledgeBaseId": "11",
        "knCode": "1",
        "total": 0,
        "pageNum": 2,
        "pageSize": 50,
        "data": [],
    }
    operation, request, _ = service.calls[0]
    assert operation == "status"
    assert request.file_path == "/docs/a.md"
    assert request.task_type.value == "ENTITY_DISCOVERY"


def test_batch_status_route_returns_progress_without_extra_params():
    service = FakeKnowledgeEntityService()
    response = make_client(service).post(
        "/api/v1/knowledgeItems/processingBatchStatus",
        json={"knCode": "1", "batchId": "ed-1"},
    )

    result = response.json()["resultObject"]
    assert result["completedCount"] == 1
    assert result["progress"] == 50
    assert "extraParams" not in result
    assert service.calls[0][0] == "batch_status"


def test_eligibility_and_semantic_relation_routes_delegate_to_service():
    service = FakeKnowledgeEntityService()
    client = make_client(service)

    eligibility = client.post(
        "/api/v1/knowledgeItems/processingEligibility",
        json={
            "knCode": "1",
            "filePath": "/docs/a.md",
            "capability": "entityDiscovery",
        },
    )
    relations = client.post(
        "/api/v1/knowledgeItems/semanticRelations",
        json={
            "knCode": "1",
            "filePath": "/KnowledgeEntity/OSOT.md",
            "relationCodeList": ["MENTIONS"],
        },
    )

    assert eligibility.json()["resultObject"]["reasonCode"] == "NEVER_PROCESSED"
    assert relations.json()["resultObject"]["data"] == []
    assert [call[0] for call in service.calls] == ["eligibility", "relations"]


def test_routes_remain_registerable_without_optional_entity_service_provider():
    response = make_client(None).post(
        "/api/v1/knowledgeItems/entityDiscovery",
        json={"knCode": "1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultCode": "-1",
        "resultMsg": "knowledge entity processing service is not configured",
        "resultObject": {},
    }


def test_entity_and_alias_delete_routes_validate_and_delegate():
    service = FakeKnowledgeEntityService()
    client = make_client(service)

    entity = client.post(
        "/api/v1/knowledgeEntities/delete",
        json={"knCode": "1", "entityId": 123},
    )
    alias = client.post(
        "/api/v1/knowledge-entities/aliases/delete",
        json={"knCode": "1", "entityId": 123, "aliasId": 456},
    )

    assert entity.json()["resultObject"] == {
        "deletedEntityCount": 1,
        "deletedAliasCount": 2,
        "deletedFileCount": 1,
    }
    assert alias.json()["resultObject"]["deletedAliasCount"] == 1
    assert [call[0] for call in service.calls] == ["delete_entity", "delete_alias"]

    invalid = client.post(
        "/api/v1/knowledgeEntities/delete",
        json={"knCode": "1", "entityId": 0},
    )
    assert invalid.json()["resultCode"] == "-1"
    assert len(service.calls) == 2
