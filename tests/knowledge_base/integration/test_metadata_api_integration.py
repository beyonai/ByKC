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
    build_dsl_dataset,
    build_filepath_dsl_dataset,
    chunk_search,
    metadata_search_paths,
    new_kb,
    new_kb_with_built_file,
    new_kb_with_file,
    register_property,
    register_property_set,
    runtime,
    set_metadata,
    wait_for_build,
)
from tests.knowledge_base.integration.test_kb_api_stateful_integration import (
    _upload_file,  # plain helper, not a fake
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


@pytest.mark.integration
def test_metadata_get_unknown_kb_rejected(monkeypatch):
    """M4.g: metadata/get against an unknown knCode returns 'knowledge base not found'."""
    with runtime(monkeypatch) as client:
        # Need a live runtime; the warmup KB inside runtime() initializes the schema.
        resp = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": "9999999", "filePath": "/whatever.md"},
        )
        assert resp.json()["resultCode"] == "-1"
        assert "knowledge base not found" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_metadata_get_unknown_file_rejected(monkeypatch):
    """M4.h: metadata/get against a known KB but absent filePath returns 'file not found'."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        resp = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": "/never_imported.md"},
        )
        assert resp.json()["resultCode"] == "-1"
        assert "file not found" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_metadata_get_returns_system_fields(monkeypatch):
    """M4.i: metadata/get returns system field values alongside user metadata."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client, file_path="/system_test.md")

        resp = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": file_path},
        )
        assert resp.json()["resultCode"] == "0"
        metadata = resp.json()["resultObject"]["metadata"]

        assert metadata["fileName"] == {
            "valueType": "string",
            "value": "system_test.md",
        }
        assert metadata["fileType"] == {"valueType": "string", "value": "md"}
        assert metadata["fileSize"]["valueType"] == "number"
        assert isinstance(metadata["fileSize"]["value"], int)
        assert metadata["mimeType"] == {"valueType": "string", "value": "text/markdown"}
        assert metadata["filePath"] == {
            "valueType": "string",
            "value": "/system_test.md",
        }
        assert metadata["createdAt"]["valueType"] == "datetime"
        assert metadata["updatedAt"]["valueType"] == "datetime"
        assert len(metadata) == 7


@pytest.mark.integration
def test_metadata_get_field_list_filters_system_fields(monkeypatch):
    """M4.j: metadata/get with metadataFieldList filters system and user fields."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client, file_path="/filtered.md")

        resp = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={
                "knCode": kb_code,
                "filePath": file_path,
                "metadataFieldList": ["fileName", "fileSize"],
            },
        )
        assert resp.json()["resultCode"] == "0"
        metadata = resp.json()["resultObject"]["metadata"]

        assert len(metadata) == 2
        assert "fileName" in metadata
        assert "fileSize" in metadata
        assert "fileType" not in metadata


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


# ===================================================================
# Section 6: YAML front matter auto metadata  (M6.a-M6.c)
# ===================================================================


def _import_md(client, kb_code, file_path, content: bytes):
    return client.post(
        "/api/v1/knowledgeItems/import",
        data={"knCode": kb_code, "filePath": file_path},
        files={"fileContent": (file_path.split("/")[-1], content, "text/markdown")},
    )


@pytest.mark.integration
def test_front_matter_auto_metadata(monkeypatch):
    """M6.a: registered front matter fields are auto-populated on import."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        prop = f"status_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        client.post(
            "/api/v1/directories/create",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        path = f"/docs/fm_{uuid4().hex[:6]}.md"
        body = f"---\n{prop}: active\n---\n# Hello\n".encode()
        resp = _import_md(client, kb_code, path, body)
        assert resp.json()["resultCode"] == "0", resp.text

        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": path},
        ).json()["resultObject"]["metadata"]
        assert got[prop]["value"] == "active"


@pytest.mark.integration
def test_front_matter_undefined_rejected(monkeypatch):
    """M6.b: front matter referring to an unregistered field rejects import."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        client.post(
            "/api/v1/directories/create",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        path = f"/docs/fm_{uuid4().hex[:6]}.md"
        body = b"---\nundefined_xyz: 1\n---\n# Hello\n"
        resp = _import_md(client, kb_code, path, body)
        assert resp.json()["resultCode"] == "-1"
        assert "not a defined metadata property" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_front_matter_multi_type(monkeypatch):
    """M6.c: front matter populates string / number / stringList in one shot."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        client.post(
            "/api/v1/directories/create",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        s = f"s_{uuid4().hex[:6]}"
        n = f"n_{uuid4().hex[:6]}"
        lst = f"l_{uuid4().hex[:6]}"
        register_property(client, s, "string")
        register_property(client, n, "number")
        register_property(client, lst, "stringList")
        path = f"/docs/fm_{uuid4().hex[:6]}.md"
        body = (
            f"---\n{s}: active\n{n}: 7\n{lst}:\n  - hr\n  - contract\n---\n# Hello\n"
        ).encode()
        resp = _import_md(client, kb_code, path, body)
        assert resp.json()["resultCode"] == "0", resp.text
        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": path},
        ).json()["resultObject"]["metadata"]
        assert got[s]["value"] == "active"
        assert got[n]["value"] == 7
        assert got[lst]["value"] == ["hr", "contract"]


@pytest.mark.integration
def test_front_matter_absent_imports_clean(monkeypatch):
    """M6.d: a markdown file without any YAML front matter imports successfully and has no metadata.

    Locks down the fail-soft contract: the import endpoint must NOT reject a
    file just because it lacks `---` fences.  The vast majority of real .md
    files have no front matter; treating absence as an error would break them.
    """
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        client.post(
            "/api/v1/directories/create",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        path = f"/docs/plain_{uuid4().hex[:6]}.md"
        body = b"# Hello\n\nJust some markdown.\n"
        resp = _import_md(client, kb_code, path, body)
        assert resp.json()["resultCode"] == "0", resp.text

        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": path},
        ).json()["resultObject"]["metadata"]
        assert set(got.keys()) == {
            "fileName",
            "fileType",
            "fileSize",
            "mimeType",
            "createdAt",
            "updatedAt",
            "filePath",
        }


@pytest.mark.integration
@pytest.mark.parametrize(
    "label, body",
    [
        pytest.param(
            "no_closing_fence",
            b"---\nstatus: active\n# Hello\n",
            id="no_closing_fence",
        ),
        pytest.param(
            "yaml_syntax_error",
            b"---\nstatus: : :\n---\n# Hello\n",
            id="yaml_syntax_error",
        ),
        pytest.param(
            "yaml_top_level_list",
            b"---\n- a\n- b\n---\n# Hello\n",
            id="yaml_top_level_list",
        ),
        pytest.param(
            "yaml_top_level_scalar",
            b"---\njust a string\n---\n# Hello\n",
            id="yaml_top_level_scalar",
        ),
    ],
)
def test_front_matter_malformed_imports_clean(monkeypatch, label, body):
    """M6.e: malformed YAML front matter is treated as 'no front matter'.

    The parser silently returns an empty dict for: opening fence without close,
    YAML syntax errors, and YAML payloads that are not a top-level mapping.
    The import still succeeds, and the file has no auto-set metadata.
    """
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        client.post(
            "/api/v1/directories/create",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        path = f"/docs/bad_{label}_{uuid4().hex[:6]}.md"
        resp = _import_md(client, kb_code, path, body)
        assert resp.json()["resultCode"] == "0", resp.text

        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": path},
        ).json()["resultObject"]["metadata"]
        assert set(got.keys()) == {
            "fileName",
            "fileType",
            "fileSize",
            "mimeType",
            "createdAt",
            "updatedAt",
            "filePath",
        }


@pytest.mark.integration
def test_front_matter_string_list_null_round_trips_as_null(monkeypatch):
    """M6.f: stringList front matter set to null persists and reads back as null."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        client.post(
            "/api/v1/directories/create",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        lst = f"l_{uuid4().hex[:6]}"
        register_property(client, lst, "stringList")
        path = f"/docs/fm_null_{uuid4().hex[:6]}.md"
        body = f"---\n{lst}: null\n---\n# Hello\n".encode()

        resp = _import_md(client, kb_code, path, body)
        assert resp.json()["resultCode"] == "0", resp.text

        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": path},
        ).json()["resultObject"]["metadata"]
        assert got[lst] == {"valueType": "stringList", "value": None}


# ===================================================================
# Section 7: Cascade cleanup on delete  (M7.a-M7.c)
# ===================================================================


@pytest.mark.integration
def test_delete_file_clears_metadata(monkeypatch):
    """M7.a: knowledgeItems/delete soft-deletes the file's metadata values."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"d_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="active",
        )

        resp = client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": kb_code, "filePath": file_path},
        )
        assert resp.json()["resultCode"] == "0"

        # metadataSearch should not return the deleted file
        search = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {"eq": {"fieldName": prop, "value": "active"}},
                "topK": 10,
            },
        )
        paths = [h["filePath"] for h in search.json()["resultObject"]["data"]]
        assert file_path not in paths

        # metadata/get on a deleted file: file not found
        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": file_path},
        )
        assert got.json()["resultCode"] == "-1"
        assert "file not found" in got.json()["resultMsg"]


