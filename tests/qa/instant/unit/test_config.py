"""Tests for instant QA configuration models."""

import pytest

from by_qa.qa.instant.config import InstantQARetrievalConfig


def test_retrieval_config_requires_service_name_and_path():
    with pytest.raises(ValueError, match="service_name"):
        InstantQARetrievalConfig(
            knowledge_bases=[
                {
                    "kb_code": "hr-policy",
                    "kb_name": "HR",
                    "service_name": "",
                    "path": "/api/v1/knowledgeItems/search",
                }
            ]
        )

    with pytest.raises(ValueError, match="path"):
        InstantQARetrievalConfig(
            knowledge_bases=[
                {
                    "kb_code": "hr-policy",
                    "kb_name": "HR",
                    "service_name": "kb-search-service-a",
                    "path": "",
                }
            ]
        )
