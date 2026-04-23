# tests/qa/instant/unit/test_dispatcher.py
"""Tests for ServiceToolDispatcher routing and dispatch."""

from unittest.mock import patch

import pytest

from by_qa.qa.instant.config import InstantQARetrievalConfig, KnowledgeBaseConfig
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext
from by_qa.qa.instant.runtime.dispatcher import ServiceToolDispatcher
from by_qa.qa.instant.runtime.operation_registry import (
    OPERATION_REGISTRY,
    OperationType,
)


def _make_context(*kbs: KnowledgeBaseConfig) -> InstantSearchRuntimeContext:
    return InstantSearchRuntimeContext(
        retrieval=InstantQARetrievalConfig(knowledge_bases=list(kbs))
    )


def _kb(kb_code: str, service: str, ops: dict) -> KnowledgeBaseConfig:
    return KnowledgeBaseConfig(
        kb_code=kb_code, kb_name=kb_code, service_name=service, operations=ops
    )


def test_build_tools_returns_one_tool_per_supported_op():
    kb = _kb(
        "kb1",
        "svc-a",
        {
            OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search",
            OperationType.LIST_DIR: "/api/v1/listDir",
        },
    )
    dispatcher = ServiceToolDispatcher([kb])
    tools = dispatcher.build_tools()
    tool_names = {t.name for t in tools}
    assert tool_names == {
        OPERATION_REGISTRY[OperationType.KNOWLEDGE_SEARCH].tool_name,
        OPERATION_REGISTRY[OperationType.LIST_DIR].tool_name,
    }


def test_build_tools_empty_when_no_kbs():
    dispatcher = ServiceToolDispatcher([])
    assert dispatcher.build_tools() == []


def test_build_tools_ignores_unknown_operation_types():
    kb = _kb("kb1", "svc-a", {"unknownOp": "/api/v1/unknown"})
    dispatcher = ServiceToolDispatcher([kb])
    assert dispatcher.build_tools() == []


@pytest.mark.asyncio
async def test_dispatch_search_groups_by_service_and_posts():
    kb_a1 = _kb(
        "kb1",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"},
    )
    kb_a2 = _kb(
        "kb2",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"},
    )
    kb_b = _kb(
        "kb3",
        "svc-b",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"},
    )
    ctx = _make_context(kb_a1, kb_a2, kb_b)
    dispatcher = ServiceToolDispatcher([kb_a1, kb_a2, kb_b])

    calls = []

    async def fake_post(*, service_name, path=None, json, headers=None):  # pylint: disable=unused-argument
        calls.append({"service_name": service_name, "json": json})
        return {
            "resultCode": "0",
            "resultMsg": "success",
            "resultObject": {
                "data": [{"chunkText": "hit", "score": 0.9, "filePath": "/f"}]
            },
        }

    with patch(
        "by_qa.qa.instant.runtime.dispatcher.post_discovered_json",
        side_effect=fake_post,
    ):
        results = await dispatcher._dispatch(
            OperationType.KNOWLEDGE_SEARCH, {"query": "q", "knCodeList": None}, ctx
        )

    assert len(calls) == 2
    service_names = {c["service_name"] for c in calls}
    assert service_names == {"svc-a", "svc-b"}
    svc_a_call = next(c for c in calls if c["service_name"] == "svc-a")
    assert set(svc_a_call["json"]["knCodeList"]) == {"kb1", "kb2"}
    assert len(results) == 2  # one hit per service


@pytest.mark.asyncio
async def test_dispatch_search_filters_by_kn_code_list():
    kb1 = _kb(
        "kb1",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"},
    )
    kb2 = _kb(
        "kb2",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"},
    )
    ctx = _make_context(kb1, kb2)
    dispatcher = ServiceToolDispatcher([kb1, kb2])

    calls = []

    async def fake_post(*, service_name=None, path=None, json, headers=None):  # pylint: disable=unused-argument
        calls.append(json["knCodeList"])
        return {"resultCode": "0", "resultMsg": "success", "resultObject": {"data": []}}

    with patch(
        "by_qa.qa.instant.runtime.dispatcher.post_discovered_json",
        side_effect=fake_post,
    ):
        await dispatcher._dispatch(
            OperationType.KNOWLEDGE_SEARCH, {"query": "q", "knCodeList": ["kb1"]}, ctx
        )

    assert calls == [["kb1"]]