@pytest.mark.integration
def test_delete_directory_clears_metadata(monkeypatch):
    """M7.b: directories/delete cascades metadata cleanup to all subtree files."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        client.post(
            "/api/v1/directories/create",
            json={"knCode": kb_code, "directoryPath": "/A"},
        )
        client.post(
            "/api/v1/directories/create",
            json={"knCode": kb_code, "directoryPath": "/A/B"},
        )
        prop = f"d_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        for path in ["/A/x.md", "/A/B/y.md"]:
            _upload_file(
                client, kb_code=kb_code, file_path=path, file_content=b"# F\nbody."
            )
            set_metadata(
                client,
                kb_code=kb_code,
                file_path=path,
                property_name=prop,
                value="active",
            )

        resp = client.post(
            "/api/v1/directories/delete",
            json={"knCode": kb_code, "directoryPath": "/A"},
        )
        assert resp.json()["resultCode"] == "0"

        search = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {"eq": {"fieldName": prop, "value": "active"}},
                "topK": 10,
            },
        )
        paths = [h["filePath"] for h in search.json()["resultObject"]["data"]]
        assert "/A/x.md" not in paths
        assert "/A/B/y.md" not in paths

        fields = client.post(
            "/api/v1/knowledgeItems/metadataFields/list",
            json={"knCodeList": [kb_code]},
        ).json()["resultObject"]["data"]
        assert all(field["propertyName"] != prop for field in fields)


@pytest.mark.integration
def test_delete_kb_clears_metadata(monkeypatch):
    """M7.c: knowledgeBases/delete soft-deletes every metadata value in the KB."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"d_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="active",
        )

        resp = client.post(
            "/api/v1/knowledgeBases/delete",
            json={"knCode": kb_code},
        )
        assert resp.json()["resultCode"] == "0"

        # metadataFields/list against a deleted KB: KB not found
        fields = client.post(
            "/api/v1/knowledgeItems/metadataFields/list",
            json={"knCodeList": [kb_code]},
        )
        assert fields.json()["resultCode"] == "-1"
        assert "knowledge base not found" in fields.json()["resultMsg"]


@pytest.mark.integration
def test_metadata_fields_list_kn_code_list_required(monkeypatch):
    """M7.d: metadataFields/list rejects requests that omit or empty knCodeList."""
    with runtime(monkeypatch) as client:
        # 1) omitted -> documented validation envelope
        resp_missing = client.post(
            "/api/v1/knowledgeItems/metadataFields/list",
            json={},
        )
        assert resp_missing.status_code == 200
        assert resp_missing.json()["resultCode"] == "-1"
        assert "request validation failed" in resp_missing.json()["resultMsg"]

        # 2) empty list -> same envelope
        resp_empty = client.post(
            "/api/v1/knowledgeItems/metadataFields/list",
            json={"knCodeList": []},
        )
        assert resp_empty.status_code == 200
        assert resp_empty.json()["resultCode"] == "-1"
        assert "request validation failed" in resp_empty.json()["resultMsg"]


@pytest.mark.integration
def test_metadata_fields_list_aggregates_across_kbs(monkeypatch):
    """M7.e: metadataFields/list with multiple knCodes returns the union of properties used."""
    with runtime(monkeypatch) as client:
        kb_a, fa = new_kb_with_file(client, file_path="/a.md")
        kb_b, fb = new_kb_with_file(client, file_path="/b.md")
        prop_x = f"x_{uuid4().hex[:6]}"
        prop_y = f"y_{uuid4().hex[:6]}"
        register_property(client, prop_x, "string")
        register_property(client, prop_y, "string")
        set_metadata(
            client, kb_code=kb_a, file_path=fa, property_name=prop_x, value="v"
        )
        set_metadata(
            client, kb_code=kb_b, file_path=fb, property_name=prop_y, value="v"
        )

        listed = client.post(
            "/api/v1/knowledgeItems/metadataFields/list",
            json={"knCodeList": [kb_a, kb_b]},
        ).json()["resultObject"]["data"]
        names = {f["propertyName"] for f in listed}
        assert {prop_x, prop_y}.issubset(names)


@pytest.mark.integration
def test_metadata_fields_list_kb_scope_isolation(monkeypatch):
    """M7.f: metadataFields/list scoped to one KB excludes properties only used elsewhere."""
    with runtime(monkeypatch) as client:
        kb_a, fa = new_kb_with_file(client, file_path="/a.md")
        kb_b, fb = new_kb_with_file(client, file_path="/b.md")
        prop_x = f"x_{uuid4().hex[:6]}"
        prop_y = f"y_{uuid4().hex[:6]}"
        register_property(client, prop_x, "string")
        register_property(client, prop_y, "string")
        set_metadata(
            client, kb_code=kb_a, file_path=fa, property_name=prop_x, value="v"
        )
        set_metadata(
            client, kb_code=kb_b, file_path=fb, property_name=prop_y, value="v"
        )

        listed_a = client.post(
            "/api/v1/knowledgeItems/metadataFields/list",
            json={"knCodeList": [kb_a]},
        ).json()["resultObject"]["data"]
        names_a = {f["propertyName"] for f in listed_a}
        assert prop_x in names_a
        assert prop_y not in names_a


@pytest.mark.integration
def test_metadata_fields_list_always_returns_system_fields(monkeypatch):
    """M7.g: metadataFields/list always appends 7 system field definitions."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)

        resp = client.post(
            "/api/v1/knowledgeItems/metadataFields/list",
            json={"knCodeList": [kb_code]},
        )
        assert resp.json()["resultCode"] == "0"
        data = resp.json()["resultObject"]["data"]

        system_names = [
            "fileName",
            "fileType",
            "fileSize",
            "mimeType",
            "createdAt",
            "updatedAt",
            "filePath",
        ]
        for i, name in enumerate(system_names):
            field = data[-(7 - i)]
            assert field["propertyName"] == name
            assert field["valueType"] in {"string", "number", "datetime"}
            assert isinstance(field.get("description"), str)
            assert field.get("extParams") is None


# ===================================================================
# Section 8: metadataSearch endpoint constraints  (M8.a-M8.h)
# ===================================================================


@pytest.mark.integration
def test_metadata_search_where_required(monkeypatch):
    """M8.a: omitting `where` is rejected via the documented envelope."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={"knCodeList": [kb_code], "topK": 10},
        )
        # Routes normalize Pydantic ValidationError into the documented envelope:
        # HTTP 200 + resultCode="-1" + resultMsg="request validation failed".
        assert resp.status_code == 200
        assert resp.json()["resultCode"] == "-1"
        assert "request validation failed" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_metadata_search_where_empty_object_rejected(monkeypatch):
    """M8.b: where={} fails DSL structural validation (must have one operator key)."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={"knCodeList": [kb_code], "where": {}, "topK": 10},
        )
        body = resp.json()
        assert body["resultCode"] == "-1"
        assert body["resultObject"]["errorCode"] == "DSL_VALIDATION_ERROR"
        codes = [e["code"] for e in body["resultObject"]["errorList"]]
        assert "INVALID_BOOLEAN_NODE" in codes


@pytest.mark.integration
def test_metadata_search_top_k_default_500(monkeypatch):
    """M8.c: topK defaults to 500 when omitted (asserted by request acceptance)."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"s_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client, kb_code=kb_code, file_path=file_path, property_name=prop, value="x"
        )
        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={"knCodeList": [kb_code], "where": {"exists": {"fieldName": prop}}},
        )
        assert resp.json()["resultCode"] == "0"
        # Generating 700 files just to prove 500 default is overkill; the contract
        # assertion is that omitting topK is accepted (default applies internally).
        assert len(resp.json()["resultObject"]["data"]) >= 1


