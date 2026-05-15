"""Integration tests for metadata property and file metadata APIs.

Requires the Docker middleware stack (make kb-stack-up).
Run: uv run python -m pytest tests/knowledge_base/integration/test_metadata_api_integration.py -v
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import by_qa.main as main_module
from by_qa.config import Settings

# ---------------------------------------------------------------------------
# Reusable helpers from the existing stateful integration test suite
# ---------------------------------------------------------------------------
from tests.knowledge_base.integration.test_kb_api_stateful_integration import (
    FakeDocumentChunkingService,
    FakeEmbeddingQueryService,
    FakeModelConfigProvider,
    _create_kb,
    _set_document_chunking_service,
    _upload_file,
)

DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = "15432"
DEFAULT_DB_DATABASE = "postgres"
DEFAULT_DB_USER = "gaussdb"
DEFAULT_DB_PASS = "OpenGauss#2026"


# ---------------------------------------------------------------------------
# Settings & runtime helpers
# ---------------------------------------------------------------------------


def _kb_settings() -> Settings:
    return Settings(
        DB_HOST=os.getenv("DB_HOST", DEFAULT_DB_HOST),
        DB_PORT=int(os.getenv("DB_PORT", DEFAULT_DB_PORT)),
        DB_DATABASE=os.getenv("DB_DATABASE", DEFAULT_DB_DATABASE),
        DB_SCHEMA=os.getenv("DB_SCHEMA", ""),
        DB_USER=os.getenv("DB_USER", DEFAULT_DB_USER),
        DB_PASS=os.getenv("DB_PASS", DEFAULT_DB_PASS),
        MINIO_ENDPOINT=os.getenv("MINIO_ENDPOINT", "127.0.0.1:19000"),
        MINIO_ACCESS_KEY=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        MINIO_SECRET_KEY=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        KB_MINIO_BUCKET=os.getenv("KB_MINIO_BUCKET", "knowledge-base"),
        KB_MINIO_MARKDOWN_BUCKET=os.getenv(
            "KB_MINIO_MARKDOWN_BUCKET", "knowledge-base-markdown"
        ),
        MINIO_SECURE=False,
        EMBEDDING_MODEL_NAME=os.getenv("EMBEDDING_MODEL_NAME", "bge-m3"),
        EMBEDDING_BASE_URL="https://embedding.example.com",
        EMBEDDING_API_KEY="secret",
        EMBEDDING_DIMENSION=int(os.getenv("EMBEDDING_DIMENSION", "3")),
        EMBEDDING_DISTANCE_METRIC=os.getenv("EMBEDDING_DISTANCE_METRIC", "cosine"),
    )


def _reset_runtime(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Reset ALL module-level singletons so each test starts with a clean slate."""
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(
        main_module,
        "load_model_config_provider",
        lambda: FakeModelConfigProvider(settings),
    )
    # Knowledge-base services
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_search_service", None)
    monkeypatch.setattr(main_module, "_knowledge_fetch_cache_cleanup_service", None)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)
    # Metadata services
    monkeypatch.setattr(main_module, "_metadata_property_service", None)
    monkeypatch.setattr(main_module, "_file_metadata_service", None)
    monkeypatch.setattr(main_module, "_metadata_search_service", None)
    # Schema state
    monkeypatch.setattr(main_module, "_knowledge_base_schema_initialized", False)
    monkeypatch.setattr(main_module, "_knowledge_base_schema_lock", asyncio.Lock())

    async def _noop_register(application):  # pylint: disable=unused-argument
        return None

    monkeypatch.setattr(main_module, "_register_service", _noop_register)
    monkeypatch.setattr(main_module, "_unregister_service", _noop_register)


def _create_directory(client: TestClient, *, kb_code: str, directory_path: str) -> None:
    resp = client.post(
        "/api/v1/directories/create",
        json={"knCode": kb_code, "directoryPath": directory_path},
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Fixture-style setup helpers
# ---------------------------------------------------------------------------


def _setup_kb_with_file_and_properties(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, str, str]:
    """Create KB, upload file, create properties. Returns (kb_code, file_path, prop1, prop2)."""
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(markdown_text="# Test\nContent here."),
    )

    kb_name = f"MetaKB_{uuid4().hex[:8]}"
    kb_code = _create_kb(client, kb_name)

    _create_directory(client, kb_code=kb_code, directory_path="/docs")

    file_path = "/docs/test.md"
    _upload_file(
        client,
        kb_code=kb_code,
        file_path=file_path,
        file_content=b"# Test\nContent",
    )

    prop1 = f"status_{uuid4().hex[:6]}"
    prop2 = f"tags_{uuid4().hex[:6]}"
    _create_property(client, prop1, "string")
    _create_property(client, prop2, "stringList")
    return kb_code, file_path, prop1, prop2


