"""Unit tests for MetadataFieldsListOperation in isolation (no HTTP)."""

from by_qa.qa.common.config import KnowledgeBaseConfig, QARetrievalConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.operation_registry import OperationType
from by_qa.qa.tools.operations.base import DispatchRequest
from by_qa.qa.tools.operations.metadata_fields_list import (
    MetadataFieldsListOperation,
    _format_metadata_field_item,
    _format_metadata_fields_api_error,
    _format_metadata_fields_error,
)


def _make_context(*kbs: KnowledgeBaseConfig) -> QARuntimeContext:
    return QARuntimeContext(retrieval=QARetrievalConfig(knowledge_bases=list(kbs)))


def _kb(kb_code: str, service: str, ops: dict) -> KnowledgeBaseConfig:
    return KnowledgeBaseConfig(
        kb_code=kb_code, kb_name=kb_code, service_name=service, operations=ops
    )


# ── Format helpers ─────────────────────────────────────────────────


def test_format_metadata_field_item_maps_camelcase():
    item = {
        "propertyName": "status",
        "valueType": "string",
        "description": "文档状态",
    }
    result = _format_metadata_field_item(item)
    assert result["property_name"] == "status"
    assert result["value_type"] == "string"
    assert result["description"] == "文档状态"
    assert result["source_type"] == "metadata_field"


def test_format_metadata_field_item_maps_snake_case():
    item = {
        "property_name": "priority",
        "value_type": "number",
        "description": "优先级",
    }
    result = _format_metadata_field_item(item)
    assert result["property_name"] == "priority"
    assert result["value_type"] == "number"
    assert result["description"] == "优先级"


def test_format_metadata_field_item_handles_missing_fields():
    result = _format_metadata_field_item({})
    assert result["property_name"] == ""
    assert result["value_type"] == ""
    assert result["description"] == ""


def test_format_metadata_fields_error_produces_entry():
    exc = RuntimeError("service unavailable")
    entry = _format_metadata_fields_error(service_name="svc", path="/api", exc=exc)
    assert entry["is_error"] is True
    assert entry["error"] == "service unavailable"
    assert entry["error_type"] == "RuntimeError"
    assert entry["service_name"] == "svc"
    assert entry["path"] == "/api"


def test_format_metadata_fields_api_error_produces_entry():
    entry = _format_metadata_fields_api_error(
        service_name="svc", path="/api", result_msg="knCodeList must not be empty"
    )
    assert entry["is_error"] is True
    assert entry["error"] == "knCodeList must not be empty"
    assert entry["error_type"] == "ApiError"
    assert entry["service_name"] == "svc"
    assert entry["path"] == "/api"


# ── build_requests ──────────────────────────────────────────────────


