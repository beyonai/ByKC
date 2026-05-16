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
    new_kb_with_file,
    register_property,
    runtime,
    set_metadata,
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


# ===================================================================
# Section 3: Reference-count protection  (M3.a-M3.c)
# ===================================================================


@pytest.mark.integration
def test_property_delete_referenced_rejected(monkeypatch):
    """M3.a: deleting a property that has at least one active value is rejected."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"ref_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="active",
        )

        resp = client.post(
            "/api/v1/metadataProperties/delete",
            json={"propertyName": prop},
        )
        assert resp.json()["resultCode"] == "-1"
        assert "still referenced" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_property_delete_after_unset_succeeds(monkeypatch):
    """M3.b: unset releases the reference so delete now succeeds."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"ref_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="active",
        )
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            operation="unset",
        )

        resp = client.post(
            "/api/v1/metadataProperties/delete",
            json={"propertyName": prop},
        )
        assert resp.json()["resultCode"] == "0"


@pytest.mark.integration
def test_property_delete_after_clear_still_rejected(monkeypatch):
    """M3.c: clear empties the list but keeps the value row, so delete still blocks."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"reflist_{uuid4().hex[:6]}"
        register_property(client, prop, "stringList")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value=["a", "b"],
        )
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            operation="clear",
        )

        resp = client.post(
            "/api/v1/metadataProperties/delete",
            json={"propertyName": prop},
        )
        assert resp.json()["resultCode"] == "-1"
        assert "still referenced" in resp.json()["resultMsg"]


# ===================================================================
# Section 4: Scalar metadata operations  (M4.a-M4.f)
# ===================================================================

SCALAR_VALUE_FIXTURES = [
    pytest.param("string", "active", id="string"),
    pytest.param("number", 42, id="number"),
    pytest.param("boolean", True, id="boolean"),
    pytest.param("datetime", "2026-05-15T10:00:00Z", id="datetime"),
    pytest.param("stringList", ["a", "b"], id="stringList"),
]


@pytest.mark.integration
@pytest.mark.parametrize("value_type, value", SCALAR_VALUE_FIXTURES)
def test_metadata_set_all_value_types(monkeypatch, value_type, value):
    """M4.a: set+get round-trips for every supported valueType."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"p_{uuid4().hex[:6]}"
        register_property(client, prop, value_type)
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value=value,
        )

        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": file_path},
        ).json()["resultObject"]["metadata"]
        assert got[prop]["valueType"] == value_type
        if value_type == "datetime":
            # service may store as datetime and emit ISO with offset; allow either form
            assert got[prop]["value"].startswith("2026-05-15T10:00:00")
        else:
            assert got[prop]["value"] == value


@pytest.mark.integration
def test_metadata_set_undefined_property_rejected(monkeypatch):
    """M4.b: writing to an unregistered property returns 'not defined'."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {"propertyName": "ghost", "operation": "set", "value": "x"}
                ],
            },
        )
        assert resp.json()["resultCode"] == "-1"
        assert "not defined" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_metadata_invalid_operation_literal_rejected(monkeypatch):
    """M4.c: operation outside {set,unset,append,remove,clear} fails the documented envelope."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"x_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {"propertyName": prop, "operation": "upsert", "value": "v"}
                ],
            },
        )
        # Routes normalize Pydantic ValidationError into the documented envelope:
        # HTTP 200 + resultCode="-1" + resultMsg="request validation failed".
        assert resp.status_code == 200
        assert resp.json()["resultCode"] == "-1"
        assert "request validation failed" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_metadata_multi_op_same_property_in_order(monkeypatch):
    """M4.d: multiple operations on same property in a single request apply in order."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"order_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {"propertyName": prop, "operation": "set", "value": "v1"},
                    {"propertyName": prop, "operation": "set", "value": "v2"},
                ],
            },
        )
        assert resp.json()["resultCode"] == "0"
        assert resp.json()["resultObject"]["metadata"][prop]["value"] == "v2"


@pytest.mark.integration
def test_metadata_unset_nonexistent_idempotent(monkeypatch):
    """M4.e: unset on a never-set property is a no-op, not an error."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"un_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [{"propertyName": prop, "operation": "unset"}],
            },
        )
        assert resp.json()["resultCode"] == "0"
        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": file_path},
        ).json()["resultObject"]["metadata"]
        assert prop not in got