@pytest.mark.asyncio
async def test_dispatch_search_normalizes_header_values_to_strings():
    kb = KnowledgeBaseConfig(
        kb_code="kb1",
        kb_name="kb1",
        service_name="svc-a",
        headers={"X-Trace-Id": 123, "X-Optional": None},
        operations={OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"},
    )
    ctx = _make_context(kb)
    dispatcher = ServiceToolDispatcher([kb])

    captured_headers = None

    async def fake_post(*, service_name=None, path=None, json=None, headers=None):  # pylint: disable=unused-argument
        nonlocal captured_headers
        captured_headers = headers
        return {"resultCode": "0", "resultMsg": "success", "resultObject": {"data": []}}

    with patch(
        "by_qa.qa.instant.runtime.dispatcher.post_discovered_json",
        side_effect=fake_post,
    ):
        await dispatcher._dispatch(
            OperationType.KNOWLEDGE_SEARCH, {"query": "q", "knCodeList": None}, ctx
        )

    assert captured_headers == {"X-Trace-Id": "123", "X-Optional": ""}


@pytest.mark.asyncio
async def test_dispatch_list_dir_single_post():
    kb = _kb("kb1", "svc-a", {OperationType.LIST_DIR: "/api/v1/listDir"})
    ctx = _make_context(kb)
    dispatcher = ServiceToolDispatcher([kb])

    async def fake_post(*, service_name=None, path=None, json=None, headers=None):  # pylint: disable=unused-argument
        return {
            "resultCode": "0",
            "resultMsg": "success",
            "resultObject": {
                "data": [
                    {"knCode": "kb1", "name": "/src", "type": "directory", "size": 0}
                ]
            },
        }

    with patch(
        "by_qa.qa.instant.runtime.dispatcher.post_discovered_json",
        side_effect=fake_post,
    ):
        results = await dispatcher._dispatch(
            OperationType.LIST_DIR, {"knCode": "kb1", "directoryPath": "/src"}, ctx
        )

    assert results == {
        "resultCode": "0",
        "resultMsg": "success",
        "resultObject": {
            "data": [{"knCode": "kb1", "name": "/src", "type": "directory", "size": 0}]
        },
    }


@pytest.mark.asyncio
async def test_dispatch_single_kb_normalizes_header_values_to_strings():
    kb = KnowledgeBaseConfig(
        kb_code="kb1",
        kb_name="kb1",
        service_name="svc-a",
        headers={"X-Retry": 2, "X-Flag": True},
        operations={OperationType.LIST_DIR: "/api/v1/listDir"},
    )
    ctx = _make_context(kb)
    dispatcher = ServiceToolDispatcher([kb])

    captured_headers = None

    async def fake_post(*, service_name=None, path=None, json=None, headers=None):  # pylint: disable=unused-argument
        nonlocal captured_headers
        captured_headers = headers
        return {"resultCode": "0", "resultMsg": "success", "resultObject": {"data": []}}

    with patch(
        "by_qa.qa.instant.runtime.dispatcher.post_discovered_json",
        side_effect=fake_post,
    ):
        await dispatcher._dispatch(
            OperationType.LIST_DIR, {"knCode": "kb1", "directoryPath": "/src"}, ctx
        )

    assert captured_headers == {"X-Retry": "2", "X-Flag": "True"}


@pytest.mark.asyncio
async def test_dispatch_single_kb_returns_raw_response_on_api_error():
    kb = _kb("kb1", "svc-a", {OperationType.LIST_DIR: "/api/v1/listDir"})
    ctx = _make_context(kb)
    dispatcher = ServiceToolDispatcher([kb])

    api_error_resp = {
        "resultCode": "-1",
        "resultMsg": "directory not found: /src",
        "resultObject": {},
    }

    async def fake_post(*, service_name=None, path=None, json=None, headers=None):  # pylint: disable=unused-argument
        return api_error_resp

    with patch(
        "by_qa.qa.instant.runtime.dispatcher.post_discovered_json",
        side_effect=fake_post,
    ):
        results = await dispatcher._dispatch(
            OperationType.LIST_DIR, {"knCode": "kb1", "directoryPath": "/src"}, ctx
        )

    assert results == api_error_resp


@pytest.mark.asyncio
async def test_dispatch_single_kb_returns_error_entry_on_service_exception():
    kb = _kb("kb1", "svc-a", {OperationType.LIST_DIR: "/api/v1/listDir"})
    ctx = _make_context(kb)
    dispatcher = ServiceToolDispatcher([kb])

    async def fake_post(*, service_name=None, path=None, json=None, headers=None):  # pylint: disable=unused-argument
        raise RuntimeError("service down")

    with patch(
        "by_qa.qa.instant.runtime.dispatcher.post_discovered_json",
        side_effect=fake_post,
    ):
        results = await dispatcher._dispatch(
            OperationType.LIST_DIR, {"knCode": "kb1", "directoryPath": "/src"}, ctx
        )

    assert results["is_error"] is True
    assert results["error_type"] == "RuntimeError"
    assert "service down" in results["error"]
    assert results["service_name"] == "svc-a"
    assert results["path"] == "/api/v1/listDir"


@pytest.mark.asyncio
async def test_dispatch_single_kb_returns_error_when_kn_code_not_found():
    kb = _kb("kb1", "svc-a", {OperationType.LIST_DIR: "/api/v1/listDir"})
    ctx = _make_context(kb)
    dispatcher = ServiceToolDispatcher([kb])

    results = await dispatcher._dispatch(
        OperationType.LIST_DIR,
        {"knCode": "unknown-kb", "directoryPath": "/src"},
        ctx,
    )

    assert results["is_error"] is True
    assert results["error_type"] == "KnowledgeBaseNotFoundOrForbiddenError"
    assert "unknown-kb" in results["error"]


@pytest.mark.asyncio
async def test_dispatch_single_kb_returns_error_when_operation_not_supported():
    kb = _kb(
        "kb1",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"},
    )
    ctx = _make_context(kb)
    dispatcher = ServiceToolDispatcher([kb])

    results = await dispatcher._dispatch(
        OperationType.LIST_DIR, {"knCode": "kb1", "directoryPath": "/src"}, ctx
    )

    assert results["is_error"] is True
    assert results["error_type"] == "OperationNotSupportedError"
    assert "listDir" in results["error"]


@pytest.mark.asyncio
async def test_dispatch_search_returns_error_for_unauthorized_kb_codes():
    kb = _kb(
        "kb1",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"},
    )
    ctx = _make_context(kb)
    dispatcher = ServiceToolDispatcher([kb])

    async def fake_post(*, service_name=None, path=None, json=None, headers=None):  # pylint: disable=unused-argument
        return {"resultCode": "0", "resultMsg": "success", "resultObject": {"data": []}}

    with patch(
        "by_qa.qa.instant.runtime.dispatcher.post_discovered_json",
        side_effect=fake_post,
    ):
        results = await dispatcher._dispatch(
            OperationType.KNOWLEDGE_SEARCH,
            {"query": "q", "knCodeList": ["kb1", "unauthorized-kb"]},
            ctx,
        )

    error_entries = [r for r in results if r.get("is_error")]
    assert len(error_entries) == 1
    assert "unauthorized-kb" in error_entries[0]["error"]
    assert error_entries[0]["error_type"] == "KnowledgeBaseNotFoundOrForbiddenError"


@pytest.mark.asyncio
async def test_dispatch_search_returns_error_entry_on_service_exception():
    kb = _kb(
        "kb1",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"},
    )
    ctx = _make_context(kb)
    dispatcher = ServiceToolDispatcher([kb])

    async def fake_post(*, service_name=None, path=None, json=None, headers=None):  # pylint: disable=unused-argument
        raise ConnectionError("timeout")

    with patch(
        "by_qa.qa.instant.runtime.dispatcher.post_discovered_json",
        side_effect=fake_post,
    ):
        results = await dispatcher._dispatch(
            OperationType.KNOWLEDGE_SEARCH, {"query": "q", "knCodeList": None}, ctx
        )

    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert "timeout" in results[0]["error"]


@pytest.mark.asyncio
async def test_dispatch_search_returns_error_entry_on_api_error():
    kb = _kb(
        "kb1",
        "svc-a",
        {OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"},
    )
    ctx = _make_context(kb)
    dispatcher = ServiceToolDispatcher([kb])

    async def fake_post(*, service_name=None, path=None, json=None, headers=None):  # pylint: disable=unused-argument
        return {
            "resultCode": "-1",
            "resultMsg": "topK must be greater than 0",
            "resultObject": {},
        }

    with patch(
        "by_qa.qa.instant.runtime.dispatcher.post_discovered_json",
        side_effect=fake_post,
    ):
        results = await dispatcher._dispatch(
            OperationType.KNOWLEDGE_SEARCH, {"query": "q", "knCodeList": None}, ctx
        )

    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert results[0]["error_type"] == "ApiError"
    assert "topK must be greater than 0" in results[0]["error"]