def _setup_kb_with_built_file_and_metadata(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, str, str]:
    """Create KB, upload+build file, set metadata. Returns (kb_code, file_path, prop1, prop2)."""
    embedding = [0.1, 0.2, 0.3]
    _set_document_chunking_service(
        monkeypatch,
        FakeDocumentChunkingService(
            markdown_text="# 续签流程\n合同续签需由业务负责人发起审批。",
            embedding=embedding,
        ),
    )

    kb_name = f"SearchKB_{uuid4().hex[:8]}"
    kb_code = _create_kb(client, kb_name)

    _create_directory(client, kb_code=kb_code, directory_path="/制度")

    file_path = "/制度/续签流程.md"
    content = "# 续签流程\n合同续签需由业务负责人发起审批。".encode("utf-8")
    _upload_file(
        client,
        kb_code=kb_code,
        file_path=file_path,
        file_content=content,
    )

    # Build the file
    resp = client.post(
        "/api/v1/fileToMarkdownIndex",
        json={"knCode": kb_code, "filePath": file_path},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["resultCode"] == "0", resp.text

    # Create properties and set metadata
    prop1 = f"status_{uuid4().hex[:6]}"
    prop2 = f"tags_{uuid4().hex[:6]}"
    _create_property(client, prop1, "string")
    _create_property(client, prop2, "stringList")

    resp = client.post(
        "/api/v1/knowledgeItems/metadata/update",
        json={
            "knCode": kb_code,
            "filePath": file_path,
            "operationList": [
                {"propertyName": prop1, "operation": "set", "value": "active"},
                {
                    "propertyName": prop2,
                    "operation": "set",
                    "value": ["hr", "contract"],
                },
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    # Patch search service with fake embedding
    async def _get_search_service(provider=None):  # pylint: disable=unused-argument
        from by_qa.knowledge_base.infrastructure.runtime import (
            build_knowledge_item_search_service,
        )

        service = await build_knowledge_item_search_service(_kb_settings())
        service.embedding_query_service = FakeEmbeddingQueryService(embedding)
        return service

    monkeypatch.setattr(
        main_module,
        "_get_or_build_knowledge_item_search_service",
        _get_search_service,
    )

    return kb_code, file_path, prop1, prop2


def _create_property(client: TestClient, property_name: str, value_type: str) -> None:
    resp = client.post(
        "/api/v1/metadataProperties/create",
        json={"propertyName": property_name, "valueType": value_type},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["resultCode"] == "0", resp.text


# ===================================================================
# Section 2: Property Definition Tests  (Task 18)
# ===================================================================


@pytest.mark.integration
def test_property_create_and_list(monkeypatch):
    """Create a property, list all, and list filtered."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        prop_name = f"test_prop_{uuid4().hex[:8]}"

        # Create
        create_resp = client.post(
            "/api/v1/metadataProperties/create",
            json={"propertyName": prop_name, "valueType": "number"},
        )
        assert create_resp.status_code == 200
        payload = create_resp.json()
        assert payload["resultCode"] == "0"
        assert payload["resultObject"]["propertyName"] == prop_name
        assert payload["resultObject"]["valueType"] == "number"

        # List all
        list_resp = client.post("/api/v1/metadataProperties/list", json={})
        assert list_resp.status_code == 200
        list_payload = list_resp.json()
        assert list_payload["resultCode"] == "0"
        prop_names = [p["propertyName"] for p in list_payload["resultObject"]["data"]]
        assert prop_name in prop_names

        # List filtered
        filtered_resp = client.post(
            "/api/v1/metadataProperties/list",
            json={"propertyNameList": [prop_name]},
        )
        assert filtered_resp.status_code == 200
        filtered_data = filtered_resp.json()["resultObject"]["data"]
        assert len(filtered_data) == 1
        assert filtered_data[0]["propertyName"] == prop_name


@pytest.mark.integration
def test_property_create_duplicate_fails(monkeypatch):
    """Creating a property with a duplicate name returns an error."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        prop_name = f"dup_prop_{uuid4().hex[:8]}"

        first = client.post(
            "/api/v1/metadataProperties/create",
            json={"propertyName": prop_name, "valueType": "string"},
        )
        assert first.status_code == 200
        assert first.json()["resultCode"] == "0"

        second = client.post(
            "/api/v1/metadataProperties/create",
            json={"propertyName": prop_name, "valueType": "string"},
        )
        assert second.status_code == 200
        assert second.json()["resultCode"] == "-1"
        assert "already exists" in second.json()["resultMsg"]


@pytest.mark.integration
def test_property_batch_create_atomic(monkeypatch):
    """Batch create creates multiple properties atomically."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        p1 = f"batch_a_{uuid4().hex[:6]}"
        p2 = f"batch_b_{uuid4().hex[:6]}"

        batch_resp = client.post(
            "/api/v1/metadataProperties/batchCreate",
            json={
                "propertyList": [
                    {"propertyName": p1, "valueType": "string"},
                    {"propertyName": p2, "valueType": "number"},
                ]
            },
        )
        assert batch_resp.status_code == 200
        payload = batch_resp.json()
        assert payload["resultCode"] == "0"
        assert len(payload["resultObject"]["data"]) == 2

        # Verify both exist
        list_resp = client.post(
            "/api/v1/metadataProperties/list",
            json={"propertyNameList": [p1, p2]},
        )
        assert list_resp.status_code == 200
        names = [p["propertyName"] for p in list_resp.json()["resultObject"]["data"]]
        assert p1 in names
        assert p2 in names


@pytest.mark.integration
def test_property_delete_and_referenced_check(monkeypatch):
    """Delete a property with no references succeeds, and it is no longer listed."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        prop_name = f"delete_me_{uuid4().hex[:8]}"

        # Create
        create_resp = client.post(
            "/api/v1/metadataProperties/create",
            json={"propertyName": prop_name, "valueType": "boolean"},
        )
        assert create_resp.json()["resultCode"] == "0"

        # Delete
        delete_resp = client.post(
            "/api/v1/metadataProperties/delete",
            json={"propertyName": prop_name},
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["resultCode"] == "0"

        # Verify gone
        list_resp = client.post(
            "/api/v1/metadataProperties/list",
            json={"propertyNameList": [prop_name]},
        )
        names = [p["propertyName"] for p in list_resp.json()["resultObject"]["data"]]
        assert prop_name not in names


@pytest.mark.integration
def test_property_create_system_field_conflict(monkeypatch):
    """Creating a property with a system field name is rejected."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    system_names = [
        "fileName",
        "filePath",
        "fileType",
        "fileSize",
        "mimeType",
        "createdAt",
        "updatedAt",
    ]

    with TestClient(main_module.app) as client:
        for name in system_names:
            resp = client.post(
                "/api/v1/metadataProperties/create",
                json={"propertyName": name, "valueType": "string"},
            )
            assert resp.status_code == 200
            assert resp.json()["resultCode"] == "-1"
            assert "conflicts with system field" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_property_delete_system_property_fails(monkeypatch):
    """Cannot delete a system property (is_system flag)."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    # We cannot create a system property, but the test verifies that
    # trying to delete a non-existent property returns the right error.
    with TestClient(main_module.app) as client:
        resp = client.post(
            "/api/v1/metadataProperties/delete",
            json={"propertyName": "nonexistent_property"},
        )
        assert resp.status_code == 200
        assert resp.json()["resultCode"] == "-1"
        assert "not found" in resp.json()["resultMsg"]


# ===================================================================
# Section 3: File Metadata CRUD Tests  (Task 19)
# ===================================================================


@pytest.mark.integration
def test_metadata_update_set_and_get(monkeypatch):
    """Set string + stringList metadata and get it back."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, prop1, prop2 = _setup_kb_with_file_and_properties(
            client, monkeypatch
        )

        # Set metadata
        update_resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {"propertyName": prop1, "operation": "set", "value": "active"},
                    {
                        "propertyName": prop2,
                        "operation": "set",
                        "value": ["hr", "contract"],
                    },
                ],
            },
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["resultCode"] == "0"
        metadata = update_resp.json()["resultObject"]["metadata"]
        assert prop1 in metadata
        assert metadata[prop1]["value"] == "active"
        assert metadata[prop1]["valueType"] == "string"
        assert prop2 in metadata
        assert metadata[prop2]["value"] == ["hr", "contract"]
        assert metadata[prop2]["valueType"] == "stringList"

        # Get metadata
        get_resp = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": file_path},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["resultCode"] == "0"
        get_meta = get_resp.json()["resultObject"]["metadata"]
        assert get_meta[prop1]["value"] == "active"
        assert get_meta[prop2]["value"] == ["hr", "contract"]


@pytest.mark.integration
def test_metadata_update_append_remove_clear(monkeypatch):
    """Append, remove, and clear operations on stringList."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, _, prop2 = _setup_kb_with_file_and_properties(
            client, monkeypatch
        )

        # Set initial
        client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {
                        "propertyName": prop2,
                        "operation": "set",
                        "value": ["a", "b"],
                    }
                ],
            },
        )

        # Append
        append_resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {
                        "propertyName": prop2,
                        "operation": "append",
                        "value": ["c"],
                    }
                ],
            },
        )
        assert append_resp.json()["resultCode"] == "0"
        assert append_resp.json()["resultObject"]["metadata"][prop2]["value"] == [
            "a",
            "b",
            "c",
        ]

        # Remove
        remove_resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {
                        "propertyName": prop2,
                        "operation": "remove",
                        "value": ["b"],
                    }
                ],
            },
        )
        assert remove_resp.json()["resultCode"] == "0"
        assert remove_resp.json()["resultObject"]["metadata"][prop2]["value"] == [
            "a",
            "c",
        ]

        # Clear
        clear_resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {
                        "propertyName": prop2,
                        "operation": "clear",
                    }
                ],
            },
        )
        assert clear_resp.json()["resultCode"] == "0"
        assert clear_resp.json()["resultObject"]["metadata"][prop2]["value"] == []


