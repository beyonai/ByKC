# tests/qa/instant/unit/test_config.py
import pytest

from by_qa.qa.instant.config import InstantQARetrievalConfig, KnowledgeBaseConfig
from by_qa.qa.instant.runtime.operation_registry import OperationType


def test_knowledge_base_config_requires_service_name():
    with pytest.raises(ValueError, match="service_name"):
        InstantQARetrievalConfig(
            knowledge_bases=[
                {
                    "kb_code": "hr-policy",
                    "kb_name": "HR",
                    "service_name": "",
                    "operations": {
                        OperationType.SEARCH: "/api/v1/knowledgeItems/search"
                    },
                }
            ]
        )


def test_knowledge_base_config_accepts_operations_dict():
    config = InstantQARetrievalConfig(
        knowledge_bases=[
            KnowledgeBaseConfig(
                kb_code="kb1",
                kb_name="KB1",
                service_name="svc-a",
                operations={
                    OperationType.SEARCH: "/api/v1/knowledgeItems/search",
                    OperationType.LIST_DIR: "/api/v1/listDir",
                },
            )
        ]
    )
    kb = config.knowledge_bases[0]
    assert kb.operations[OperationType.SEARCH] == "/api/v1/knowledgeItems/search"
    assert kb.operations[OperationType.LIST_DIR] == "/api/v1/listDir"


def test_knowledge_base_config_accepts_dict_input():
    config = InstantQARetrievalConfig(
        knowledge_bases=[
            {
                "kb_code": "kb1",
                "kb_name": "KB1",
                "service_name": "svc-a",
                "operations": {"search": "/api/v1/knowledgeItems/search"},
            }
        ]
    )
    kb = config.knowledge_bases[0]
    assert kb.operations[OperationType.SEARCH] == "/api/v1/knowledgeItems/search"


def test_knowledge_base_config_empty_operations_is_valid():
    config = InstantQARetrievalConfig(
        knowledge_bases=[
            KnowledgeBaseConfig(kb_code="kb1", kb_name="KB1", service_name="svc-a")
        ]
    )
    assert config.knowledge_bases[0].operations == {}