@pytest.mark.integration
def test_metadata_search_top_k_max_10000(monkeypatch):
    """M8.d: topK > 10000 is rejected by schema validation; 10000 is accepted."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {"exists": {"fieldName": "fileName"}},
                "topK": 10001,
            },
        )
        # Routes normalize Pydantic ValidationError into the documented envelope.
        assert resp.status_code == 200
        assert resp.json()["resultCode"] == "-1"
        assert "request validation failed" in resp.json()["resultMsg"]
        # Boundary still accepted
        resp_ok = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {"exists": {"fieldName": "fileName"}},
                "topK": 10000,
            },
        )
        assert resp_ok.status_code == 200
        assert resp_ok.json()["resultCode"] == "0"


@pytest.mark.integration
@pytest.mark.parametrize("bad", [0, -1])
def test_metadata_search_top_k_zero_or_negative_rejected(monkeypatch, bad):
    """M8.e: topK <= 0 is rejected (Pydantic post-validator)."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {"exists": {"fieldName": "fileName"}},
                "topK": bad,
            },
        )
        # Routes normalize Pydantic ValidationError into the documented envelope.
        assert resp.status_code == 200
        assert resp.json()["resultCode"] == "-1"
        assert "request validation failed" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_metadata_search_kb_scope(monkeypatch):
    """M8.f: knCodeList narrows the search to a single KB even when other KBs match."""
    with runtime(monkeypatch) as client:
        kb_a, fa = new_kb_with_file(client, file_path="/a.md")
        kb_b, fb = new_kb_with_file(client, file_path="/b.md")
        prop = f"s_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(client, kb_code=kb_a, file_path=fa, property_name=prop, value="x")
        set_metadata(client, kb_code=kb_b, file_path=fb, property_name=prop, value="x")
        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_a],
                "where": {"eq": {"fieldName": prop, "value": "x"}},
                "topK": 10,
            },
        )
        kbs = {h["knCode"] for h in resp.json()["resultObject"]["data"]}
        assert kbs == {kb_a}


@pytest.mark.integration
def test_metadata_search_unknown_kb_rejected(monkeypatch):
    """M8.g: unknown knCode in knCodeList yields KnowledgeBaseValidationError."""
    with runtime(monkeypatch) as client:
        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": ["does_not_exist_99999"],
                "where": {"exists": {"fieldName": "fileName"}},
                "topK": 10,
            },
        )
        assert resp.json()["resultCode"] == "-1"
        assert "knowledge base not found" in resp.json()["resultMsg"]


@pytest.mark.integration
def test_metadata_search_metadata_field_list_filters_response(monkeypatch):
    """M8.h: metadataFieldList trims the per-hit metadata to listed fields only."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        keep = f"k_{uuid4().hex[:6]}"
        drop = f"d_{uuid4().hex[:6]}"
        register_property(client, keep, "string")
        register_property(client, drop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=keep,
            value="yes",
        )
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=drop,
            value="yes",
        )

        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {"eq": {"fieldName": keep, "value": "yes"}},
                "metadataFieldList": [keep],
                "topK": 10,
            },
        )
        hit = next(
            h for h in resp.json()["resultObject"]["data"] if h["filePath"] == file_path
        )
        assert keep in hit["metadata"]
        assert drop not in (hit["metadata"] or {})


@pytest.mark.integration
def test_metadata_search_kn_code_list_required(monkeypatch):
    """M8.i: metadataSearch rejects requests that omit or empty knCodeList."""
    with runtime(monkeypatch) as client:
        new_kb(client)
        resp_missing = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={"where": {"exists": {"fieldName": "fileName"}}, "topK": 5},
        )
        assert resp_missing.status_code == 200
        assert resp_missing.json()["resultCode"] == "-1"
        assert "request validation failed" in resp_missing.json()["resultMsg"]

        resp_empty = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [],
                "where": {"exists": {"fieldName": "fileName"}},
                "topK": 5,
            },
        )
        assert resp_empty.status_code == 200
        assert resp_empty.json()["resultCode"] == "-1"
        assert "request validation failed" in resp_empty.json()["resultMsg"]


# ===================================================================
# Section 9: DSL operator matrix  (M9.*)
#
# These tests share one DslDataset (6 files, 5 properties).  Each
# case-id maps 1:1 to a row in api-integration-test-plan.md.
# ===================================================================


@pytest.fixture
def dsl(monkeypatch):
    with runtime(monkeypatch) as client:
        ds = build_dsl_dataset(client)
        yield client, ds


def _leaf_cases(props):
    return [
        pytest.param(
            {"eq": {"fieldName": props.status, "value": "active"}},
            {"/dsl/F1.md", "/dsl/F2.md", "/dsl/F5.pdf"},
            id="eq",
        ),
        pytest.param(
            {"ne": {"fieldName": props.status, "value": "active"}},
            {"/dsl/F3.md", "/dsl/F4.md"},
            id="ne",
        ),
        pytest.param(
            {"in": {"fieldName": props.status, "value": ["active", "pending"]}},
            {"/dsl/F1.md", "/dsl/F2.md", "/dsl/F3.md", "/dsl/F5.pdf"},
            id="in",
        ),
        pytest.param(
            {"contains": {"fieldName": props.tags, "value": "contract"}},
            {"/dsl/F2.md", "/dsl/F3.md"},
            id="contains",
        ),
        pytest.param(
            {"exists": {"fieldName": props.archived}},
            {"/dsl/F1.md", "/dsl/F2.md", "/dsl/F3.md", "/dsl/F4.md", "/dsl/F5.pdf"},
            id="exists",
        ),
        pytest.param(
            {"gt": {"fieldName": props.priority, "value": 5}},
            {"/dsl/F4.md"},
            id="gt",
        ),
        pytest.param(
            {"gte": {"fieldName": props.priority, "value": 5}},
            {"/dsl/F2.md", "/dsl/F3.md", "/dsl/F4.md"},
            id="gte",
        ),
        pytest.param(
            {"lt": {"fieldName": props.priority, "value": 5}},
            {"/dsl/F1.md", "/dsl/F5.pdf"},
            id="lt",
        ),
        pytest.param(
            {"lte": {"fieldName": props.priority, "value": 5}},
            {"/dsl/F1.md", "/dsl/F2.md", "/dsl/F3.md", "/dsl/F5.pdf"},
            id="lte",
        ),
        pytest.param(
            {"gt": {"fieldName": props.published_at, "value": "2026-02-01T00:00:00Z"}},
            {"/dsl/F2.md", "/dsl/F3.md"},
            id="gt_datetime",
        ),
        pytest.param(
            {"prefix": {"fieldName": props.status, "value": "act"}},
            {"/dsl/F1.md", "/dsl/F2.md", "/dsl/F5.pdf"},
            id="prefix",
        ),
        pytest.param(
            {"wildcard": {"fieldName": props.status, "value": "act*"}},
            {"/dsl/F1.md", "/dsl/F2.md", "/dsl/F5.pdf"},
            id="wildcard",
        ),
    ]


@pytest.mark.integration
def test_dsl_operator(dsl):  # pylint: disable=redefined-outer-name
    """M9.eq..M9.gt-dt: each leaf operator returns the expected file set.

    pytest.parametrize cannot drive over a fixture-derived value, so we
    iterate the cases manually.  Each pytest.param's `id` preserves the
    M9.* sub-id in failure messages.
    """
    client, ds = dsl
    for param in _leaf_cases(ds.props):
        where, expected = param.values
        hit_paths = set(
            metadata_search_paths(
                client,
                kb_code=ds.kb_code,
                where=where,
                top_k=50,
            )
        )
        assert hit_paths == expected, f"case={param.id} got={hit_paths}"


@pytest.mark.integration
def test_dsl_boolean(dsl):  # pylint: disable=redefined-outer-name
    """M9.and..M9.nest3: boolean operators (and/or/not) and depth=3 nesting."""
    client, ds = dsl
    p = ds.props
    cases = [
        pytest.param(
            {
                "and": [
                    {"eq": {"fieldName": p.status, "value": "active"}},
                    {"contains": {"fieldName": p.tags, "value": "contract"}},
                ]
            },
            {"/dsl/F2.md"},
            id="and_flat",
        ),
        pytest.param(
            {
                "or": [
                    {"eq": {"fieldName": p.status, "value": "active"}},
                    {"eq": {"fieldName": p.status, "value": "pending"}},
                ]
            },
            {"/dsl/F1.md", "/dsl/F2.md", "/dsl/F3.md", "/dsl/F5.pdf"},
            id="or_flat",
        ),
        pytest.param(
            # Compiler emits NOT (EXISTS ... mv match).  For files that have
            # NO status set (F6), the inner EXISTS is FALSE so NOT-EXISTS is
            # TRUE → F6 is included.  Encode that semantics here.  If the
            # implementation later changes to "NOT must keep only files
            # where the property is set", update the expected set together.
            {"not": {"eq": {"fieldName": p.status, "value": "archived"}}},
            {"/dsl/F1.md", "/dsl/F2.md", "/dsl/F3.md", "/dsl/F5.pdf", "/dsl/F6.md"},
            id="not_leaf",
        ),
        pytest.param(
            {
                "and": [
                    {
                        "or": [
                            {"eq": {"fieldName": p.status, "value": "active"}},
                            {"eq": {"fieldName": p.status, "value": "pending"}},
                        ]
                    },
                    {"gt": {"fieldName": p.priority, "value": 3}},
                ]
            },
            {"/dsl/F2.md", "/dsl/F3.md"},
            id="nest_and_or_leaf",
        ),
        pytest.param(
            {
                "or": [
                    {"not": {"exists": {"fieldName": p.archived}}},
                    {"eq": {"fieldName": p.status, "value": "active"}},
                ]
            },
            {"/dsl/F1.md", "/dsl/F2.md", "/dsl/F5.pdf", "/dsl/F6.md"},
            id="nest_or_not_leaf",
        ),
        pytest.param(
            {
                "and": [
                    {
                        "or": [
                            {
                                "and": [
                                    {
                                        "eq": {
                                            "fieldName": p.status,
                                            "value": "active",
                                        }
                                    },
                                    {
                                        "contains": {
                                            "fieldName": p.tags,
                                            "value": "hr",
                                        }
                                    },
                                ]
                            },
                        ]
                    },
                ]
            },
            {"/dsl/F1.md", "/dsl/F2.md", "/dsl/F5.pdf"},
            id="nest_three_depth",
        ),
    ]
    for case in cases:
        where, expected = case.values
        hit_paths = set(
            metadata_search_paths(
                client,
                kb_code=ds.kb_code,
                where=where,
                top_k=50,
            )
        )
        assert hit_paths == expected, f"case={case.id} got={hit_paths}"


@pytest.mark.integration
def test_dsl_boolean_demorgan_equivalence(dsl):  # pylint: disable=redefined-outer-name
    """M9.demor: NOT (A OR B) ≡ AND[NOT A, NOT B] over the dataset."""
    client, ds = dsl
    p = ds.props
    a = {"eq": {"fieldName": p.status, "value": "active"}}
    b = {"eq": {"fieldName": p.status, "value": "pending"}}

    not_or = {"not": {"or": [a, b]}}
    and_not = {"and": [{"not": a}, {"not": b}]}
    left = set(
        metadata_search_paths(
            client,
            kb_code=ds.kb_code,
            where=not_or,
            top_k=50,
        )
    )
    right = set(
        metadata_search_paths(
            client,
            kb_code=ds.kb_code,
            where=and_not,
            top_k=50,
        )
    )
    assert left == right


# ===================================================================
# Section 10-11: DSL validation errors  (M10.a-M10.o, M11.a-M11.c)
# ===================================================================


@pytest.fixture
def dsl_props(monkeypatch):
    """Lighter fixture: register the prop set without seeding files."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        ps = register_property_set(client)
        yield client, kb_code, ps