@pytest.mark.integration
def test_metadata_update_unset(monkeypatch):
    """Unset removes a property from metadata."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, prop1, _ = _setup_kb_with_file_and_properties(
            client, monkeypatch
        )

        # Set then unset
        client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {"propertyName": prop1, "operation": "set", "value": "hello"}
                ],
            },
        )
        unset_resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [{"propertyName": prop1, "operation": "unset"}],
            },
        )
        assert unset_resp.json()["resultCode"] == "0"

        # Verify gone
        get_resp = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": file_path},
        )
        assert get_resp.json()["resultCode"] == "0"
        assert prop1 not in get_resp.json()["resultObject"]["metadata"]


@pytest.mark.integration
def test_metadata_fields_list(monkeypatch):
    """List metadataFields returns only properties that have values set."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, prop1, prop2 = _setup_kb_with_file_and_properties(
            client, monkeypatch
        )

        # Set a value on prop1 only
        client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {"propertyName": prop1, "operation": "set", "value": "val"}
                ],
            },
        )

        # List fields
        fields_resp = client.post(
            "/api/v1/knowledgeItems/metadataFields/list",
            json={"knCodeList": [kb_code]},
        )
        assert fields_resp.status_code == 200
        assert fields_resp.json()["resultCode"] == "0"
        names = [f["propertyName"] for f in fields_resp.json()["resultObject"]["data"]]
        assert prop1 in names
        # prop2 has no value set, should not be listed
        assert prop2 not in names