def test_build_requests_groups_by_service():
    kb_a1 = _kb(
        "kb1",
        "svc-a",
        {OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    kb_a2 = _kb(
        "kb2",
        "svc-a",
        {OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    kb_b = _kb(
        "kb3",
        "svc-b",
        {OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    ctx = _make_context(kb_a1, kb_a2, kb_b)
    op = MetadataFieldsListOperation()

    requests, errors = op.build_requests({}, [kb_a1, kb_a2, kb_b], ctx)

    assert errors == []
    assert len(requests) == 2
    service_names = {r.service_name for r in requests}
    assert service_names == {"svc-a", "svc-b"}
    svc_a_req = next(r for r in requests if r.service_name == "svc-a")
    assert set(svc_a_req.body["knCodeList"]) == {"kb1", "kb2"}
    svc_b_req = next(r for r in requests if r.service_name == "svc-b")
    assert svc_b_req.body["knCodeList"] == ["kb3"]


def test_build_requests_filters_by_kn_code_list():
    kb1 = _kb(
        "kb1",
        "svc-a",
        {OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    kb2 = _kb(
        "kb2",
        "svc-a",
        {OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    ctx = _make_context(kb1, kb2)
    op = MetadataFieldsListOperation()

    requests, errors = op.build_requests({"knCodeList": ["kb1"]}, [kb1, kb2], ctx)

    assert errors == []
    assert len(requests) == 1
    assert requests[0].body["knCodeList"] == ["kb1"]


def test_build_requests_queries_all_when_no_kn_code_list():
    kb1 = _kb(
        "kb1",
        "svc-a",
        {OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    kb2 = _kb(
        "kb2",
        "svc-a",
        {OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    ctx = _make_context(kb1, kb2)
    op = MetadataFieldsListOperation()

    requests, errors = op.build_requests({}, [kb1, kb2], ctx)

    assert errors == []
    assert len(requests) == 1
    assert set(requests[0].body["knCodeList"]) == {"kb1", "kb2"}


def test_build_requests_reports_unauthorized_kb_codes():
    kb = _kb(
        "kb1",
        "svc-a",
        {OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    ctx = _make_context(kb)
    op = MetadataFieldsListOperation()

    requests, errors = op.build_requests(
        {"knCodeList": ["kb1", "unauthorized"]}, [kb], ctx
    )

    assert len(errors) == 1
    assert "unauthorized" in errors[0]["error"]
    assert errors[0]["is_error"] is True
    assert len(requests) == 1
    assert requests[0].body["knCodeList"] == ["kb1"]


def test_build_requests_skips_kbs_without_metadata_fields_path():
    kb = _kb("kb1", "svc-a", {OperationType.LIST_DIR: "/api/listDir"})
    ctx = _make_context(kb)
    op = MetadataFieldsListOperation()

    requests, errors = op.build_requests({}, [kb], ctx)

    assert requests == []
    assert errors == []


def test_build_requests_direct_mode():
    kb = KnowledgeBaseConfig(
        kb_code="kb1",
        kb_name="kb1",
        service_name="",
        base_url="http://10.0.1.5:8080",
        operations={OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    ctx = _make_context(kb)
    op = MetadataFieldsListOperation()

    requests, _ = op.build_requests({}, [kb], ctx)

    assert len(requests) == 1
    assert requests[0].base_url == "http://10.0.1.5:8080"
    assert requests[0].path == "/api/v1/metadataFields/list"


def test_build_requests_normalizes_headers():
    kb = KnowledgeBaseConfig(
        kb_code="kb1",
        kb_name="kb1",
        service_name="svc-a",
        headers={"X-Trace": 123, "X-None": None},
        operations={OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    ctx = _make_context(kb)
    op = MetadataFieldsListOperation()

    requests, _ = op.build_requests({}, [kb], ctx)

    assert requests[0].headers == {"X-Trace": "123", "X-None": ""}


# ── process_response ────────────────────────────────────────────────


def test_process_response_extracts_data_items():
    op = MetadataFieldsListOperation()
    resp = {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {
            "data": [
                {
                    "propertyName": "status",
                    "valueType": "string",
                    "description": "状态",
                },
                {
                    "propertyName": "tags",
                    "valueType": "stringList",
                    "description": "标签",
                },
            ]
        },
    }
    req = DispatchRequest(service_name="svc", path="/api", body={})

    results = op.process_response(resp, req)

    assert len(results) == 2
    assert results[0]["property_name"] == "status"
    assert results[1]["property_name"] == "tags"


def test_process_response_handles_empty_data():
    op = MetadataFieldsListOperation()
    resp = {"resultCode": "0", "resultMsg": "success", "resultObject": {"data": []}}
    req = DispatchRequest(service_name="svc", path="/api", body={})

    results = op.process_response(resp, req)

    assert results == []


# ── process_api_error ───────────────────────────────────────────────


def test_process_api_error_formats_entry():
    op = MetadataFieldsListOperation()
    resp = {
        "resultCode": "-1",
        "resultMsg": "knCodeList must not be empty",
        "resultObject": {},
    }
    req = DispatchRequest(service_name="svc", path="/api", body={})

    results = op.process_api_error(resp, req)

    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert results[0]["error_type"] == "ApiError"
    assert "knCodeList must not be empty" in results[0]["error"]


# ── process_error ───────────────────────────────────────────────────


def test_process_error_formats_entry():
    op = MetadataFieldsListOperation()
    req = DispatchRequest(service_name="svc", path="/api", body={})

    results = op.process_error(ConnectionError("timeout"), req)

    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert results[0]["error_type"] == "ConnectionError"
    assert "timeout" in results[0]["error"]


# ── aggregate ───────────────────────────────────────────────────────


def test_aggregate_sorts_by_property_name():
    op = MetadataFieldsListOperation()
    parts = [
        [{"property_name": "status", "value_type": "string"}],
        [{"property_name": "archived", "value_type": "boolean"}],
    ]

    result = op.aggregate(parts)

    assert [r["property_name"] for r in result] == ["archived", "status"]


def test_aggregate_deduplicates_by_property_name():
    op = MetadataFieldsListOperation()
    parts = [
        [{"property_name": "status", "value_type": "string"}],
        [{"property_name": "status", "value_type": "string"}],
    ]

    result = op.aggregate(parts)

    assert len(result) == 1
    assert result[0]["property_name"] == "status"


def test_aggregate_prepends_pre_dispatch_errors():
    op = MetadataFieldsListOperation()
    pre_dispatch = [{"error": "unauthorized", "is_error": True}]
    per_request = [[{"property_name": "status", "value_type": "string"}]]

    result = op.aggregate(pre_dispatch + per_request)

    assert result[0]["is_error"] is True
    assert result[1]["property_name"] == "status"


def test_aggregate_handles_empty_parts():
    op = MetadataFieldsListOperation()
    assert op.aggregate([]) == []


# ── Dispatcher integration ──────────────────────────────────────────


def test_dispatcher_auto_registers_metadata_fields_operation():
    from by_qa.qa.tools.knowledge_tools import ServiceToolDispatcher

    kb = _kb(
        "kb1",
        "svc-a",
        {OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    dispatcher = ServiceToolDispatcher([kb])

    assert OperationType.METADATA_FIELDS_LIST in dispatcher._parallel_ops
    assert isinstance(
        dispatcher._parallel_ops[OperationType.METADATA_FIELDS_LIST],
        MetadataFieldsListOperation,
    )


def test_build_tools_includes_metadata_fields_list():
    from by_qa.qa.tools.knowledge_tools import ServiceToolDispatcher

    kb = _kb(
        "kb1",
        "svc-a",
        {OperationType.METADATA_FIELDS_LIST: "/api/v1/metadataFields/list"},
    )
    dispatcher = ServiceToolDispatcher([kb])
    tools = dispatcher.build_tools()

    tool_names = [t.name for t in tools]
    assert "list_metadata_fields" in tool_names
