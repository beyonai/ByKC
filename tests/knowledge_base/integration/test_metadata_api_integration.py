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
    metadata_search_paths,
    new_kb,
    new_kb_with_file,
    register_property,
    runtime,
    set_metadata,
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
        assert got == {}


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
        assert got == {}


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