def _expect_dsl_error(client, kb_code, where, *, code, top_k=10):
    resp = client.post(
        "/api/v1/knowledgeItems/metadataSearch",
        json={"knCodeList": [kb_code], "where": where, "topK": top_k},
    )
    body = resp.json()
    assert body["resultCode"] == "-1", body
    assert body["resultObject"]["errorCode"] == "DSL_VALIDATION_ERROR", body
    codes = [e["code"] for e in body["resultObject"]["errorList"]]
    assert code in codes, f"expected {code} in {codes}"
    return body["resultObject"]["errorList"]


@pytest.mark.integration
def test_dsl_invalid_value_type(dsl_props):  # pylint: disable=redefined-outer-name
    """M10.a..M10.j: leaf-level type / shape errors map to INVALID_FIELD_VALUE_TYPE."""
    client, kb_code, p = dsl_props
    cases = [
        (
            "string_takes_number",
            {"eq": {"fieldName": p.status, "value": 1}},
        ),
        (
            "number_takes_string",
            {"eq": {"fieldName": p.priority, "value": "5"}},
        ),
        (
            "number_takes_bool",
            {"eq": {"fieldName": p.priority, "value": True}},
        ),
        (
            "datetime_bad_format",
            {"gt": {"fieldName": p.published_at, "value": "yesterday"}},
        ),
        (
            "exists_with_value",
            {"exists": {"fieldName": p.status, "value": "active"}},
        ),
        (
            "in_on_stringList",
            {"in": {"fieldName": p.tags, "value": ["hr"]}},
        ),
        (
            "contains_on_string",
            {"contains": {"fieldName": p.status, "value": "active"}},
        ),
        (
            "gt_on_string",
            {"gt": {"fieldName": p.status, "value": "active"}},
        ),
        (
            "in_empty_array",
            {"in": {"fieldName": p.status, "value": []}},
        ),
        (
            "in_mixed_types",
            {"in": {"fieldName": p.priority, "value": [1, "two"]}},
        ),
        (
            "prefix_on_numeric_field",
            {"prefix": {"fieldName": p.priority, "value": "1"}},
        ),
        (
            "wildcard_on_numeric_field",
            {"wildcard": {"fieldName": p.priority, "value": "1*"}},
        ),
    ]
    for case_id, where in cases:
        try:
            _expect_dsl_error(client, kb_code, where, code="INVALID_FIELD_VALUE_TYPE")
        except AssertionError as exc:
            raise AssertionError(f"case={case_id}: {exc}") from exc


@pytest.mark.integration
def test_dsl_structural(dsl_props):  # pylint: disable=redefined-outer-name
    """M10.k..M10.o: structural / unknown-operator / unknown-field errors."""
    client, kb_code, p = dsl_props
    cases = [
        (
            "multi_key_node",
            {
                "eq": {"fieldName": p.status, "value": "active"},
                "ne": {"fieldName": p.status, "value": "x"},
            },
            "INVALID_BOOLEAN_NODE",
        ),
        (
            "and_empty",
            {"and": []},
            "INVALID_BOOLEAN_NODE",
        ),
        (
            "not_array",
            {"not": [{"eq": {"fieldName": p.status, "value": "x"}}]},
            "INVALID_BOOLEAN_NODE",
        ),
        (
            "unsupported_operator",
            {"between": {"fieldName": p.priority, "value": [1, 5]}},
            "UNSUPPORTED_OPERATOR",
        ),
        (
            "unknown_field",
            {"eq": {"fieldName": "not_a_field", "value": "x"}},
            "UNKNOWN_FIELD",
        ),
    ]
    for case_id, where, code in cases:
        try:
            _expect_dsl_error(client, kb_code, where, code=code)
        except AssertionError as exc:
            raise AssertionError(f"case={case_id}: {exc}") from exc


@pytest.mark.integration
def test_dsl_too_deep(dsl_props):  # pylint: disable=redefined-outer-name
    """M11.a: nesting depth > 3 yields TOO_DEEP_BOOLEAN_NESTING."""
    client, kb_code, p = dsl_props
    where = {
        "and": [
            {
                "or": [
                    {
                        "and": [
                            {
                                "or": [
                                    {"eq": {"fieldName": p.status, "value": "active"}},
                                ]
                            },
                        ]
                    },
                ]
            },
        ]
    }
    _expect_dsl_error(client, kb_code, where, code="TOO_DEEP_BOOLEAN_NESTING")


@pytest.mark.integration
def test_dsl_too_many_conditions(dsl_props):  # pylint: disable=redefined-outer-name
    """M11.b: leaf condition count > 12 yields TOO_MANY_CONDITIONS."""
    client, kb_code, p = dsl_props
    where = {
        "and": [{"eq": {"fieldName": p.status, "value": str(i)}} for i in range(13)]
    }
    _expect_dsl_error(client, kb_code, where, code="TOO_MANY_CONDITIONS")


@pytest.mark.integration
def test_dsl_multiple_errors_aggregated(dsl_props):  # pylint: disable=redefined-outer-name
    """M11.c: a single request with two distinct issues returns both errors."""
    client, kb_code, p = dsl_props
    where = {
        "and": [
            {"eq": {"fieldName": "ghost", "value": "x"}},
            {"eq": {"fieldName": p.status, "value": 7}},
        ]
    }
    errors = _expect_dsl_error(client, kb_code, where, code="UNKNOWN_FIELD")
    codes = {e["code"] for e in errors}
    assert "INVALID_FIELD_VALUE_TYPE" in codes
    paths = [e["path"] for e in errors]
    assert any("[0]" in pp for pp in paths)
    assert any("[1]" in pp for pp in paths)


# ===================================================================
# Section 11: System fields in DSL  (M12.a-M12.e)
# ===================================================================