@pytest.mark.integration
def test_metadata_update_invalid_operation_for_type(monkeypatch):
    """append on a string property is rejected."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, prop1, _ = _setup_kb_with_file_and_properties(
            client, monkeypatch
        )

        resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {
                        "propertyName": prop1,
                        "operation": "append",
                        "value": ["x"],
                    }
                ],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["resultCode"] == "-1"
        assert "not allowed" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_metadata_update_system_property_fails(monkeypatch):
    """Cannot modify system metadata properties."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, _, _ = _setup_kb_with_file_and_properties(
            client, monkeypatch
        )

        resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {"propertyName": "filePath", "operation": "set", "value": "/x"}
                ],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["resultCode"] == "-1"
        assert "metadata property not defined" in resp.json()["resultMsg"]


# ===================================================================
# Section 4: Metadata Search Tests  (Task 20)
# ===================================================================


@pytest.mark.integration
def test_metadata_search_no_filter(monkeypatch):
    """Search without 'where' returns files with metadata."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, prop1, prop2 = _setup_kb_with_file_and_properties(
            client, monkeypatch
        )

        # Set metadata values
        client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {"propertyName": prop1, "operation": "set", "value": "active"},
                    {
                        "propertyName": prop2,
                        "operation": "set",
                        "value": ["hr"],
                    },
                ],
            },
        )

        # Metadata search without where filter
        search_resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "topK": 10,
            },
        )
        assert search_resp.status_code == 200
        assert search_resp.json()["resultCode"] == "0"
        data = search_resp.json()["resultObject"]["data"]
        assert len(data) >= 1
        matching = [h for h in data if h["filePath"] == file_path]
        assert len(matching) >= 1


@pytest.mark.integration
def test_metadata_search_with_eq_filter(monkeypatch):
    """Eq filter returns matching files and excludes non-matching."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, prop1, _ = _setup_kb_with_file_and_properties(
            client, monkeypatch
        )

        # Set metadata
        client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {"propertyName": prop1, "operation": "set", "value": "matched"}
                ],
            },
        )

        # Search with eq filter
        search_resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {
                    "and": [
                        {
                            "eq": {
                                "fieldName": prop1,
                                "value": "matched",
                            }
                        }
                    ]
                },
                "topK": 10,
            },
        )
        assert search_resp.status_code == 200
        assert search_resp.json()["resultCode"] == "0"
        data = search_resp.json()["resultObject"]["data"]
        assert len(data) >= 1
        matching = [h for h in data if h["filePath"] == file_path]
        assert len(matching) >= 1

        # Search with non-matching filter
        search_resp2 = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {
                    "and": [
                        {
                            "eq": {
                                "fieldName": prop1,
                                "value": "nonexistent",
                            }
                        }
                    ]
                },
                "topK": 10,
            },
        )
        assert search_resp2.status_code == 200
        assert search_resp2.json()["resultCode"] == "0"
        non_matching = [
            h
            for h in search_resp2.json()["resultObject"]["data"]
            if h["filePath"] == file_path
        ]
        assert len(non_matching) == 0


