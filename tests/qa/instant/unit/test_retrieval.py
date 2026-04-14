"""Tests for instant QA retrieval adapters."""

from unittest.mock import patch

import pytest

from by_qa.qa.instant.config import InstantQARetrievalConfig, KnowledgeBaseConfig
from by_qa.qa.instant.runtime.context import InstantSearchRuntimeContext
from by_qa.qa.instant.runtime.retrieval import (
    _build_remote_search_requests,
    _format_search_hit,
    search_knowledge_items,
)


def test_build_remote_search_requests_groups_kb_codes_by_service_and_path():
    runtime_context = InstantSearchRuntimeContext(
        retrieval=InstantQARetrievalConfig(
            knowledge_bases=[
                KnowledgeBaseConfig(
                    kb_code="hr-policy",
                    kb_name="HR",
                    service_name="kb-search-service-a",
                    path="/api/v1/knowledge-items/search",
                ),
                KnowledgeBaseConfig(
                    kb_code="finance-policy",
                    kb_name="Finance",
                    service_name="kb-search-service-a",
                    path="/api/v1/knowledge-items/search",
                ),
                KnowledgeBaseConfig(
                    kb_code="legal-policy",
                    kb_name="Legal",
                    service_name="kb-search-service-b",
                    path="/api/v1/knowledge-items/search",
                ),
            ],
            source_codes=["oa"],
            type_codes=["pdf"],
            top_k=5,
            vector_top_k=10,
            text_top_k=10,
        )
    )

    requests = _build_remote_search_requests("员工请假制度", runtime_context)

    assert requests == [
        (
            ("kb-search-service-a", "/api/v1/knowledge-items/search"),
            {
                "query": "员工请假制度",
                "kb_codes": ["hr-policy", "finance-policy"],
                "source_codes": ["oa"],
                "type_codes": ["pdf"],
                "top_k": 5,
                "vector_top_k": 10,
                "text_top_k": 10,
            },
        ),
        (
            ("kb-search-service-b", "/api/v1/knowledge-items/search"),
            {
                "query": "员工请假制度",
                "kb_codes": ["legal-policy"],
                "source_codes": ["oa"],
                "type_codes": ["pdf"],
                "top_k": 5,
                "vector_top_k": 10,
                "text_top_k": 10,
            },
        ),
    ]


def test_format_search_hit_matches_agent_facing_shape():
    formatted = _format_search_hit(
        {
            "kb_code": "hr-policy",
            "file_code": "attendance-policy",
            "version": "v1",
            "chunk_no": 2,
            "chunk_text": "第二条 异常考勤需提交说明。",
            "score": 0.91,
            "source_code": "oa",
            "type_code": "pdf",
            "file_path": "/考勤制度/异常考勤处理办法.pdf",
        }
    )

    assert formatted["content"] == "第二条 异常考勤需提交说明。"
    assert formatted["source"] == "/考勤制度/异常考勤处理办法.pdf"
    assert formatted["source_type"] == "knowledge_base"
    assert formatted["score"] == 0.91


@pytest.mark.asyncio
async def test_search_knowledge_items_uses_framework_client():
    runtime_context = InstantSearchRuntimeContext(
        retrieval=InstantQARetrievalConfig(
            knowledge_bases=[
                KnowledgeBaseConfig(
                    kb_code="hr-policy",
                    kb_name="HR",
                    service_name="kb-search-service-a",
                    path="/api/v1/knowledge-items/search",
                )
            ],
            source_codes=["oa"],
            type_codes=["pdf"],
        )
    )
    recorded = {}

    async def fake_post(*, service_name, path, json):
        recorded["service_name"] = service_name
        recorded["path"] = path
        recorded["json"] = json
        return {
            "data": {
                "items": [
                    {
                        "kb_code": "hr-policy",
                        "file_code": "attendance-policy",
                        "version": "v1",
                        "chunk_no": 2,
                        "chunk_text": "第二条 异常考勤需提交说明。",
                        "score": 0.91,
                        "source_code": "oa",
                        "type_code": "pdf",
                        "file_path": "/考勤制度/异常考勤处理办法.pdf",
                    }
                ]
            }
        }

    with patch(
        "by_qa.qa.instant.runtime.retrieval.post_discovered_json",
        side_effect=fake_post,
    ):
        results = await search_knowledge_items("员工请假制度", runtime_context)

    assert recorded == {
        "service_name": "kb-search-service-a",
        "path": "/api/v1/knowledge-items/search",
        "json": {
            "query": "员工请假制度",
            "kb_codes": ["hr-policy"],
            "source_codes": ["oa"],
            "type_codes": ["pdf"],
            "top_k": 20,
            "vector_top_k": 40,
            "text_top_k": 30,
        },
    }
    assert results[0]["content"] == "第二条 异常考勤需提交说明。"