def _seed_mixed_kb(client):
    """KB with files of different extensions / sizes / dates for system-field tests."""
    kb_code = new_kb(client)
    files = {}
    for path, body in [
        ("/sm.md", b"# tiny\n"),  # ~7 bytes
        ("/lg.md", b"# big\n" + b"x" * 10_000),  # ~10006 bytes
        ("/doc.pdf", b"%PDF-1.4\n%fake\n"),
        ("/note.txt", b"plain text\n"),
    ]:
        _upload_file(client, kb_code=kb_code, file_path=path, file_content=body)
        files[path] = body
    return kb_code, files


@pytest.mark.integration
def test_system_field(monkeypatch):
    """M12.a..M12.d: in fileType / eq fileName / gt fileSize / gt createdAt all work."""
    with runtime(monkeypatch) as client:
        kb_code, _ = _seed_mixed_kb(client)

        # M12.a in fileType ["md","pdf"] → /sm.md, /lg.md, /doc.pdf
        paths = set(
            metadata_search_paths(
                client,
                kb_code=kb_code,
                where={"in": {"fieldName": "fileType", "value": ["md", "pdf"]}},
                top_k=20,
            )
        )
        assert paths == {"/sm.md", "/lg.md", "/doc.pdf"}

        # M12.b eq fileName "note.txt" → /note.txt
        paths = set(
            metadata_search_paths(
                client,
                kb_code=kb_code,
                where={"eq": {"fieldName": "fileName", "value": "note.txt"}},
                top_k=10,
            )
        )
        assert paths == {"/note.txt"}

        # M12.c gt fileSize 1000 → only /lg.md exceeds
        paths = set(
            metadata_search_paths(
                client,
                kb_code=kb_code,
                where={"gt": {"fieldName": "fileSize", "value": 1000}},
                top_k=10,
            )
        )
        assert paths == {"/lg.md"}

        # M12.d gt createdAt with a past date → all four files
        paths = set(
            metadata_search_paths(
                client,
                kb_code=kb_code,
                where={
                    "gt": {
                        "fieldName": "createdAt",
                        "value": "2000-01-01T00:00:00Z",
                    }
                },
                top_k=10,
            )
        )
        assert paths == {"/sm.md", "/lg.md", "/doc.pdf", "/note.txt"}


@pytest.mark.integration
def test_system_field_contains_rejected(monkeypatch):
    """M12.e: contains is invalid for system string fields (only stringList allows it)."""
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [kb_code],
                "where": {"contains": {"fieldName": "fileType", "value": "md"}},
                "topK": 5,
            },
        )
        body = resp.json()
        assert body["resultCode"] == "-1"
        codes = [e["code"] for e in body["resultObject"]["errorList"]]
        assert "INVALID_FIELD_VALUE_TYPE" in codes


@pytest.mark.integration
def test_metadata_search_custom_and_system_field_intersect(monkeypatch):
    """M12.f: metadataSearch combining custom + system fields applies both filters.

    Two .md files plus one .txt, custom `status` set on a subset.  The
    `and: [eq status active, in fileType [md]]` predicate must keep only
    the .md files whose status is `active`.
    """
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        prop = f"status_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        # Three files; only md_active satisfies BOTH constraints.
        md_active = "/md_active.md"
        md_archived = "/md_archived.md"
        txt_active = "/note_active.txt"
        for path in (md_active, md_archived, txt_active):
            _upload_file(client, kb_code=kb_code, file_path=path, file_content=b"# F\n")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=md_active,
            property_name=prop,
            value="active",
        )
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=md_archived,
            property_name=prop,
            value="archived",
        )
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=txt_active,
            property_name=prop,
            value="active",
        )

        paths = set(
            metadata_search_paths(
                client,
                kb_code=kb_code,
                where={
                    "and": [
                        {"eq": {"fieldName": prop, "value": "active"}},
                        {"in": {"fieldName": "fileType", "value": ["md"]}},
                    ]
                },
                top_k=20,
            )
        )
        assert paths == {md_active}


@pytest.mark.integration
def test_dsl_prefix_wildcard_on_system_field(monkeypatch):
    """M9.prefix-fn, M9.wildcard-fn: prefix/wildcard on fileName system field.

    prefix fileName "F" → all 6 files.
    wildcard fileName "F?.md" → F1.md..F6.md (F5.pdf excluded by ? matching one char).
    """
    with runtime(monkeypatch) as client:
        ds = build_dsl_dataset(client)

        # prefix: fileName starts with "F"
        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [ds.kb_code],
                "where": {"prefix": {"fieldName": "fileName", "value": "F"}},
                "topK": 20,
            },
        )
        assert resp.status_code == 200 and resp.json()["resultCode"] == "0"
        paths = {h["filePath"] for h in resp.json()["resultObject"]["data"]}
        assert paths == {
            "/dsl/F1.md",
            "/dsl/F2.md",
            "/dsl/F3.md",
            "/dsl/F4.md",
            "/dsl/F5.pdf",
            "/dsl/F6.md",
        }

        # wildcard: fileName matches "F?.*" → all files with 2-char name
        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [ds.kb_code],
                "where": {"wildcard": {"fieldName": "fileName", "value": "F?.*"}},
                "topK": 20,
            },
        )
        assert resp.status_code == 200 and resp.json()["resultCode"] == "0"
        paths = {h["filePath"] for h in resp.json()["resultObject"]["data"]}
        assert paths == {
            "/dsl/F1.md",
            "/dsl/F2.md",
            "/dsl/F3.md",
            "/dsl/F4.md",
            "/dsl/F5.pdf",
            "/dsl/F6.md",
        }

        # wildcard: narrow to *.md only
        resp = client.post(
            "/api/v1/knowledgeItems/metadataSearch",
            json={
                "knCodeList": [ds.kb_code],
                "where": {
                    "and": [
                        {"wildcard": {"fieldName": "fileName", "value": "F?.*"}},
                        {"eq": {"fieldName": "fileType", "value": "md"}},
                    ]
                },
                "topK": 20,
            },
        )
        assert resp.status_code == 200 and resp.json()["resultCode"] == "0"
        paths = {h["filePath"] for h in resp.json()["resultObject"]["data"]}
        assert paths == {
            "/dsl/F1.md",
            "/dsl/F2.md",
            "/dsl/F3.md",
            "/dsl/F4.md",
            "/dsl/F6.md",
        }
        assert "/dsl/F5.pdf" not in paths


# ===================================================================
# Section 11b: filePath system field  (M12.filePath-*)
# ===================================================================


@pytest.mark.integration
def test_file_path_eq_exact_match(monkeypatch):
    """M12.filePath-eq: eq filePath exact match returns single file."""
    with runtime(monkeypatch) as client:
        ds = build_filepath_dsl_dataset(client)

        paths = set(
            metadata_search_paths(
                client,
                kb_code=ds.kb_code,
                where={"eq": {"fieldName": "filePath", "value": "/dsl/F1.md"}},
                top_k=20,
            )
        )
        assert paths == {"/dsl/F1.md"}


@pytest.mark.integration
def test_file_path_prefix_directory(monkeypatch):
    """M12.filePath-prefix: prefix filePath matches all files under directory."""
    with runtime(monkeypatch) as client:
        ds = build_filepath_dsl_dataset(client)

        paths = set(
            metadata_search_paths(
                client,
                kb_code=ds.kb_code,
                where={"prefix": {"fieldName": "filePath", "value": "/dsl/"}},
                top_k=50,
            )
        )
        # All files under /dsl/ including nested
        assert "/dsl/F1.md" in paths
        assert "/dsl/F5.pdf" in paths
        assert "/dsl/F1.data/nested.txt" in paths
        # Files outside /dsl/ excluded
        assert "/other/G1.md" not in paths


@pytest.mark.integration
def test_file_path_wildcard_single_level(monkeypatch):
    """M12.filePath-wildcard: wildcard with ? matches single char, . is literal."""
    with runtime(monkeypatch) as client:
        ds = build_filepath_dsl_dataset(client)

        paths = set(
            metadata_search_paths(
                client,
                kb_code=ds.kb_code,
                where={"wildcard": {"fieldName": "filePath", "value": "/dsl/F?.md"}},
                top_k=20,
            )
        )
        # F + one char + .md
        assert "/dsl/F1.md" in paths
        assert "/dsl/F2.md" in paths
        assert "/dsl/F3.md" in paths
        assert "/dsl/F4.md" in paths
        assert "/dsl/F6.md" in paths
        # F5.pdf does not match *.md
        assert "/dsl/F5.pdf" not in paths
        # nested.txt does not match
        assert "/dsl/F1.data/nested.txt" not in paths


