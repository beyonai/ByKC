"""Regression tests for non-semantic relation evidence chunking."""

from by_qa.knowledge_base.services.knowledge_entity_task_worker import (
    KnowledgeEntityTaskWorker,
)


def test_relation_chunking_preserves_mixed_entity_source_text() -> None:
    source = (
        "Google Knowledge Catalog：用知识目录发现语义资产\n\n"
        "Knowledge Catalog 支持 MCP，Agent 可以搜索数据资产。\n\n"
        "IBM watsonx.data intelligence：连接业务定义与治理证据"
    )

    fragments = KnowledgeEntityTaskWorker._split_relation_evidence(source)

    assert "\n\n".join(fragments) == source


def test_relation_chunking_uses_only_structural_character_limit() -> None:
    source = "A" * 2_100

    fragments = KnowledgeEntityTaskWorker._split_relation_evidence(source)

    assert tuple(map(len, fragments)) == (2_000, 100)
    assert "".join(fragments) == source


def test_relation_evidence_budget_samples_the_whole_document() -> None:
    fragments = tuple(str(index) * 2_000 for index in range(10))

    selected = KnowledgeEntityTaskWorker._select_relation_evidence(
        fragments, max_total_chars=8_000
    )

    assert selected == (fragments[0], fragments[3], fragments[6], fragments[9])
    assert sum(map(len, selected)) == 8_000
