"""Tests for the instant QA smoke-test script."""

import importlib.util
from pathlib import Path

import pytest


def _load_script_module():
    script_path = Path("scripts/qa/test_instant_qa.py")
    spec = importlib.util.spec_from_file_location("test_instant_qa_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_engine_config_pairs_appended_headers_with_kb_codes():
    script = _load_script_module()

    args = script._build_parser().parse_args(
        [
            "--query",
            "员工请假制度",
            "--kb-code",
            "hr-policy",
            "--kb-code",
            "finance-policy",
            "--kb-code",
            "legal-policy",
            "--header",
            "Authorization=Bearer hr-token;X-Tenant=tenant-a",
            "--header",
            "",
            "--header",
            "Authorization: Bearer legal-token;X-Tenant=legal-tenant",
        ]
    )

    config = script._build_engine_config(args)

    assert config["retrieval"]["knowledge_bases"] == [
        {
            "kb_code": "hr-policy",
            "kb_name": "hr-policy",
            "kb_description": "",
            "service_name": "by-qa-manager",
            "path": "/api/v1/knowledgeItems/sear",
            "headers": {
                "Authorization": "Bearer hr-token",
                "X-Tenant": "tenant-a",
            },
        },
        {
            "kb_code": "finance-policy",
            "kb_name": "finance-policy",
            "kb_description": "",
            "service_name": "by-qa-manager",
            "path": "/api/v1/knowledgeItems/sear",
        },
        {
            "kb_code": "legal-policy",
            "kb_name": "legal-policy",
            "kb_description": "",
            "service_name": "by-qa-manager",
            "path": "/api/v1/knowledgeItems/sear",
            "headers": {
                "Authorization": "Bearer legal-token",
                "X-Tenant": "legal-tenant",
            },
        },
    ]


def test_build_engine_config_rejects_partial_header_list():
    script = _load_script_module()

    args = script._build_parser().parse_args(
        [
            "--query",
            "员工请假制度",
            "--kb-code",
            "hr-policy",
            "--kb-code",
            "finance-policy",
            "--header",
            "Authorization=Bearer hr-token",
        ]
    )

    with pytest.raises(ValueError, match="--header count"):
        script._build_engine_config(args)
