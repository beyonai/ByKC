from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest

from by_qa.knowledge_base.services.knowledge_entity_discovery import (
    DISCOVERY_SYSTEM_PROMPT,
    KnowledgeEntityDiscovery,
    build_discovery_context,
    normalize_discovery_result,
)
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    KnowledgeEntityOutputError,
)


class FakeLLM:
    def __init__(self, *outputs: object) -> None:
        self.outputs = [json.dumps(value, ensure_ascii=False) for value in outputs]
        self.calls: list[Sequence[Mapping[str, str]]] = []

    async def complete(self, messages, *, json_mode=False):
        assert json_mode is True
        self.calls.append(messages)
        return self.outputs.pop(0)


def payload(*, entities=None, topics=None):
    return {"entities": entities or [], "topics": topics or []}


def entity(ref="e1", name="Hermes Agent", aliases=None):
    return {
        "entityRef": ref,
        "name": name,
        "aliases": aliases or [],
        "entityDescription": f"{name} 是一个具有稳定身份的知识实体。",
        "evidenceSummary": "文档直接系统研究它的稳定身份与独立维护价值。",
    }


def topic(owner="e1", name="上下文管理"):
    return {
        "ownerEntityRef": owner,
        "name": name,
        "evidenceSummary": "该方向附属于 owner 的实现且具有持续检索价值。",
    }


@pytest.mark.asyncio
async def test_strict_entity_topic_protocol_without_source_refs() -> None:
    llm = FakeLLM(
        payload(
            entities=[entity(aliases=["Hermes"])],
            topics=[topic()],
        )
    )
    result = await KnowledgeEntityDiscovery(llm).discover(
        "Hermes Agent（简称 Hermes）是本文的核心研究对象。\n\n"
        "上下文管理是 Hermes Agent 的特化实现方向。"
    )
    assert [item.name for item in result.entities] == ["Hermes Agent"]
    assert result.entities[0].aliases == ("Hermes",)
    assert result.topics[0].owner_entity_ref == "e1"
    assert set(result.raw_json) == {"entities", "topics"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"entities": [], "topics": [], "extra": True},
        payload(entities=[{**entity(), "entityType": "product"}]),
        payload(
            entities=[
                {key: value for key, value in entity().items() if key != "entityRef"}
            ]
        ),
        payload(entities=[entity()], topics=[topic(owner="missing")]),
        payload(entities=[{**entity(), "evidenceRefs": ["s1"]}]),
        payload(entities=[entity(), entity(ref="e1")]),
    ],
)
async def test_shape_failures_retry_then_recover(bad) -> None:
    good = payload(entities=[entity()], topics=[])
    result = await KnowledgeEntityDiscovery(FakeLLM(bad, good)).discover(
        "Hermes Agent 是本文直接研究的稳定对象。"
    )
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_retries_are_bounded() -> None:
    with pytest.raises(KnowledgeEntityOutputError):
        await KnowledgeEntityDiscovery(
            FakeLLM({"bad": []}, {"bad": []}), max_attempts=2
        ).discover("Hermes Agent")


def test_formal_validation_drops_missing_names_aliases_and_owners_fail_closed() -> None:
    context = build_discovery_context(
        "Hermes Agent 是核心对象，Hermes 是简称。\n\n上下文管理是其专题。"
    )
    raw = payload(
        entities=[
            entity(),
            entity(ref="e2", name="未出现实体"),
            entity(ref="e3", name="Hermes Agent", aliases=["未出现别名", "Hermes"]),
        ],
        topics=[topic(owner="e2"), topic(owner="e3")],
    )
    entities, topics, warnings = normalize_discovery_result(raw, context=context)
    assert len(entities) == 1
    assert entities[0].aliases == ("Hermes",)
    assert topics[0].owner_entity_ref == "e1"
    assert any("name_not_in_context" in warning for warning in warnings)
    assert any("unresolved_owner" in warning for warning in warnings)


def test_entity_and_same_named_topic_coexist_without_cross_layer_dedupe() -> None:
    context = build_discovery_context(
        "上下文管理是公共对象。\n\nOpenClaw 的上下文管理是特化实现。"
    )
    raw = payload(
        entities=[
            entity(ref="public", name="上下文管理"),
            entity(ref="owner", name="OpenClaw"),
        ],
        topics=[topic(owner="owner", name="上下文管理")],
    )
    entities, topics, _ = normalize_discovery_result(raw, context=context)
    assert [item.name for item in entities] == ["上下文管理", "OpenClaw"]
    assert [item.name for item in topics] == ["上下文管理"]


def test_name_validation_accepts_only_cjk_adjacent_typographic_spaces() -> None:
    context = build_discovery_context("Emoji 使用规范由小红书写作规范统一维护。")
    raw = payload(
        entities=[entity(name="小红书写作规范")],
        topics=[topic(name="Emoji使用规范")],
    )

    entities, topics, warnings = normalize_discovery_result(raw, context=context)

    assert [item.name for item in entities] == ["小红书写作规范"]
    assert [item.name for item in topics] == ["Emoji使用规范"]
    assert not any("name_not_in_context" in warning for warning in warnings)