@pytest.mark.integration
def test_metadata_unknown_kb_or_file_rejected(monkeypatch):
    """M4.f: unknown knCode / filePath both yield resultCode=-1 with descriptive msg."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"k_{uuid4().hex[:6]}"
        register_property(client, prop, "string")

        bad_kb = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": "9999999",
                "filePath": file_path,
                "operationList": [
                    {"propertyName": prop, "operation": "set", "value": "x"}
                ],
            },
        )
        assert bad_kb.json()["resultCode"] == "-1"
        assert "knowledge base not found" in bad_kb.json()["resultMsg"]

        bad_file = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": "/nope.md",
                "operationList": [
                    {"propertyName": prop, "operation": "set", "value": "x"}
                ],
            },
        )
        assert bad_file.json()["resultCode"] == "-1"
        assert "file not found" in bad_file.json()["resultMsg"]


# ===================================================================
# Section 5: stringList operations  (M5.a-M5.e)
# ===================================================================


@pytest.mark.integration
def test_metadata_append_dedup(monkeypatch):
    """M5.a: append skips items already present."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"l_{uuid4().hex[:6]}"
        register_property(client, prop, "stringList")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value=["a", "b"],
        )
        resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {"propertyName": prop, "operation": "append", "value": ["b", "c"]}
                ],
            },
        )
        assert resp.json()["resultObject"]["metadata"][prop]["value"] == ["a", "b", "c"]


@pytest.mark.integration
def test_metadata_remove_tolerates_missing(monkeypatch):
    """M5.b: remove silently ignores items not in the list."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"l_{uuid4().hex[:6]}"
        register_property(client, prop, "stringList")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value=["a"],
        )
        resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "operationList": [
                    {"propertyName": prop, "operation": "remove", "value": ["x", "y"]}
                ],
            },
        )
        assert resp.json()["resultCode"] == "0"
        assert resp.json()["resultObject"]["metadata"][prop]["value"] == ["a"]


@pytest.mark.integration
def test_metadata_set_overwrites_list(monkeypatch):
    """M5.c: set replaces the list entirely, no merging with prior content."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"l_{uuid4().hex[:6]}"
        register_property(client, prop, "stringList")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value=["a", "b"],
        )
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value=["x"],
        )
        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": file_path},
        ).json()["resultObject"]["metadata"]
        assert got[prop]["value"] == ["x"]


@pytest.mark.integration
def test_metadata_clear_keeps_value_type(monkeypatch):
    """M5.d: clear empties the list but the property row + valueType remain visible."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"l_{uuid4().hex[:6]}"
        register_property(client, prop, "stringList")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value=["a", "b"],
        )
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            operation="clear",
        )
        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": file_path},
        ).json()["resultObject"]["metadata"]
        assert got[prop] == {"valueType": "stringList", "value": []}


@pytest.mark.integration
@pytest.mark.parametrize(
    "value_type, op, value, expected_msg",
    [
        pytest.param("string", "append", ["x"], "not allowed", id="append_on_string"),
        pytest.param("string", "remove", ["x"], "not allowed", id="remove_on_string"),
        pytest.param("string", "clear", None, "not allowed", id="clear_on_string"),
        pytest.param("number", "append", [1], "not allowed", id="append_on_number"),
    ],
)
def test_metadata_op_type_mismatch_rejected(
    monkeypatch, value_type, op, value, expected_msg
):
    """M5.e: list-only ops on scalar fields (and vice versa) are rejected."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"m_{uuid4().hex[:6]}"
        register_property(client, prop, value_type)
        body_op = {"propertyName": prop, "operation": op}
        if value is not None:
            body_op["value"] = value
        resp = client.post(
            "/api/v1/knowledgeItems/metadata/update",
            json={"knCode": kb_code, "filePath": file_path, "operationList": [body_op]},
        )
        assert resp.json()["resultCode"] == "-1"
        assert expected_msg in resp.json()["resultMsg"]
