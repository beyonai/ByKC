"""Unit tests for KnowledgeSearchOperation in isolation (no HTTP)."""

from by_qa.qa.common.config import KnowledgeBaseConfig, QARetrievalConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.operation_registry import OperationType
from by_qa.qa.tools.operations.knowledge_search import (
    KnowledgeSearchOperation,
    _format_search_api_error,
    _format_search_error,
    _format_search_result,
)


def _make_context(*kbs: KnowledgeBaseConfig) -> QARuntimeContext:
    return QARuntimeContext(retrieval=QARetrievalConfig(knowledge_bases=list(kbs)))


def _kb(kb_code: str, service: str, ops: dict) -> KnowledgeBaseConfig:
    return KnowledgeBaseConfig(
        kb_code=kb_code, kb_name=kb_code, service_name=service, operations=ops
    )


# ── Format helpers ──────────────────────────────────────────────


def test_format_search_result_maps_camelcase_fields():
    item = {
        "chunkText": "hello",
        "filePath": "/doc.md",
        "score": 0.95,
        "knCode": "kb1",
        "file_code": "f1",
        "version": "v2",
        "chunkNo": 3,
        "source_code": "src1",
        "type_code": "t1",
    }
    result = _format_search_result(item)
    assert result["content"] == "hello"
    assert result["source"] == "/doc.md"
    assert result["source_type"] == "knowledge_base"
    assert result["score"] == 0.95
    assert result["kb_code"] == "kb1"
    assert result["file_code"] == "f1"
    assert result["version"] == "v2"
    assert result["chunk_no"] == 3
    assert result["file_path"] == "/doc.md"


def test_format_search_result_maps_snake_case_fields():
    item = {
        "chunk_text": "world",
        "file_path": "/a.py",
        "score": 0.5,
        "kb_code": "kb2",
    }
    result = _format_search_result(item)
    assert result["content"] == "world"
    assert result["source"] == "/a.py"
    assert result["kb_code"] == "kb2"


def test_format_search_error_produces_entry():
    exc = RuntimeError("boom")
    entry = _format_search_error(service_name="svc", path="/api", exc=exc)
    assert entry["is_error"] is True
    assert entry["error"] == "boom"
    assert entry["error_type"] == "RuntimeError"
    assert entry["service_name"] == "svc"
    assert entry["path"] == "/api"
    assert entry["score"] == 0.0


def test_format_search_api_error_produces_entry():
    entry = _format_search_api_error(
        service_name="svc", path="/api", result_msg="bad request"
    )
    assert entry["is_error"] is True
    assert entry["error"] == "bad request"
    assert entry["error_type"] == "ApiError"
    assert entry["service_name"] == "svc"
    assert entry["path"] == "/api"


# ── build_requests ──────────────────────────────────────────────


def test_build_requests_groups_by_service():
    kb_a1 = _kb(
        "kb1",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/search"},
    )
    kb_a2 = _kb(
        "kb2",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/search"},
    )
    kb_b = _kb(
        "kb3",
        "svc-b",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/search"},
    )
    ctx = _make_context(kb_a1, kb_a2, kb_b)
    op = KnowledgeSearchOperation()

    requests, errors = op.build_requests({"query": "q"}, [kb_a1, kb_a2, kb_b], ctx)

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
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/search"},
    )
    kb2 = _kb(
        "kb2",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/search"},
    )
    ctx = _make_context(kb1, kb2)
    op = KnowledgeSearchOperation()

    requests, errors = op.build_requests(
        {"query": "q", "knCodeList": ["kb1"]}, [kb1, kb2], ctx
    )

    assert errors == []
    assert len(requests) == 1
    assert requests[0].body["knCodeList"] == ["kb1"]


def test_build_requests_reports_unauthorized_kb_codes():
    kb = _kb(
        "kb1",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/search"},
    )
    ctx = _make_context(kb)
    op = KnowledgeSearchOperation()

    requests, errors = op.build_requests(
        {"query": "q", "knCodeList": ["kb1", "unauthorized"]}, [kb], ctx
    )

    assert len(errors) == 1
    assert "unauthorized" in errors[0]["error"]
    assert errors[0]["is_error"] is True
    assert len(requests) == 1
    assert requests[0].body["knCodeList"] == ["kb1"]