def test_name_validation_preserves_latin_word_boundaries() -> None:
    context = build_discovery_context("BOWL 是文中出现的唯一字样。")
    raw = payload(entities=[entity(name="OWL")])

    entities, _, warnings = normalize_discovery_result(raw, context=context)

    assert entities == ()
    assert any("name_not_in_context" in warning for warning in warnings)


def test_name_validation_does_not_join_independent_latin_words() -> None:
    context = build_discovery_context("Open Claw 是原文中的写法。")
    raw = payload(entities=[entity(name="OpenClaw")])

    entities, _, warnings = normalize_discovery_result(raw, context=context)

    assert entities == ()
    assert any("name_not_in_context" in warning for warning in warnings)


def test_defensive_limits_only_truncate_and_warn() -> None:
    context = build_discovery_context("A B C\n\nT1 T2")
    raw = payload(
        entities=[entity("a", "A"), entity("b", "B"), entity("c", "C")],
        topics=[topic("a", "T1"), topic("a", "T2")],
    )
    entities, topics, warnings = normalize_discovery_result(
        raw, context=context, max_entities=1, max_topics=1
    )
    assert len(entities) == 1 and len(topics) == 1
    assert sum("truncated" in warning for warning in warnings) == 2


def test_prompt_is_concise_and_precision_first() -> None:
    assert len(DISCOVERY_SYSTEM_PROMPT) < 5_000
    assert "首要目标是 precision" in DISCOVERY_SYSTEM_PROMPT
    assert "宁可遗漏" in DISCOVERY_SYSTEM_PROMPT
    assert "任一核心条件不确定就不输出" in DISCOVERY_SYSTEM_PROMPT
    assert "不得为接近数量上限而凑数" in DISCOVERY_SYSTEM_PROMPT


def test_prompt_requires_context_independent_unambiguous_identity() -> None:
    assert "脱离当前原材料" in DISCOVERY_SYSTEM_PROMPT
    assert "独立身份" in DISCOVERY_SYSTEM_PROMPT
    assert "基本无歧义" in DISCOVERY_SYSTEM_PROMPT
    assert "熔断机制" in DISCOVERY_SYSTEM_PROMPT
    assert "金融和计算机领域含义不同" in DISCOVERY_SYSTEM_PROMPT
    assert "不得用 evidenceSummary 弥补" in DISCOVERY_SYSTEM_PROMPT
    assert "规范、标准或制度资产本身" in DISCOVERY_SYSTEM_PROMPT
    assert "完整正式标题仍能在本知识库内唯一定位" in DISCOVERY_SYSTEM_PROMPT


def test_prompt_separates_stable_description_from_source_evidence() -> None:
    assert "entityDescription" in DISCOVERY_SYSTEM_PROMPT
    assert "脱离当前文档仍成立" in DISCOVERY_SYSTEM_PROMPT
    assert "它是什么" in DISCOVERY_SYSTEM_PROMPT
    assert "核心研究对象" in DISCOVERY_SYSTEM_PROMPT


def test_prompt_distinguishes_classification_labels_and_members() -> None:
    assert "作者为本文临时建立" in DISCOVERY_SYSTEM_PROMPT
    assert "分类标签而非 Entity" in DISCOVERY_SYSTEM_PROMPT
    assert "分类单位是 X" in DISCOVERY_SYSTEM_PROMPT
    assert "优先对 X 及其具体成员" in DISCOVERY_SYSTEM_PROMPT
    assert "另一作者的分类框架混合" in DISCOVERY_SYSTEM_PROMPT


def test_prompt_keeps_core_topic_naming_scope_and_alias_rules() -> None:
    role_index = DISCOVERY_SYSTEM_PROMPT.index("先冻结 Entity 集")
    naming_index = DISCOVERY_SYSTEM_PROMPT.index("命名规则")
    assert role_index < naming_index
    assert "ownerEntityRef" in DISCOVERY_SYSTEM_PROMPT
    assert "无法确定 owner 就不输出" in DISCOVERY_SYSTEM_PROMPT
    assert "局部受众" in DISCOVERY_SYSTEM_PROMPT
    assert "同时作 owner 的 Topic" in DISCOVERY_SYSTEM_PROMPT
    assert "只用于组织 owner 的分析维度或章节" in DISCOVERY_SYSTEM_PROMPT
    assert "稳定规则域可以作为 Topic" in DISCOVERY_SYSTEM_PROMPT
    assert "内容适配/建议" in DISCOVERY_SYSTEM_PROMPT
    assert "不能作该规范的 Topic" in DISCOVERY_SYSTEM_PROMPT
    assert "不足以再创建 Topic" in DISCOVERY_SYSTEM_PROMPT
    assert "最小充分名" in DISCOVERY_SYSTEM_PROMPT
    assert "不得临时翻译" in DISCOVERY_SYSTEM_PROMPT
    assert "本规范" in DISCOVERY_SYSTEM_PROMPT
    assert "篇章内指代" in DISCOVERY_SYSTEM_PROMPT
    assert "documentOverview" in DISCOVERY_SYSTEM_PROMPT
    assert "documentEvidence" in DISCOVERY_SYSTEM_PROMPT