@pytest.mark.integration
def test_file_path_wildcard_star_penetrates_directory(monkeypatch):
    """M12.filePath-wildcard-penetrate: * matches / (ES wildcard semantics)."""
    with runtime(monkeypatch) as client:
        ds = build_filepath_dsl_dataset(client)

        paths = set(
            metadata_search_paths(
                client,
                kb_code=ds.kb_code,
                where={"wildcard": {"fieldName": "filePath", "value": "/dsl/F?.*"}},
                top_k=50,
            )
        )
        # F + one char + . + anything (including paths with /)
        assert "/dsl/F1.md" in paths
        assert "/dsl/F5.pdf" in paths
        # F1.data/nested.txt matches: /dsl/ + F + 1 + . + data/nested.txt (* penetrates /)
        assert "/dsl/F1.data/nested.txt" in paths


@pytest.mark.integration
def test_file_path_only_returns_files_not_directories(monkeypatch):
    """metadataSearch with filePath prefix returns only FILE entries."""
    with runtime(monkeypatch) as client:
        ds = build_filepath_dsl_dataset(client)

        paths = set(
            metadata_search_paths(
                client,
                kb_code=ds.kb_code,
                where={"prefix": {"fieldName": "filePath", "value": "/"}},
                top_k=100,
            )
        )
        # All returned items should be files (have file extensions)
        for p in paths:
            filename = p.split("/")[-1]
            assert "." in filename, f"Expected file, got directory-like path: {p}"


@pytest.mark.integration
def test_file_path_wildcard_no_match(monkeypatch):
    """wildcard filePath with non-existent prefix returns empty."""
    with runtime(monkeypatch) as client:
        ds = build_filepath_dsl_dataset(client)

        paths = metadata_search_paths(
            client,
            kb_code=ds.kb_code,
            where={"wildcard": {"fieldName": "filePath", "value": "/nonexistent/X*"}},
            top_k=20,
        )
        assert len(paths) == 0


# ===================================================================
# Section 11c: virtual_path maintenance correctness
# ===================================================================


@pytest.mark.integration
def test_virtual_path_set_on_file_create(monkeypatch):
    """virtual_path is correctly set when creating files via API."""
    with runtime(monkeypatch) as client:
        ds = build_dsl_dataset(client)

        paths = set(
            metadata_search_paths(
                client,
                kb_code=ds.kb_code,
                where={"eq": {"fieldName": "filePath", "value": "/dsl/F1.md"}},
                top_k=5,
            )
        )
        assert paths == {"/dsl/F1.md"}


@pytest.mark.integration
def test_virtual_path_updated_on_rename(monkeypatch):
    """virtual_path updates for entry and descendants after directory rename."""
    with runtime(monkeypatch) as client:
        ds = build_filepath_dsl_dataset(client)

        # Rename /dsl → /renamed via directories/update API
        resp = client.post(
            "/api/v1/directories/update",
            json={
                "knCode": ds.kb_code,
                "directoryPath": "/dsl",
                "directoryName": "renamed",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["resultCode"] == "0", resp.text

        # Files should now be under /renamed/
        paths = set(
            metadata_search_paths(
                client,
                kb_code=ds.kb_code,
                where={"prefix": {"fieldName": "filePath", "value": "/renamed/"}},
                top_k=50,
            )
        )
        assert "/renamed/F1.md" in paths
        assert "/renamed/F5.pdf" in paths
        assert "/renamed/F1.data/nested.txt" in paths

        # Old /dsl/ path should return nothing
        old_paths = metadata_search_paths(
            client,
            kb_code=ds.kb_code,
            where={"prefix": {"fieldName": "filePath", "value": "/dsl/"}},
            top_k=50,
        )
        assert len(old_paths) == 0


# ===================================================================
# Section 12: knowledgeItems/search with DSL  (M13.a-M13.g)
# ===================================================================


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["fullTextRecall", "embedding", "mixedRecall"])
def test_search_modes_with_where(monkeypatch, mode):
    """M13.a: each searchMode honors the DSL `where` filter."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_built_file(client)
        prop = f"s_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="active",
        )

        resp = chunk_search(
            client,
            kb_code=kb_code,
            query="续签",
            mode=mode,
            where={"eq": {"fieldName": prop, "value": "active"}},
        )
        assert resp.status_code == 200
        data = resp.json()["resultObject"]["data"]
        assert any(h["filePath"] == file_path for h in data)


@pytest.mark.integration
def test_search_metadata_excluded_by_default(monkeypatch):
    """M13.b: when metadataFieldList is omitted, hits do not carry metadata."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_built_file(client)
        prop = f"s_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="active",
        )

        resp = chunk_search(client, kb_code=kb_code, query="续签")
        for hit in resp.json()["resultObject"]["data"]:
            assert hit["metadata"] is None


@pytest.mark.integration
def test_search_metadata_field_list_filters_response(monkeypatch):
    """M13.c: metadataFieldList limits returned per-hit metadata to listed fields."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_built_file(client)
        keep = f"keep_{uuid4().hex[:6]}"
        drop = f"drop_{uuid4().hex[:6]}"
        register_property(client, keep, "string")
        register_property(client, drop, "string")
        set_metadata(
            client, kb_code=kb_code, file_path=file_path, property_name=keep, value="y"
        )
        set_metadata(
            client, kb_code=kb_code, file_path=file_path, property_name=drop, value="y"
        )
        resp = chunk_search(
            client, kb_code=kb_code, query="续签", metadata_field_list=[keep]
        )
        hits = [
            h for h in resp.json()["resultObject"]["data"] if h["filePath"] == file_path
        ]
        assert hits and keep in hits[0]["metadata"]
        assert drop not in hits[0]["metadata"]


@pytest.mark.integration
def test_search_where_empty_result(monkeypatch):
    """M13.d: a non-matching `where` short-circuits to zero hits."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_built_file(client)
        prop = f"s_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="active",
        )

        resp = chunk_search(
            client,
            kb_code=kb_code,
            query="续签",
            where={"eq": {"fieldName": prop, "value": "archived"}},
        )
        assert resp.json()["resultObject"]["data"] == []


@pytest.mark.integration
@pytest.mark.parametrize("top_k", [0, -1])
def test_search_top_k_bounds_rejected(monkeypatch, top_k):
    """M13.e: topK<=0 is rejected; missing topK also rejected."""
    with runtime(monkeypatch) as client:
        kb_code, _ = new_kb_with_built_file(client)
        resp = chunk_search(client, kb_code=kb_code, query="x", top_k=top_k)
        # Documented envelope: HTTP 200 + resultCode="-1" + "request validation failed".
        assert resp.status_code == 200
        assert resp.json()["resultCode"] == "-1"
        assert "request validation failed" in resp.json()["resultMsg"]
        # missing topK
        resp_missing = client.post(
            "/api/v1/knowledgeItems/search",
            json={"query": "x", "knCodeList": [kb_code], "searchMode": "mixedRecall"},
        )
        assert resp_missing.status_code == 200
        assert resp_missing.json()["resultCode"] == "-1"


@pytest.mark.integration
def test_search_where_system_field_file_type(monkeypatch):
    """M13.f: chunk search filters by `where: in fileType [...]` (system field).

    The PDF is uploaded but NOT indexed (fileToMarkdownIndex is not called for
    it) — it has no chunks, so it can never appear in chunk search results
    regardless of the filter.  The assertion confirms the md filter returns the
    md file and that the pdf path is absent (it has no chunks to return).
    """
    with runtime(monkeypatch) as client:
        kb_code, md_path = new_kb_with_built_file(client)
        pdf_path = "/制度/续签流程.pdf"
        _upload_file(
            client, kb_code=kb_code, file_path=pdf_path, file_content=b"%PDF-1.4\n"
        )
        # Do NOT call fileToMarkdownIndex for the fake PDF — PyMuPDF cannot
        # parse a stub PDF and would raise FzErrorFormat.  The file exists in
        # the KB as a file entry but has no chunks, so it cannot appear in any
        # chunk search result.

        resp = chunk_search(
            client,
            kb_code=kb_code,
            query="续签",
            where={"in": {"fieldName": "fileType", "value": ["md"]}},
            top_k=10,
        )
        paths = [h["filePath"] for h in resp.json()["resultObject"]["data"]]
        assert md_path in paths
        assert pdf_path not in paths