def test_build_requests_skips_kbs_without_search_path():
    kb = _kb("kb1", "svc-a", {OperationType.LIST_DIR: "/api/listDir"})
    ctx = _make_context(kb)
    op = KnowledgeSearchOperation()

    requests, errors = op.build_requests({"query": "q"}, [kb], ctx)

    assert requests == []
    assert errors == []


def test_build_requests_includes_top_k_and_search_mode():
    kb = _kb(
        "kb1",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/search"},
    )
    ctx = _make_context(kb)
    ctx.retrieval.top_k = 15
    op = KnowledgeSearchOperation()

    requests, _ = op.build_requests({"query": "q"}, [kb], ctx)

    assert requests[0].body["topK"] == 15
    assert requests[0].body["searchMode"] == "mixedRecall"


def test_build_requests_direct_mode():
    kb = KnowledgeBaseConfig(
        kb_code="kb1",
        kb_name="kb1",
        service_name="",
        base_url="http://10.0.1.5:8080",
        operations={OperationType.KNOWLEDGE_SEARCH: "/api/v1/search"},
    )
    ctx = _make_context(kb)
    op = KnowledgeSearchOperation()

    requests, _ = op.build_requests({"query": "q"}, [kb], ctx)

    assert len(requests) == 1
    assert requests[0].base_url == "http://10.0.1.5:8080"
    assert requests[0].path == "/api/v1/search"


def test_build_requests_normalizes_headers():
    kb = KnowledgeBaseConfig(
        kb_code="kb1",
        kb_name="kb1",
        service_name="svc-a",
        headers={"X-Trace": 123, "X-None": None},
        operations={OperationType.KNOWLEDGE_SEARCH: "/api/v1/search"},
    )
    ctx = _make_context(kb)
    op = KnowledgeSearchOperation()

    requests, _ = op.build_requests({"query": "q"}, [kb], ctx)

    assert requests[0].headers == {"X-Trace": "123", "X-None": ""}


# ── process_response ────────────────────────────────────────────


def test_process_response_extracts_data_items():
    op = KnowledgeSearchOperation()
    resp = {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {
            "data": [
                {"chunkText": "hit1", "score": 0.9, "filePath": "/a.md"},
                {"chunkText": "hit2", "score": 0.7, "filePath": "/b.md"},
            ]
        },
    }
    from by_qa.qa.tools.operations.base import DispatchRequest

    req = DispatchRequest(service_name="svc", path="/api", body={})

    results = op.process_response(resp, req)

    assert len(results) == 2
    assert results[0]["content"] == "hit1"
    assert results[1]["content"] == "hit2"


# ── process_api_error ───────────────────────────────────────────


def test_process_api_error_formats_entry():
    op = KnowledgeSearchOperation()
    resp = {"resultCode": "-1", "resultMsg": "topK must be > 0", "resultObject": {}}
    from by_qa.qa.tools.operations.base import DispatchRequest

    req = DispatchRequest(service_name="svc", path="/api", body={})

    results = op.process_api_error(resp, req)

    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert results[0]["error_type"] == "ApiError"
    assert "topK must be > 0" in results[0]["error"]


# ── process_error ───────────────────────────────────────────────


def test_process_error_formats_entry():
    op = KnowledgeSearchOperation()
    from by_qa.qa.tools.operations.base import DispatchRequest

    req = DispatchRequest(service_name="svc", path="/api", body={})

    results = op.process_error(ConnectionError("timeout"), req)

    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert results[0]["error_type"] == "ConnectionError"
    assert "timeout" in results[0]["error"]


# ── aggregate ───────────────────────────────────────────────────


def test_aggregate_sorts_by_score_descending():
    op = KnowledgeSearchOperation()
    parts = [
        [{"content": "low", "score": 0.3}],
        [{"content": "high", "score": 0.9}, {"content": "mid", "score": 0.6}],
    ]

    result = op.aggregate(parts)

    assert [r["content"] for r in result] == ["high", "mid", "low"]


def test_aggregate_prepends_pre_dispatch_errors():
    op = KnowledgeSearchOperation()
    pre_dispatch = [{"error": "unauthorized", "is_error": True, "score": 0.0}]
    per_request = [[{"content": "good", "score": 0.8}]]

    result = op.aggregate(pre_dispatch + per_request)

    assert result[0]["is_error"] is True
    assert result[1]["content"] == "good"


def test_aggregate_handles_empty_parts():
    op = KnowledgeSearchOperation()
    assert op.aggregate([]) == []