def test_long_document_context_covers_structure_and_distributed_evidence() -> None:
    sections = []
    for index, name in enumerate(("Alpha", "Beta", "Gamma", "Delta"), start=1):
        sections.append(
            f"## {name}\n"
            f"{name}-代表证据 " + (f"第{index}节稳定正文。" * 45) + "\n\n"
            f"{name}-补充证据 " + (f"第{index}节补充材料。" * 35)
        )
    markdown = (
        "# 全文标题\n\n导语证据 " + ("总体说明。" * 30) + "\n\n" + "\n\n".join(sections)
    )

    context = build_discovery_context(markdown, max_chars=4000)

    assert context.truncated is True
    assert "[documentOverview]" in context.excerpt
    assert "[headingIndex]" in context.excerpt
    assert "[documentEvidence]" in context.excerpt
    for heading in ("# 全文标题", "## Alpha", "## Beta", "## Gamma", "## Delta"):
        assert heading in context.excerpt
    for marker in (
        "Alpha-代表证据",
        "Beta-代表证据",
        "Gamma-代表证据",
        "Delta-代表证据",
    ):
        assert marker in context.excerpt
    assert len(context.excerpt) <= 4000
    assert sum(len(item.content) for item in context.source_references) <= 4000
    assert any(item.start > len(markdown) // 3 for item in context.source_references)
    assert any(
        item.start > len(markdown) * 2 // 3 for item in context.source_references
    )


def test_context_references_are_exact_source_spans_with_structural_metadata() -> None:
    markdown = "  # 标题\n\n开头。\n\n## 中段\n中段正文。\n\n## 结尾\n结尾正文。  "

    context = build_discovery_context(markdown, max_chars=1024)

    assert context.source_references
    for reference in context.source_references:
        assert markdown[reference.start : reference.end] == reference.content
        assert reference.kind in {"heading", "content"}
        if reference.kind == "heading":
            assert reference.heading_level in {1, 2}
            assert reference.section_path
        else:
            assert reference.heading_level is None
    assert "section=" in context.excerpt


def test_complete_short_context_is_not_marked_truncated_by_blank_lines() -> None:
    context = build_discovery_context("# 标题\n\n第一段。\n\n第二段。", max_chars=1024)

    assert context.truncated is False
    assert "truncated=false" in context.excerpt
    assert "headingIndexComplete=true" in context.excerpt


def test_fenced_code_comments_do_not_pollute_heading_index_or_section_path() -> None:
    markdown = (
        "# 正文标题\n\n开头正文。\n\n"
        "```bash\n# 伪标题一\necho ok\n# 伪标题二\n```\n\n"
        "## 真实章节\n\n真实章节正文。"
    )

    context = build_discovery_context(markdown, max_chars=2048)
    headings = [item for item in context.source_references if item.kind == "heading"]
    content = [item for item in context.source_references if item.kind == "content"]

    assert [item.content for item in headings] == ["# 正文标题", "## 真实章节"]
    assert all("伪标题" not in " > ".join(item.section_path) for item in content)
    assert content[-1].section_path == ("正文标题", "真实章节")


@pytest.mark.asyncio
async def test_removed_evidence_refs_field_is_rejected() -> None:
    bad = payload(entities=[{**entity(name="Hermes Agent"), "evidenceRefs": ["s1"]}])
    good = payload(entities=[entity(name="Hermes Agent")])

    result = await KnowledgeEntityDiscovery(FakeLLM(bad, good)).discover(
        "# Hermes Agent\n\nHermes Agent 是本文直接研究的稳定对象。"
    )

    assert result.attempts == 2


def test_serialized_context_and_unbroken_long_line_stay_within_budget() -> None:
    markdown = "# 标题\n\n" + ("无分隔正文" * 50000)

    context = build_discovery_context(markdown, max_chars=2400)

    assert len(context.excerpt) <= 2400
    assert context.truncated is True
    assert all(
        markdown[item.start : item.end] == item.content
        for item in context.source_references
    )


def test_normalization_preserves_source_exact_names_without_semantic_rewrite() -> None:
    context = build_discovery_context(
        "OpenClaw 是核心对象。\n\n"
        "Prompt Engineering：动态组装与文件驱动是一个稳定方向。"
    )
    raw = payload(
        entities=[entity(name="OpenClaw")],
        topics=[
            topic(
                name="Prompt Engineering：动态组装与文件驱动",
            )
        ],
    )

    entities, topics, _ = normalize_discovery_result(raw, context=context)

    assert entities[0].name == "OpenClaw"
    assert topics[0].name == "Prompt Engineering：动态组装与文件驱动"