@pytest.mark.integration
def test_search_where_custom_and_system_field_intersect(monkeypatch):
    """M13.g: chunk search where with `and: [custom, system]` applies both filters."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_built_file(client)
        prop = f"s_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="active",
        )

        resp = chunk_search(
            client,
            kb_code=kb_code,
            query="续签",
            where={
                "and": [
                    {"eq": {"fieldName": prop, "value": "active"}},
                    {"gt": {"fieldName": "fileSize", "value": 1}},
                ]
            },
            top_k=10,
        )
        paths = [h["filePath"] for h in resp.json()["resultObject"]["data"]]
        assert file_path in paths

        # Negative: tighter system filter -> AND unsat -> empty result
        resp_empty = chunk_search(
            client,
            kb_code=kb_code,
            query="续签",
            where={
                "and": [
                    {"eq": {"fieldName": prop, "value": "active"}},
                    {"gt": {"fieldName": "fileSize", "value": 10**9}},
                ]
            },
            top_k=10,
        )
        assert resp_empty.json()["resultObject"]["data"] == []


@pytest.mark.integration
def test_search_where_drives_recall_not_postfilter(monkeypatch):
    """M13.h: with `where` excluding the strongest hit, the next-best file rises.

    Distinguishes pre-filter (where_sql baked into recall SQL) from
    post-filter (drop-after-top-K).  Post-filter would leave top-1 empty.
    """
    with runtime(monkeypatch) as client:
        kb_code = new_kb(client)
        client.post(
            "/api/v1/directories/create",
            json={"knCode": kb_code, "directoryPath": "/docs"},
        )
        path_a = "/docs/A.md"
        path_b = "/docs/B.md"
        # A is a near-verbatim match for the query; B is a weaker but real
        # match.  Both share the noun "续签" so neither is filtered out by
        # vocabulary alone.
        _upload_file(
            client,
            kb_code=kb_code,
            file_path=path_a,
            file_content=(
                "# 续签流程\n"
                "续签流程是合同续签的核心步骤,续签需要业务负责人发起审批,"
                "再由人事确认续签条款。续签完成后系统会归档。\n"
            ).encode("utf-8"),
        )
        _upload_file(
            client,
            kb_code=kb_code,
            file_path=path_b,
            file_content=(
                "# 入职指南\n员工入职需要完成基本资料登记。续签问题请咨询人事。\n"
            ).encode("utf-8"),
        )
        for path in (path_a, path_b):
            resp = client.post(
                "/api/v1/fileToMarkdownIndex",
                json={"knCode": kb_code, "filePath": path},
            )
            assert resp.json()["resultCode"] == "0", resp.text
            wait_for_build(client, kb_code=kb_code, file_path=path)

        prop = f"status_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=path_a,
            property_name=prop,
            value="archived",
        )

        # Baseline: no where filter -> top1 should be A (closer match).
        resp_no_where = chunk_search(
            client,
            kb_code=kb_code,
            query="续签流程",
            top_k=1,
        )
        assert resp_no_where.json()["resultCode"] == "0"
        baseline = resp_no_where.json()["resultObject"]["data"]
        assert len(baseline) == 1, baseline
        assert baseline[0]["filePath"] == path_a, baseline

        # With `where` excluding A's status, B must rise into top-1.
        # If `where` were a post-filter, top-1 would already be A (which
        # the post-filter then drops), leaving an empty result.
        resp_with_where = chunk_search(
            client,
            kb_code=kb_code,
            query="续签流程",
            top_k=1,
            where={"not": {"eq": {"fieldName": prop, "value": "archived"}}},
        )
        assert resp_with_where.json()["resultCode"] == "0"
        promoted = resp_with_where.json()["resultObject"]["data"]
        assert len(promoted) == 1, (
            "Expected top-K to be re-populated with the next-best file "
            f"after `where` excludes A, got {promoted}.  Empty result "
            "would indicate post-filter behavior."
        )
        assert promoted[0]["filePath"] == path_b, promoted


# ===================================================================
# Section 13: fileTypeList legacy + searchFile  (M14.a-M14.b, M15.a-M15.e)
# ===================================================================


def _build_two_kinds(client) -> tuple[str, str, str]:
    """Create a KB with one .md + one .txt, both built. Returns (kb, md, txt)."""
    kb_code, md_path = new_kb_with_built_file(client)
    # Reuse the "/制度" directory created by new_kb_with_built_file.
    txt_path = "/制度/续签备注.txt"
    _upload_file(
        client,
        kb_code=kb_code,
        file_path=txt_path,
        file_content=b"continuance renewal continued contract.\n",
    )
    resp = client.post(
        "/api/v1/fileToMarkdownIndex",
        json={"knCode": kb_code, "filePath": txt_path},
    )
    assert resp.json()["resultCode"] == "0", resp.text
    wait_for_build(client, kb_code=kb_code, file_path=txt_path)
    return kb_code, md_path, txt_path


@pytest.mark.integration
def test_search_file_type_list_legacy(monkeypatch):
    """M14.a: fileTypeList alone works like {in fileType [...]}."""
    with runtime(monkeypatch) as client:
        kb_code, md_path, txt_path = _build_two_kinds(client)
        resp = chunk_search(
            client,
            kb_code=kb_code,
            query="续签",
            file_type_list=["md"],
            top_k=10,
        )
        paths = [h["filePath"] for h in resp.json()["resultObject"]["data"]]
        assert md_path in paths
        assert txt_path not in paths


@pytest.mark.integration
def test_search_file_type_list_intersect_where(monkeypatch):
    """M14.b: fileTypeList AND where(fileType) intersect; mismatched sets -> empty."""
    with runtime(monkeypatch) as client:
        kb_code = _build_two_kinds(client)[0]
        # md from fileTypeList ∩ txt from where → empty
        resp = chunk_search(
            client,
            kb_code=kb_code,
            query="续签",
            where={"in": {"fieldName": "fileType", "value": ["txt"]}},
            file_type_list=["md"],
            top_k=10,
        )
        assert resp.json()["resultObject"]["data"] == []


@pytest.mark.integration
def test_search_file_dedup_by_path(monkeypatch):
    """M15.a: searchFile aggregates multi-chunk hits to one filePath entry.

    Builds a markdown long enough to produce >=2 chunks (chunk_size=512 in
    the chunking service).  Verifies via chunk search that the file truly
    has multiple chunks recalled by the same query, then asserts that
    searchFile returns the file exactly once after aggregation.
    """
    long_paragraph = (
        "续签合同流程是合同续签的核心步骤,需要由业务负责人发起审批,人事确认续签条款。 "
        * 60
    )
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_built_file(
            client,
            markdown=f"# 续签流程\n\n{long_paragraph}\n",
        )

        # Pre-condition: chunk search must see at least 2 chunks of this file
        # for the dedup assertion below to be meaningful.
        chunk_resp = chunk_search(
            client,
            kb_code=kb_code,
            query="续签",
            top_k=20,
        )
        chunk_hits_for_file = [
            h
            for h in chunk_resp.json()["resultObject"]["data"]
            if h["filePath"] == file_path
        ]
        assert len(chunk_hits_for_file) >= 2, (
            f"setup failed: file produced only {len(chunk_hits_for_file)} "
            "chunk hit(s); the dedup test needs >=2 to be meaningful"
        )
        # Sanity: distinct chunk_no across the recalled chunks.
        assert len({h["chunkNo"] for h in chunk_hits_for_file}) >= 2

        # Actual claim: searchFile aggregates the multi-chunk recall to one entry.
        resp = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "topK": 10,
                "searchMode": "mixedRecall",
            },
        )
        paths = [h["filePath"] for h in resp.json()["resultObject"]["data"]]
        assert paths.count(file_path) == 1, (
            f"expected exactly one searchFile hit for {file_path}, got "
            f"{paths.count(file_path)} (paths={paths})"
        )


@pytest.mark.integration
def test_search_file_with_dsl_and_metadata(monkeypatch):
    """M15.b: searchFile honors `where` and returns metadata for listed fields."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_built_file(client)
        prop = f"s_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="active",
        )

        resp = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "topK": 10,
                "searchMode": "mixedRecall",
                "where": {"eq": {"fieldName": prop, "value": "active"}},
                "metadataFieldList": [prop],
            },
        )
        hits = [
            h for h in resp.json()["resultObject"]["data"] if h["filePath"] == file_path
        ]
        assert hits, resp.text
        assert hits[0]["metadata"][prop]["value"] == "active"


@pytest.mark.integration
def test_search_file_kn_code_list_required(monkeypatch):
    """M15.c: searchFile rejects requests that omit or empty knCodeList.

    Without a kb scope the underlying SQL would always return zero rows
    (kb_codes=[] makes `ANY(kb_codes)` false for every chunk), so the
    schema makes knCodeList required with min_length=1 and the route
    returns the documented validation envelope when callers omit it.
    """
    with runtime(monkeypatch) as client:
        # Existing KB just to ensure the schema check runs against a live runtime.
        new_kb_with_built_file(client)

        # 1) omitted knCodeList -> documented validation envelope.
        resp_missing = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={"query": "续签", "topK": 10, "searchMode": "mixedRecall"},
        )
        assert resp_missing.status_code == 200
        assert resp_missing.json()["resultCode"] == "-1"
        assert "request validation failed" in resp_missing.json()["resultMsg"]

        # 2) empty knCodeList -> same envelope.
        resp_empty = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={
                "query": "续签",
                "knCodeList": [],
                "topK": 10,
                "searchMode": "mixedRecall",
            },
        )
        assert resp_empty.status_code == 200
        assert resp_empty.json()["resultCode"] == "-1"
        assert "request validation failed" in resp_empty.json()["resultMsg"]