@pytest.mark.integration
def test_metadata_search_dsl_validation_error(monkeypatch):
    """Unknown field in DSL returns a DSL validation error."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, _, _, _ = _setup_kb_with_file_and_properties(client, monkeypatch)

        search_resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {
                    "eq": {
                        "fieldName": "undefined_field",
                        "value": "x",
                    }
                },
                "topK": 5,
            },
        )
        assert search_resp.status_code == 200
        payload = search_resp.json()
        assert payload["resultCode"] == "-1"
        assert payload["resultObject"]["errorCode"] == "DSL_VALIDATION_ERROR"
        assert len(payload["resultObject"]["errorList"]) >= 1


@pytest.mark.integration
def test_metadata_search_contains_string_list(monkeypatch):
    """Contains filter works on stringList metadata."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, _, prop2 = _setup_kb_with_file_and_properties(
            client, monkeypatch
        )

        # Set stringList metadata
        client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {
                        "propertyName": prop2,
                        "operation": "set",
                        "value": ["hr", "contract"],
                    }
                ],
            },
        )

        # Search with contains on stringList
        search_resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {
                    "contains": {
                        "fieldName": prop2,
                        "value": "hr",
                    }
                },
                "topK": 10,
            },
        )
        assert search_resp.status_code == 200
        assert search_resp.json()["resultCode"] == "0"
        data = search_resp.json()["resultObject"]["data"]
        assert len(data) >= 1
        matching = [h for h in data if h["filePath"] == file_path]
        assert len(matching) >= 1

        # Search with non-matching contains
        search_resp2 = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {
                    "contains": {
                        "fieldName": prop2,
                        "value": "nonexistent",
                    }
                },
                "topK": 10,
            },
        )
        assert search_resp2.status_code == 200
        assert search_resp2.json()["resultCode"] == "0"
        assert len(search_resp2.json()["resultObject"]["data"]) == 0


# ===================================================================
# Section 5: Semantic Search + DSL Tests  (Task 21)
# ===================================================================


