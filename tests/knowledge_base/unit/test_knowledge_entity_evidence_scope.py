"""Focused regression tests for Entity-scoped enrichment evidence."""

from by_qa.knowledge_base.services.knowledge_entity_task_worker import (
    KnowledgeEntityTaskWorker,
)


def test_entity_scope_removes_adjacent_sibling_product_profile() -> None:
    mixed = (
        "Google Knowledge Catalog：用知识目录发现语义资产\n\n"
        "Knowledge Catalog 支持 MCP，Agent 可以搜索数据资产。\n\n"
        "IBM watsonx.data intelligence：连接业务定义与治理证据\n\n"
        "watsonx.data intelligence 连接元数据、血缘和质量信息。\n\n"
        "下一流派：任务组装派"
    )

    scoped = KnowledgeEntityTaskWorker._scope_evidence_to_entity(
        mixed,
        names=("IBM watsonx.data intelligence",),
        preserve_without_mention=False,
    )

    assert "IBM watsonx.data intelligence" in scoped
    assert "元数据、血缘和质量" in scoped
    assert "Google Knowledge Catalog" not in scoped
    assert "MCP" not in scoped
    assert "任务组装派" not in scoped
