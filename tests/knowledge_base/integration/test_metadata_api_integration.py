"""Integration tests for metadata properties, file metadata, and DSL search.

Scenario coverage maps to docs/modules/api-integration-test-plan.md M1-M17.

Requires:
  - `make kb-stack-up` (OpenGauss + MinIO + Redis)
  - Reachable embedding service via EMBEDDING_BASE_URL / EMBEDDING_API_KEY /
    EMBEDDING_MODEL_NAME / EMBEDDING_DIMENSION env vars.

Run:
  NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= \
    uv run python -m pytest tests/knowledge_base/integration/test_metadata_api_integration.py -v
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.knowledge_base.integration._metadata_helpers import (
    register_property,
    runtime,
)

# ===================================================================
# Section 1: Property definition lifecycle  (M1.a-M1.i)
# ===================================================================


@pytest.mark.integration
def test_property_create_and_list_all(monkeypatch):
    """M1.a: create three properties, listing without filter returns all of them."""
    with runtime(monkeypatch) as client:
        names = [f"p_{uuid4().hex[:6]}" for _ in range(3)]
        for n in names:
            register_property(client, n, "string")
        resp = client.post("/api/v1/metadataProperties/list", json={})
        assert resp.json()["resultCode"] == "0"
        listed = {p["propertyName"] for p in resp.json()["resultObject"]["data"]}
        assert set(names).issubset(listed)


@pytest.mark.integration
def test_property_list_filter_by_names(monkeypatch):
    """M1.b: list with propertyNameList returns only those entries."""
    with runtime(monkeypatch) as client:
        keep = f"keep_{uuid4().hex[:6]}"
        skip = f"skip_{uuid4().hex[:6]}"
        register_property(client, keep, "string")
        register_property(client, skip, "string")
        resp = client.post(
            "/api/v1/metadataProperties/list",
            json={"propertyNameList": [keep]},
        )
        names = {p["propertyName"] for p in resp.json()["resultObject"]["data"]}
        assert names == {keep}


@pytest.mark.integration
def test_property_list_filter_unknown_returns_empty(monkeypatch):
    """M1.c: filtering by an unknown name returns an empty data array, not error."""
    with runtime(monkeypatch) as client:
        resp = client.post(
            "/api/v1/metadataProperties/list",
            json={"propertyNameList": ["does_not_exist"]},
        )
        assert resp.status_code == 200
        assert resp.json()["resultCode"] == "0"
        assert resp.json()["resultObject"]["data"] == []


@pytest.mark.integration
def test_property_create_duplicate_rejected(monkeypatch):
    """M1.d: re-creating a name yields resultCode=-1 with 'already exists'."""
    with runtime(monkeypatch) as client:
        n = f"dup_{uuid4().hex[:6]}"
        register_property(client, n, "string")
        resp = client.post(
            "/api/v1/metadataProperties/create",
            json={"propertyName": n, "valueType": "string"},
        )
        assert resp.json()["resultCode"] == "-1"
        assert "already exists" in resp.json()["resultMsg"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "name",
    [
        "fileName",
        "filePath",
        "fileType",
        "fileSize",
        "mimeType",
        "createdAt",
        "updatedAt",
    ],
)
def test_property_create_system_field_conflict(monkeypatch, name):
    """M1.e: registering a system field name is rejected."""
    with runtime(monkeypatch) as client:
        resp = client.post(
            "/api/v1/metadataProperties/create",
            json={"propertyName": name, "valueType": "string"},
        )
        assert resp.json()["resultCode"] == "-1"
        assert "conflicts with system field" in resp.json()["resultMsg"]


@pytest.mark.integration
@pytest.mark.parametrize("bad_name", ["", "x" * 129])
def test_property_create_property_name_bounds_rejected(monkeypatch, bad_name):
    """M1.f: empty or >128-char propertyName fails Pydantic validation.

    Routes normalize Pydantic ValidationError to HTTP 200 with
    resultCode=-1 and resultMsg="request validation failed" via
    `_documented_error_response`.
    """
    with runtime(monkeypatch) as client:
        resp = client.post(
            "/api/v1/metadataProperties/create",
            json={"propertyName": bad_name, "valueType": "string"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resultCode"] == "-1"
        assert body["resultMsg"] == "request validation failed"


@pytest.mark.integration
@pytest.mark.parametrize("vt", ["int", "json", "", "STRING"])
def test_property_create_invalid_value_type_rejected(monkeypatch, vt):
    """M1.g: valueType outside {string,stringList,number,boolean,datetime} fails."""
    with runtime(monkeypatch) as client:
        resp = client.post(
            "/api/v1/metadataProperties/create",
            json={"propertyName": f"vt_{uuid4().hex[:6]}", "valueType": vt},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resultCode"] == "-1"
        assert body["resultMsg"] == "request validation failed"


@pytest.mark.integration
def test_property_delete_nonexistent_rejected(monkeypatch):
    """M1.h: deleting a non-existent property returns 'not found'."""
    with runtime(monkeypatch) as client:
        resp = client.post(
            "/api/v1/metadataProperties/delete",
            json={"propertyName": f"ghost_{uuid4().hex[:6]}"},
        )
        assert resp.json()["resultCode"] == "-1"
        assert "not found" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_property_delete_unused_succeeds(monkeypatch):
    """M1.i: a property with no references can be deleted, then disappears from list."""
    with runtime(monkeypatch) as client:
        n = f"del_{uuid4().hex[:6]}"
        register_property(client, n, "boolean")
        resp = client.post(
            "/api/v1/metadataProperties/delete",
            json={"propertyName": n},
        )
        assert resp.json()["resultCode"] == "0"
        listed = client.post(
            "/api/v1/metadataProperties/list",
            json={"propertyNameList": [n]},
        ).json()["resultObject"]["data"]
        assert listed == []


# ===================================================================
# Section 2: Batch property creation atomicity  (M2.a-M2.d)
# ===================================================================


@pytest.mark.integration
def test_batch_create_multiple_succeeds(monkeypatch):
    """M2.a: batchCreate persists every item when none conflict."""
    with runtime(monkeypatch) as client:
        a = f"a_{uuid4().hex[:6]}"
        b = f"b_{uuid4().hex[:6]}"
        resp = client.post(
            "/api/v1/metadataProperties/batchCreate",
            json={
                "propertyList": [
                    {"propertyName": a, "valueType": "string"},
                    {"propertyName": b, "valueType": "number"},
                ]
            },
        )
        assert resp.json()["resultCode"] == "0"
        assert {p["propertyName"] for p in resp.json()["resultObject"]["data"]} == {
            a,
            b,
        }


@pytest.mark.integration
def test_batch_create_conflict_rolls_back(monkeypatch):
    """M2.b: a conflict in one item rolls back the whole batch."""
    with runtime(monkeypatch) as client:
        existing = f"e_{uuid4().hex[:6]}"
        new_one = f"n_{uuid4().hex[:6]}"
        register_property(client, existing, "string")
        resp = client.post(
            "/api/v1/metadataProperties/batchCreate",
            json={
                "propertyList": [
                    {"propertyName": new_one, "valueType": "string"},
                    {"propertyName": existing, "valueType": "string"},
                ]
            },
        )
        assert resp.json()["resultCode"] == "-1"
        assert "already exists" in resp.json()["resultMsg"]
        listed = client.post(
            "/api/v1/metadataProperties/list",
            json={"propertyNameList": [new_one]},
        ).json()["resultObject"]["data"]
        assert listed == []  # rollback proven: new_one never persisted


@pytest.mark.integration
def test_batch_create_invalid_type_rolls_back(monkeypatch):
    """M2.c: an invalid valueType in any item fails the entire batch."""
    with runtime(monkeypatch) as client:
        ok = f"ok_{uuid4().hex[:6]}"
        bad = f"bad_{uuid4().hex[:6]}"
        resp = client.post(
            "/api/v1/metadataProperties/batchCreate",
            json={
                "propertyList": [
                    {"propertyName": ok, "valueType": "string"},
                    {"propertyName": bad, "valueType": "INVALID"},
                ]
            },
        )
        # Routes normalize Pydantic ValidationError into the documented envelope:
        # HTTP 200 + resultCode="-1" + resultMsg="request validation failed".
        assert resp.status_code == 200
        assert resp.json()["resultCode"] == "-1"
        assert "request validation failed" in resp.json()["resultMsg"]
        listed = client.post(
            "/api/v1/metadataProperties/list",
            json={"propertyNameList": [ok]},
        ).json()["resultObject"]["data"]
        assert listed == []


@pytest.mark.integration
def test_batch_create_empty_list_rejected(monkeypatch):
    """M2.d: empty propertyList is rejected by min_length=1 schema."""
    with runtime(monkeypatch) as client:
        resp = client.post(
            "/api/v1/metadataProperties/batchCreate",
            json={"propertyList": []},
        )
        # Routes normalize Pydantic ValidationError into the documented envelope:
        # HTTP 200 + resultCode="-1" + resultMsg="request validation failed".
        assert resp.status_code == 200
        assert resp.json()["resultCode"] == "-1"
        assert "request validation failed" in resp.json()["resultMsg"]