@pytest.mark.integration
def test_search_with_dsl_filter(monkeypatch):
    """Semantic search with where filter returns results with metadata."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, prop1, _ = _setup_kb_with_built_file_and_metadata(
            client, monkeypatch
        )

        search_resp = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "where": {
                    "eq": {
                        "fieldName": prop1,
                        "value": "active",
                    }
                },
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )
        assert search_resp.status_code == 200
        payload = search_resp.json()
        assert payload["resultCode"] == "0"
        data = payload["resultObject"]["data"]
        assert len(data) >= 1
        matching = [h for h in data if h["filePath"] == file_path]
        assert len(matching) >= 1
        # Chunk-level search returns metadata on each hit
        if matching[0].get("metadata"):
            assert matching[0]["metadata"].get(prop1) is not None


@pytest.mark.integration
def test_search_with_dsl_filter_excludes_non_matching(monkeypatch):
    """Non-matching where filter returns empty results in semantic search."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, _, prop1, _ = _setup_kb_with_built_file_and_metadata(
            client, monkeypatch
        )

        search_resp = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "where": {
                    "eq": {
                        "fieldName": prop1,
                        "value": "inactive",
                    }
                },
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )
        assert search_resp.status_code == 200
        assert search_resp.json()["resultCode"] == "0"
        data = search_resp.json()["resultObject"]["data"]
        assert len(data) == 0


@pytest.mark.integration
def test_search_backward_compatible_without_dsl(monkeypatch):
    """Legacy semantic search (without where/metadataFieldList) still works."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, _, _ = _setup_kb_with_built_file_and_metadata(
            client, monkeypatch
        )

        search_resp = client.post(
            "/api/v1/knowledgeItems/search",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )
        assert search_resp.status_code == 200
        payload = search_resp.json()
        assert payload["resultCode"] == "0"
        data = payload["resultObject"]["data"]
        assert len(data) >= 1
        matching = [h for h in data if h["filePath"] == file_path]
        assert len(matching) >= 1


@pytest.mark.integration
def test_search_file_with_dsl(monkeypatch):
    """searchFile returns file-level results with metadata."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code, file_path, _, _ = _setup_kb_with_built_file_and_metadata(
            client, monkeypatch
        )

        search_resp = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "topK": 5,
                "searchMode": "mixedRecall",
            },
        )
        assert search_resp.status_code == 200
        payload = search_resp.json()
        assert payload["resultCode"] == "0"
        data = payload["resultObject"]["data"]
        assert len(data) >= 1
        matching = [h for h in data if h["filePath"] == file_path]
        assert len(matching) >= 1


# ===================================================================
# Section 6: YAML Front Matter Tests  (from Task 9.5)
# ===================================================================


@pytest.mark.integration
def test_import_markdown_with_front_matter_auto_metadata(monkeypatch):
    """Uploading a Markdown file with front matter auto-populates metadata."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"fm_kb_{uuid4().hex[:8]}")
        prop_name = f"status_{uuid4().hex[:8]}"

        # Define the property first
        _create_property(client, prop_name, "string")

        # Create directory
        _create_directory(client, kb_code=kb_code, directory_path="/docs")

        # Upload markdown with front matter referencing the property
        md_content = f"---\n{prop_name}: active\n---\n# Hello\n".encode()
        file_path = f"/docs/fm_test_{uuid4().hex[:8]}.md"
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": file_path},
            files={"fileContent": ("test.md", md_content, "text/markdown")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["resultCode"] == "0"

        # Verify metadata was auto-set
        resp = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": file_path},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["resultCode"] == "0"
        metadata = resp.json()["resultObject"]["metadata"]
        assert prop_name in metadata
        assert metadata[prop_name]["value"] == "active"


@pytest.mark.integration
def test_import_markdown_with_undefined_front_matter_fails(monkeypatch):
    """Uploading a Markdown with undefined front matter property fails."""
    settings = _kb_settings()
    _reset_runtime(monkeypatch, settings)

    with TestClient(main_module.app) as client:
        kb_code = _create_kb(client, f"fm_fail_kb_{uuid4().hex[:8]}")
        _create_directory(client, kb_code=kb_code, directory_path="/docs")

        md_content = b"---\nundefined_prop: value\n---\n# Hello\n"
        file_path = f"/docs/fm_fail_{uuid4().hex[:8]}.md"
        resp = client.post(
            "/api/v1/knowledgeItems/import",
            data={"knCode": kb_code, "filePath": file_path},
            files={"fileContent": ("test.md", md_content, "text/markdown")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["resultCode"] == "-1"
        assert "not a defined metadata property" in resp.json()["resultMsg"]
