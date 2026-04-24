"""Smoke tests for shared QA runtime modules."""

from by_qa.qa.common.config import KnowledgeBaseConfig, QARetrievalConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.operation_registry import OperationType
from by_qa.qa.tools.knowledge_tools import ServiceToolDispatcher


def test_shared_runtime_models_are_available_for_non_instant_engines():
    retrieval = QARetrievalConfig(
        knowledge_bases=[
            KnowledgeBaseConfig(
                kb_code="kb1",
                kb_name="KB1",
                service_name="svc-a",
                operations={
                    OperationType.KNOWLEDGE_SEARCH: "/api/v1/knowledgeItems/search"
                },
            )
        ]
    )

    context = QARuntimeContext(retrieval=retrieval)
    dispatcher = ServiceToolDispatcher(context.retrieval.knowledge_bases)

    assert context.retrieval.knowledge_bases[0].kb_code == "kb1"
    assert dispatcher.build_tools()