@pytest.mark.integration
def test_search_file_where_system_field_file_type(monkeypatch):
    """M15.d: searchFile filters by `where: in fileType [...]` (system field)."""
    with runtime(monkeypatch) as client:
        kb_code, md_path, txt_path = _build_two_kinds(client)
        resp = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "topK": 10,
                "searchMode": "mixedRecall",
                "where": {"in": {"fieldName": "fileType", "value": ["md", "txt"]}},
            },
        )
        paths = {h["filePath"] for h in resp.json()["resultObject"]["data"]}
        assert {md_path, txt_path}.issubset(paths)

        resp_only_txt = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "topK": 10,
                "searchMode": "mixedRecall",
                "where": {"in": {"fieldName": "fileType", "value": ["txt"]}},
            },
        )
        only_txt = {h["filePath"] for h in resp_only_txt.json()["resultObject"]["data"]}
        assert md_path not in only_txt
        assert txt_path in only_txt


@pytest.mark.integration
def test_search_file_where_system_field_created_at(monkeypatch):
    """M15.e: searchFile honors `gt createdAt <past_date>` system filter."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_built_file(client)
        resp_past = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "topK": 10,
                "searchMode": "mixedRecall",
                "where": {
                    "gt": {"fieldName": "createdAt", "value": "2000-01-01T00:00:00Z"}
                },
            },
        )
        paths = [h["filePath"] for h in resp_past.json()["resultObject"]["data"]]
        assert file_path in paths

        resp_future = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "topK": 10,
                "searchMode": "mixedRecall",
                "where": {
                    "gt": {"fieldName": "createdAt", "value": "2099-01-01T00:00:00Z"}
                },
            },
        )
        assert resp_future.json()["resultObject"]["data"] == []


@pytest.mark.integration
def test_search_file_custom_and_system_field_intersect(monkeypatch):
    """M15.f: searchFile with `and: [custom, system]` applies both filters.

    Build .md and .txt, set custom `status=active` only on the .md.
    `and: [eq status active, in fileType [md, txt]]` must keep only the
    .md.  Tightening to `[txt]` makes the AND unsatisfiable -> empty.
    """
    with runtime(monkeypatch) as client:
        kb_code, md_path, txt_path = _build_two_kinds(client)
        prop = f"status_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=md_path,
            property_name=prop,
            value="active",
        )
        # Note: txt_path intentionally has no status set.

        resp = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "topK": 10,
                "searchMode": "mixedRecall",
                "where": {
                    "and": [
                        {"eq": {"fieldName": prop, "value": "active"}},
                        {"in": {"fieldName": "fileType", "value": ["md", "txt"]}},
                    ]
                },
            },
        )
        paths = {h["filePath"] for h in resp.json()["resultObject"]["data"]}
        assert md_path in paths
        assert txt_path not in paths

        # Tightening fileType to txt makes the AND unsatisfiable.
        resp_empty = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "topK": 10,
                "searchMode": "mixedRecall",
                "where": {
                    "and": [
                        {"eq": {"fieldName": prop, "value": "active"}},
                        {"in": {"fieldName": "fileType", "value": ["txt"]}},
                    ]
                },
            },
        )
        assert resp_empty.json()["resultObject"]["data"] == []


# ===================================================================
# Section 14: cross-interface consistency + soft-delete isolation
#                (M16.a-M16.c, M17.a-M17.c)
# ===================================================================


@pytest.mark.integration
def test_consistency_update_search_get(monkeypatch):
    """M16.a: update -> metadataSearch -> metadata/get all agree on the same value.

    After unset, the file disappears from a search that was matching the
    unset value, and metadata/get no longer returns the property.
    """
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"c_{uuid4().hex[:6]}"
        register_property(client, prop, "string")

        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="active",
        )
        # search hits
        paths = metadata_search_paths(
            client,
            kb_code=kb_code,
            where={"eq": {"fieldName": prop, "value": "active"}},
        )
        assert file_path in paths
        # get returns same value
        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": file_path},
        ).json()["resultObject"]["metadata"]
        assert got[prop]["value"] == "active"

        # unset
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            operation="unset",
        )
        paths = metadata_search_paths(
            client,
            kb_code=kb_code,
            where={"eq": {"fieldName": prop, "value": "active"}},
        )
        assert file_path not in paths
        got = client.post(
            "/api/v1/knowledgeItems/metadata/get",
            json={"knCode": kb_code, "filePath": file_path},
        ).json()["resultObject"]["metadata"]
        assert prop not in got


@pytest.mark.integration
def test_consistency_fields_list_reflects_values(monkeypatch):
    """M16.b: a property only appears in metadataFields/list once it has a value."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        a = f"a_{uuid4().hex[:6]}"
        b = f"b_{uuid4().hex[:6]}"
        register_property(client, a, "string")
        register_property(client, b, "string")
        set_metadata(
            client, kb_code=kb_code, file_path=file_path, property_name=a, value="x"
        )

        listed = client.post(
            "/api/v1/knowledgeItems/metadataFields/list",
            json={"knCodeList": [kb_code]},
        ).json()["resultObject"]["data"]
        names = {f["propertyName"] for f in listed}
        assert a in names
        assert b not in names

        # After unset, a disappears too
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=a,
            operation="unset",
        )
        listed = client.post(
            "/api/v1/knowledgeItems/metadataFields/list",
            json={"knCodeList": [kb_code]},
        ).json()["resultObject"]["data"]
        names = {f["propertyName"] for f in listed}
        assert a not in names


@pytest.mark.integration
def test_consistency_clear_keeps_field(monkeypatch):
    """M16.c: clear leaves the value row in place, so metadataFields/list still lists it."""
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
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            operation="clear",
        )
        listed = client.post(
            "/api/v1/knowledgeItems/metadataFields/list",
            json={"knCodeList": [kb_code]},
        ).json()["resultObject"]["data"]
        assert prop in {f["propertyName"] for f in listed}


@pytest.mark.integration
def test_soft_delete_excluded_from_metadata_search(monkeypatch):
    """M17.a: a soft-deleted file is excluded from metadataSearch."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"s_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="active",
        )
        client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": kb_code, "filePath": file_path},
        )
        paths = metadata_search_paths(
            client,
            kb_code=kb_code,
            where={"eq": {"fieldName": prop, "value": "active"}},
        )
        assert file_path not in paths


@pytest.mark.integration
def test_soft_delete_excluded_from_chunk_and_file_search(monkeypatch):
    """M17.b: soft-deleted files do not appear in `search` or `searchFile`."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_built_file(client)
        client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": kb_code, "filePath": file_path},
        )

        chunk = chunk_search(client, kb_code=kb_code, query="续签").json()
        assert all(h["filePath"] != file_path for h in chunk["resultObject"]["data"])

        file_resp = client.post(
            "/api/v1/knowledgeItems/searchFile",
            json={
                "query": "续签",
                "knCodeList": [kb_code],
                "topK": 10,
                "searchMode": "mixedRecall",
            },
        ).json()
        assert all(
            h["filePath"] != file_path for h in file_resp["resultObject"]["data"]
        )


@pytest.mark.integration
def test_soft_delete_then_reimport_no_pollution(monkeypatch):
    """M17.c: re-importing the same path uses fresh metadata; old values stay hidden."""
    with runtime(monkeypatch) as client:
        kb_code, file_path = new_kb_with_file(client)
        prop = f"s_{uuid4().hex[:6]}"
        register_property(client, prop, "string")
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="old_value",
        )

        client.post(
            "/api/v1/knowledgeItems/delete",
            json={"knCode": kb_code, "filePath": file_path},
        )

        _upload_file(
            client, kb_code=kb_code, file_path=file_path, file_content=b"# T\nbody2"
        )
        set_metadata(
            client,
            kb_code=kb_code,
            file_path=file_path,
            property_name=prop,
            value="new_value",
        )

        # old value not findable
        old_paths = metadata_search_paths(
            client,
            kb_code=kb_code,
            where={"eq": {"fieldName": prop, "value": "old_value"}},
        )
        assert file_path not in old_paths
        # new value findable, exactly once
        new_paths = metadata_search_paths(
            client,
            kb_code=kb_code,
            where={"eq": {"fieldName": prop, "value": "new_value"}},
        )
        assert new_paths.count(file_path) == 1
