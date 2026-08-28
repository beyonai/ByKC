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


def test_entity_scope_keeps_profile_body_when_heading_ends_previous_block() -> None:
    mixed = (
        "重点案例：Google Knowledge Catalog、IBM watsonx.data intelligence\n"
        "Google Knowledge Catalog：用知识目录发现语义资产\n\n"
        "Knowledge Catalog 聚合元数据并支持 MCP 协议。\n\n"
        "IBM watsonx.data intelligence：连接治理证据"
    )

    scoped = KnowledgeEntityTaskWorker._scope_evidence_to_entity(
        mixed,
        names=("Google Knowledge Catalog",),
        preserve_without_mention=False,
    )

    assert scoped.startswith("Google Knowledge Catalog：")
    assert "支持 MCP 协议" in scoped
    assert "IBM watsonx.data intelligence" not in scoped


def test_entity_scope_uses_vendorless_name_and_splits_shared_paragraph() -> None:
    mixed = (
        "Palantir Ontology：可执行的业务孪生\n\n"
        "Fabric IQ Ontology、Tableau Semantics：让定义跨应用复用\n\n"
        "微软通过 Fabric IQ Ontology 描述业务对象。"
        "Salesforce 将统一本体建模列为 Agentforce 架构维度。"
    )

    scoped = KnowledgeEntityTaskWorker._scope_evidence_to_entity(
        mixed,
        names=("Microsoft Fabric IQ Ontology",),
        preserve_without_mention=False,
    )

    assert "Fabric IQ Ontology" in scoped
    assert "微软通过" in scoped
    assert "Palantir Ontology" not in scoped
    assert "Salesforce" not in scoped
