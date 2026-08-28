"""Unit tests for the pure KnowledgeEntity intelligence layer."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import pytest

from by_qa.core.model_config import LLMModelProfile, ModelConfig
from by_qa.knowledge_base.services import (
    knowledge_entity_intelligence as intelligence_module,
)
from by_qa.knowledge_base.services.knowledge_entity_discovery import (
    KnowledgeEntityDiscovery,
    build_discovery_context,
)
from by_qa.knowledge_base.services.knowledge_entity_enrichment import (
    EvidenceFragment,
    KnowledgeEntityEnricher,
    KnowledgeEntityIdentity,
    RelationTarget,
    audit_enriched_markdown,
    build_evidence_claim_groups,
    format_source_reference,
    normalize_generated_references,
    organize_evidence,
    preserve_existing_references,
)
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    IdentityScope,
    KnowledgeEntityLLMError,
    KnowledgeEntityOutputError,
    OpenAICompatibleKnowledgeEntityLLM,
    RelationCode,
    build_discovery_llm,
    build_enrichment_llm,
    normalize_surface,
    normalize_text_with_offsets,
)


class _FakeLLM:
    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[list[dict[str, str]], bool]] = []

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        json_mode: bool = False,
    ) -> str:
        self.calls.append(([dict(message) for message in messages], json_mode))
        return self.outputs.pop(0)


def test_surface_normalization_uses_nfkc_casefold_and_whitespace_collapse() -> None:
    assert normalize_surface("  ＡＰＩ\u3000Straße  ") == "api strasse"
    assert normalize_surface("e\u0301") == "é"

    normalized = normalize_text_with_offsets("甲ＡＰＩ  Straße乙")

    assert normalized.text == "甲api strasse乙"
    start = normalized.text.index("strasse")
    original_start, original_end = normalized.original_span(start, start + 7)
    assert "甲ＡＰＩ  Straße乙"[original_start:original_end] == "Straße"

    combining = normalize_text_with_offsets("x e\u0301 y")
    assert combining.text == "x é y"
    accent_start = combining.text.index("é")
    original_start, original_end = combining.original_span(
        accent_start, accent_start + 1
    )
    assert "x e\u0301 y"[original_start:original_end] == "e\u0301"


@pytest.mark.skip(reason="replaced by Entity/Topic sourceRef protocol tests")
def test_discovery_context_keeps_original_head_tail_frame_within_50k() -> None:
    markdown = (
        "HEAD-TOKEN\n"
        + ("x" * 12_000)
        + "\nQUARTER-TOKEN\n"
        + ("x" * 12_000)
        + "\n## 中部标题\n"
        + "MIDDLE-TOKEN\n"
        + ("y" * 12_000)
        + "\nTHREE-QUARTER-TOKEN\n"
        + ("y" * 12_000)
        + "\nEND-FRAME\n"
        + ("z" * 12_000)
        + "\nTAIL-TOKEN"
    )

    context = build_discovery_context(markdown)

    assert context.truncated is True
    assert len(context.excerpt) <= 50_000
    assert "HEAD-TOKEN" in context.excerpt
    assert "TAIL-TOKEN" in context.excerpt
    assert "[文档结尾]" in context.excerpt
    assert "[文档标题地图]" in context.excerpt
    assert any("中部标题" in heading for heading in context.heading_map)
    assert "中部标题" in context.excerpt


@pytest.mark.asyncio
@pytest.mark.skip(reason="replaced by Entity/Topic sourceRef protocol tests")
async def test_discovery_retries_strict_json_and_filters_non_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_messages: list[str] = []

    def capture_log(message: str, *args: Any, **_kwargs: Any) -> None:
        log_messages.append(message % args)

    monkeypatch.setattr(intelligence_module.logger, "info", capture_log)
    monkeypatch.setattr(intelligence_module.logger, "warning", capture_log)
    raw_candidates = [
        {
            "entityName": " OSOT ",
            "localName": "OSOT",
            "aliases": ["ＯＳＯＴ", "Object-oriented theory"],
            "identityScope": "global",
            "candidateKind": "entity",
            "stableIdentity": True,
            "evidence": "OSOT 是文档的核心理论。",
        },
        {
            "entityName": "wrong title",
            "localName": "OCG",
            "subjectEntityName": "OSOT",
            "identityScope": "subject",
            "candidateKind": "entity",
            "evidence": "OCG 是 OSOT 定义的稳定组成机制。",
        },
        {
            "entityName": "OSOT 发布会",
            "localName": "OSOT 发布会",
            "identityScope": "global",
            "candidateKind": "event",
            "isEvent": True,
            "evidence": "会议在周五召开。",
        },
        {
            "entityName": "性能提升 30%",
            "localName": "性能提升 30%",
            "identityScope": "global",
            "candidateKind": "entity",
            "isFact": True,
            "evidence": "指标提升。",
        },
        {
            "entityName": "局部角色",
            "localName": "局部角色",
            "identityScope": "global",
            "stableIdentity": False,
            "evidence": "临时角色。",
        },
    ]
    llm = _FakeLLM("not json", json.dumps(raw_candidates, ensure_ascii=False))
    discovery = KnowledgeEntityDiscovery(llm)

    result = await discovery.discover(
        ("# OSOT\n文档内容\nOSOT 是文档的核心理论。\nOCG 是 OSOT 定义的稳定组成机制。"),
        log_context={
            "batch_id": "batch-1",
            "task_id": 51,
            "kb_code": "7",
            "source_file_id": 42,
            "file_path": "/docs/source.md",
            "task_type": "ENTITY_DISCOVERY",
        },
    )

    assert result.attempts == 2
    assert [candidate.entity_name for candidate in result.candidates] == [
        "OSOT",
        "OSOT-OCG",
    ]
    assert result.candidates[0].aliases == ("Object-oriented theory",)
    assert result.candidates[1].identity_scope is IdentityScope.SUBJECT
    assert any("event_or_fact" in warning for warning in result.warnings)
    assert any("unstable_identity" in warning for warning in result.warnings)
    assert len(llm.calls) == 2
    assert llm.calls[0][1] is False
    assert "上次输出无效" in llm.calls[1][0][-1]["content"]
    assert "KnowledgeEntity v1" in llm.calls[0][0][0]["content"]
    rendered_logs = "\n".join(log_messages)
    assert "llm output invalid" in rendered_logs
    assert "llm retry recovered" in rendered_logs
    assert "batch_id=batch-1" in rendered_logs
    assert "task_id=51" in rendered_logs
    assert "文档内容" not in rendered_logs
    assert "Object-oriented theory" not in rendered_logs


@pytest.mark.asyncio
@pytest.mark.skip(reason="replaced by Entity/Topic sourceRef protocol tests")
async def test_discovery_accepts_think_preface_and_json_fence_without_retry() -> None:
    raw_candidates = [
        {
            "entityName": "ByDC",
            "localName": "ByDC",
            "identityScope": "global",
            "candidateKind": "entity",
            "stableIdentity": True,
            "evidence": "ByDC 是一个企业数据中枢。",
        }
    ]
    output = (
        "<think>\nLet me analyze the document carefully.\n</think>\n\n"
        "```json\n"
        f"{json.dumps(raw_candidates, ensure_ascii=False)}\n"
        "```"
    )
    llm = _FakeLLM(output)

    result = await KnowledgeEntityDiscovery(llm).discover(
        "# ByDC\nByDC 是一个企业数据中枢。"
    )

    assert result.attempts == 1
    assert [candidate.entity_name for candidate in result.candidates] == ["ByDC"]
    assert len(llm.calls) == 1


@pytest.mark.asyncio
@pytest.mark.skip(reason="replaced by Entity/Topic sourceRef protocol tests")
async def test_discovery_repairs_malformed_json_without_retry() -> None:
    output = """[
      {
        "entityName": "ByDC",
        "localName": "ByDC",
        "identityScope": "global",
        "candidateKind": "entity",
        "stableIdentity": true,
        "evidence": "ByDC 是一个企业数据中枢。",
      }
    ]"""
    llm = _FakeLLM(output)

    result = await KnowledgeEntityDiscovery(llm).discover(
        "# ByDC\nByDC 是一个企业数据中枢。"
    )

    assert result.attempts == 1
    assert [candidate.entity_name for candidate in result.candidates] == ["ByDC"]
    assert len(llm.calls) == 1


@pytest.mark.asyncio
@pytest.mark.skip(reason="replaced by Entity/Topic sourceRef protocol tests")
async def test_discovery_prompt_does_not_include_existing_entity_vocabulary() -> None:
    output = json.dumps(
        [
            {
                "entityName": "OSOT",
                "localName": "OSOT",
                "identityScope": "global",
                "candidateKind": "entity",
                "stableIdentity": True,
                "evidence": "OSOT 是文档的核心理论。",
            }
        ],
        ensure_ascii=False,
    )
    llm = _FakeLLM(output, output)
    discovery = KnowledgeEntityDiscovery(llm)
    markdown = "# OSOT\nOSOT 是文档的核心理论。"
    result = await discovery.discover(markdown)

    assert result.candidates[0].entity_name == "OSOT"
    assert len(llm.calls) == 1
    system_prompt = llm.calls[0][0][0]["content"]
    user_prompt = llm.calls[0][0][1]["content"]
    normalized_prompt = " ".join(system_prompt.split())
    assert "只在抽取后做身份解析" in normalized_prompt
    assert "外部词表状态" in normalized_prompt
    assert "fileId=10" not in user_prompt
    assert "Known AC matches" not in user_prompt


@pytest.mark.asyncio
@pytest.mark.skip(reason="replaced by Entity/Topic sourceRef protocol tests")
async def test_discovery_cache_is_isolated_by_model_identity() -> None:
    first = json.dumps(
        [
            {
                "entityName": "模型甲实体",
                "localName": "模型甲实体",
                "identityScope": "global",
                "isEvent": False,
                "evidence": "模型甲实体是稳定对象。",
            }
        ],
        ensure_ascii=False,
    )
    second = json.dumps(
        [
            {
                "entityName": "模型乙实体",
                "localName": "模型乙实体",
                "identityScope": "global",
                "isEvent": False,
                "evidence": "模型乙实体是稳定对象。",
            }
        ],
        ensure_ascii=False,
    )

    class _ModelAwareFakeLLM(_FakeLLM):
        identity = "model-a"

        async def cache_identity(self) -> str:
            return self.identity

    llm = _ModelAwareFakeLLM(first, second)
    discovery = KnowledgeEntityDiscovery(llm)
    markdown = "模型甲实体是稳定对象。\n模型乙实体是稳定对象。"

    result_a = await discovery.discover(markdown)
    llm.identity = "model-b"
    result_b = await discovery.discover(markdown)

    assert [item.entity_name for item in result_a.candidates] == ["模型甲实体"]
    assert [item.entity_name for item in result_b.candidates] == ["模型乙实体"]
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_discovery_raises_after_strict_json_retry_limit() -> None:
    llm = _FakeLLM("not json", "{}")

    with pytest.raises(KnowledgeEntityOutputError, match="after 2 attempts"):
        await KnowledgeEntityDiscovery(llm, max_attempts=2).discover("# 文档")


def test_evidence_organization_is_authorized_deduplicated_prioritized_and_bounded() -> (
    None
):
    fragments = [
        EvidenceFragment(1, "/self.md", "self", direct_mention=True),
        EvidenceFragment(2, "/denied.md", "denied", authorized=False),
        EvidenceFragment(3, "/semantic.md", "semantic-high", semantic_score=0.99),
        EvidenceFragment(4, "/direct.md", "direct-evidence", direct_mention=True),
        EvidenceFragment(4, "/direct.md", "direct-evidence", direct_mention=True),
        EvidenceFragment(
            5, "/reference.md", "explicit-reference", explicit_reference=True
        ),
    ]

    bundle = organize_evidence(
        fragments,
        target_file_id=1,
        max_total_chars=24,
        max_fragments=3,
        max_fragment_chars=16,
    )

    assert [item.document_file_id for item in bundle.fragments] == [4, 5]
    assert bundle.total_chars == 24
    assert bundle.fragments[1].content == "explicit-"
    assert bundle.discarded_count == 4
    assert bundle.warnings


def test_default_enrichment_evidence_budget_is_50k() -> None:
    fragments = [
        EvidenceFragment(
            1,
            "/docs/source.md",
            f"{index:02d}" + ("x" * 1_998),
            start=index,
            end=index + 1,
        )
        for index in range(1, 31)
    ]

    bundle = organize_evidence(fragments)

    assert bundle.total_chars == 50_000
    assert len(bundle.fragments) == 25


def test_semantic_overlap_is_merged_into_same_source_mention() -> None:
    shared = "OpenClaw 使用 memory_search 检索相关记忆。"
    bundle = organize_evidence(
        [
            EvidenceFragment(
                7,
                "/openclaw.md",
                f"## 记忆检索\n\n{shared}",
                direct_mention=True,
                explicit_reference=True,
                matched_topics=("记忆搜索",),
            ),
            EvidenceFragment(
                7,
                "/openclaw.md",
                shared,
                semantic_score=0.96,
                matched_topics=("记忆搜索",),
            ),
            EvidenceFragment(
                7,
                "/openclaw.md",
                shared,
                semantic_score=0.91,
                matched_topics=("上下文工程",),
            ),
        ]
    )

    assert len(bundle.fragments) == 1
    assert bundle.fragments[0].direct_mention is True
    assert bundle.fragments[0].semantic_score == 0.96
    assert bundle.fragments[0].matched_topics == ("记忆搜索", "上下文工程")
    assert bundle.fragments[0].content.count("memory_search") == 1


def test_same_source_mention_and_semantic_have_separate_minimum_quotas() -> None:
    bundle = organize_evidence(
        [
            EvidenceFragment(
                7,
                "/openclaw.md",
                "M" * 20,
                direct_mention=True,
                matched_topics=("上下文工程",),
            ),
            EvidenceFragment(
                7,
                "/openclaw.md",
                "S" * 20,
                semantic_score=0.9,
                matched_topics=("上下文工程",),
            ),
        ],
        max_total_chars=30,
        max_fragment_chars=20,
        min_topic_source_chars=10,
    )

    assert len(bundle.fragments) == 2
    assert bundle.total_chars == 30
    assert any(item.direct_mention for item in bundle.fragments)
    assert any(item.semantic_score > 0 for item in bundle.fragments)


def test_topic_source_quota_prevents_early_topic_from_consuming_budget() -> None:
    bundle = organize_evidence(
        [
            EvidenceFragment(
                7,
                "/openclaw.md",
                "A" * 20,
                semantic_score=1.0,
                matched_topics=("Prompt Engineering",),
            ),
            EvidenceFragment(
                7,
                "/openclaw.md",
                "B" * 20,
                semantic_score=0.8,
                matched_topics=("Harness Engineering",),
            ),
        ],
        max_total_chars=30,
        max_fragment_chars=20,
        min_topic_source_chars=10,
    )

    assert {item.matched_topics for item in bundle.fragments} == {
        ("Prompt Engineering",),
        ("Harness Engineering",),
    }


def test_adjacent_same_kind_fragments_are_coalesced_before_budgeting() -> None:
    bundle = organize_evidence(
        [
            EvidenceFragment(
                7,
                "/openclaw.md",
                "OpenClaw 的上下文由会话历史组成。",
                start=10,
                end=12,
                direct_mention=True,
                matched_topics=("上下文工程",),
            ),
            EvidenceFragment(
                7,
                "/openclaw.md",
                "系统会在接近阈值时压缩历史。",
                start=13,
                end=15,
                direct_mention=True,
                matched_topics=("上下文压缩",),
            ),
        ],
        max_fragment_chars=200,
    )

    assert len(bundle.fragments) == 1
    assert "会话历史" in bundle.fragments[0].content
    assert "压缩历史" in bundle.fragments[0].content
    assert bundle.fragments[0].matched_topics == ("上下文工程", "上下文压缩")


def test_equal_relevance_fill_uses_stable_source_order_not_lexical_density() -> None:
    bundle = organize_evidence(
        [
            EvidenceFragment(
                7,
                "/source.md",
                "这是一段普通说明文字。" * 4,
                semantic_score=0.8,
                matched_topics=("运行机制",),
            ),
            EvidenceFragment(
                7,
                "/source.md",
                "系统通过 3 个组件实现流程，但限制是延迟可能增加 20%。",
                semantic_score=0.8,
                matched_topics=("运行机制",),
            ),
        ],
        max_total_chars=30,
        max_fragments=1,
        max_fragment_chars=30,
    )

    assert len(bundle.fragments) == 1
    assert bundle.fragments[0].content.startswith("这是一段普通说明文字")


def test_claim_groups_preserve_topic_source_without_lexical_aspects() -> None:
    bundle = organize_evidence(
        [
            EvidenceFragment(
                7,
                "/openclaw.md",
                "OpenClaw 通过流水线实现压缩，用于长会话，但限制是会损失细节。",
                direct_mention=True,
                matched_topics=("上下文工程",),
            )
        ]
    )

    groups = build_evidence_claim_groups(bundle)

    assert len(groups) == 1
    assert groups[0].topic == "上下文工程"
    assert groups[0].source_id == "S1"
    assert groups[0].evidence_ids == ("F1",)
    assert groups[0].required is True


def test_claim_groups_aggregate_sources_by_topic_without_changing_evidence() -> None:
    bundle = organize_evidence(
        [
            EvidenceFragment(
                7,
                "/first.md",
                "OpenClaw 通过检索恢复记忆。",
                direct_mention=True,
                matched_topics=("记忆搜索",),
            ),
            EvidenceFragment(
                8,
                "/second.md",
                "OpenClaw 的时间衰减会影响排序。",
                semantic_score=0.9,
                matched_topics=("记忆搜索",),
            ),
        ]
    )

    groups = build_evidence_claim_groups(bundle)

    assert len(groups) == 1
    assert groups[0].source_ids == ("S1", "S2")
    assert groups[0].source_paths == ("/first.md", "/second.md")
    assert groups[0].evidence_ids == ("F1", "F2")


def test_quality_audit_requires_every_original_source_in_references() -> None:
    bundle = organize_evidence(
        [
            EvidenceFragment(
                7,
                "/first.md",
                "OpenClaw 通过检索恢复记忆。",
                direct_mention=True,
                matched_topics=("记忆搜索",),
            ),
            EvidenceFragment(
                8,
                "/second.md",
                "OpenClaw 的时间衰减会影响排序。",
                semantic_score=0.9,
                matched_topics=("记忆搜索",),
            ),
        ]
    )

    audit = audit_enriched_markdown(
        "# OpenClaw\n\n## 核心事实\n\nOpenClaw 支持记忆检索和排序。\n\n"
        "## 参考资料\n\n- [first.md](/first.md)",
        claim_groups=build_evidence_claim_groups(bundle),
    )

    assert audit.untraceable_source_group_ids == ("CG1",)
    assert audit.invalid_source_traceability_count == 1


def test_quality_audit_requires_original_source_in_references_not_body() -> None:
    evidence = organize_evidence(
        [
            EvidenceFragment(
                7,
                "/authorized.md",
                "Beta 通过检索恢复记忆。",
                direct_mention=True,
                matched_topics=("记忆检索",),
            )
        ]
    )
    groups = build_evidence_claim_groups(evidence)
    audit = audit_enriched_markdown(
        "# Beta\n\n## 记忆检索\n\nBeta 通过检索恢复记忆。[authorized](/authorized.md)",
        claim_groups=groups,
    )

    assert audit.untraceable_source_group_ids == ("CG1",)
    assert audit.invalid_source_traceability_count == 1
    assert audit.hard_original_reference_count == 1


def test_quality_audit_does_not_treat_natural_inline_attribution_as_hard_citation():
    evidence = organize_evidence(
        [
            EvidenceFragment(
                7,
                "/authorized.md",
                "Beta 通过检索恢复记忆。",
                direct_mention=True,
                matched_topics=("记忆检索",),
            )
        ]
    )

    audit = audit_enriched_markdown(
        "# Beta\n\n## 记忆检索\n\n"
        "[来源作者](/authorized.md) 将这一行为描述为按需恢复，"
        "Beta 据此实现记忆检索。\n\n"
        "## 参考资料\n\n- [authorized](/authorized.md)",
        claim_groups=build_evidence_claim_groups(evidence),
    )

    assert audit.untraceable_source_group_ids == ()
    assert audit.hard_original_reference_count == 0


def test_quality_audit_accepts_natural_chinese_reference_heading_variant() -> None:
    evidence = organize_evidence(
        [
            EvidenceFragment(
                7,
                "/authorized.md",
                "Beta 通过检索恢复记忆。",
                direct_mention=True,
                matched_topics=("记忆检索",),
            )
        ]
    )

    audit = audit_enriched_markdown(
        "# Beta\n\n## 记忆检索\n\nBeta 通过检索恢复记忆。\n\n"
        "## 资料参考\n\n- [authorized](/authorized.md)",
        claim_groups=build_evidence_claim_groups(evidence),
    )

    assert audit.untraceable_source_group_ids == ()
    assert audit.invalid_source_traceability_count == 0
    assert audit.hard_original_reference_count == 0


@pytest.mark.asyncio
async def test_enrich_uses_soft_template_pins_identity_and_discards_bad_relations() -> (
    None
):
    output = {
        "markdown": (
            "---\nentityName: 错误实体\n---\n"
            "# 错误实体\n\n## 实体定义与边界\n\n"
            "OSOT 是一个稳定理论。\n\n# 额外一级标题\n\n{{optional}}"
        ),
        "relations": [
            {
                "relationCode": "PART_OF",
                "targetFileId": 20,
                "targetEntityName": "ignored model name",
                "confidence": 1.2,
            },
            {
                "relationCode": "AFFECTS",
                "targetFileId": 20,
                "targetEntityName": "OSOT",
            },
            {
                "relationCode": "IS_A",
                "targetFileId": 999,
                "targetEntityName": "hallucinated",
            },
        ],
    }
    llm = _FakeLLM(json.dumps(output, ensure_ascii=False))
    enricher = KnowledgeEntityEnricher(llm)
    identity = KnowledgeEntityIdentity(
        file_id=10,
        knowledge_base_id=1,
        entity_name="OSOT-OCG",
        aliases=("OCG",),
    )
    evidence = [
        EvidenceFragment(
            100,
            "/papers/osot.md",
            "OCG 是 OSOT 理论的组成机制。",
            direct_mention=True,
        )
    ]

    result = await enricher.enrich(
        identity,
        evidence,
        soft_template=(
            "# {entityName}\n\n## 实体定义与边界\n\n## 核心事实\n\n## 可选章节"
        ),
        relation_targets=[RelationTarget(20, "OSOT")],
    )

    assert result.markdown.startswith("# OSOT-OCG\n")
    assert "entityName: 错误实体" not in result.markdown
    assert "## 额外一级标题" in result.markdown
    assert result.relations[0].relation_code is RelationCode.PART_OF
    assert result.relations[0].target_entity_name == "OSOT"
    assert result.relations[0].confidence == 1.0
    assert result.discarded_relation_count == 2
    assert result.missing_sections == ("核心事实", "可选章节")
    assert result.template_coverage == pytest.approx(1 / 3)
    assert result.placeholder_count == 1
    assert any("identity drift corrected" in warning for warning in result.warnings)
    assert any("invalid relationCode=AFFECTS" in warning for warning in result.warnings)
    assert any(
        "soft template section missing" in warning for warning in result.warnings
    )
    assert llm.calls[0][1] is True
    assert "模板是写作建议" in llm.calls[0][0][0]["content"]
    assert "sourceFileId=100" in llm.calls[0][0][1]["content"]
    assert "[osot.md](/papers/osot.md)" in llm.calls[0][0][1]["content"]
    assert "必须原样使用以下 Markdown" in llm.calls[0][0][1]["content"]


@pytest.mark.asyncio
async def test_enrich_prompt_groups_fragments_by_source_with_one_reference() -> None:
    llm = _FakeLLM(
        json.dumps(
            {
                "markdown": "# Beta\n\n## 核心事实\n\nSupported fact.",
                "relations": [],
                "warnings": [],
            }
        )
    )

    await KnowledgeEntityEnricher(llm).enrich(
        KnowledgeEntityIdentity(1, 1, "Beta"),
        [
            EvidenceFragment(2, "/same.md", "First fragment."),
            EvidenceFragment(2, "/same.md", "Second fragment."),
            EvidenceFragment(3, "/other.md", "Third fragment."),
        ],
    )

    user_prompt = llm.calls[0][0][1]["content"]
    assert user_prompt.count("[same.md](/same.md)") == 1
    assert user_prompt.count("[other.md](/other.md)") == 1
    assert "[S1] sourceFileId=2" in user_prompt
    assert "[F1]" in user_prompt
    assert "[F2]" in user_prompt


def test_preserve_existing_references_moves_omitted_links_to_reference_section():
    existing = (
        "# Beta\n\nOld claim [source A](/docs/a.md).\n\n"
        "Another claim [source B](/docs/b.md)."
    )
    generated = "# Beta\n\nUpdated claim [source A](/docs/a.md).\n"

    merged, restored = preserve_existing_references(existing, generated)

    assert restored == 1
    assert merged.count("/docs/a.md") == 1
    assert "## 参考资料\n\n- [source B](/docs/b.md)" in merged


def test_preserve_existing_references_treats_encoded_path_as_same_link():
    existing = "# Beta\n\nOld claim [source](/大厂文章/来源.md)."
    generated = "# Beta\n\nUpdated claim [source](/%E5%A4%A7%E5%8E%82%E6%96%87%E7%AB%A0/%E6%9D%A5%E6%BA%90.md).\n"

    merged, restored = preserve_existing_references(existing, generated)

    assert restored == 0
    assert merged == generated


def test_format_source_reference_uses_parent_title_for_generic_article_name():
    reference = format_source_reference("/大厂文章/有意义的文章标题/article.md")

    assert reference.startswith("[有意义的文章标题](")
    assert reference.endswith("/article.md)")


def test_generated_references_repair_single_source_and_remove_unknown_external():
    generated = (
        "# Entity\n\n[wrong](/invented/path.md) "
        "[external](https://invented.example/path)"
    )

    normalized, corrected, discarded = normalize_generated_references(
        generated,
        existing_markdown="# Entity",
        evidence=[EvidenceFragment(2, "/docs/source.md", "Supported fact.")],
    )

    assert corrected == 1
    assert discarded == 1
    assert "[wrong](/docs/source.md)" in normalized
    assert "[external]" not in normalized
    assert "external" in normalized
    assert "invented.example" not in normalized


def test_generated_references_move_trailing_original_link_to_references() -> None:
    generated = (
        "# Beta\n\n## 核心事实\n\n"
        "Beta 支持稳定检索。[source](/docs/source.md)\n\n"
        "[source](/docs/source.md) 对该机制给出了完整示例。"
    )

    normalized, corrected, discarded = normalize_generated_references(
        generated,
        existing_markdown="# Beta",
        evidence=[EvidenceFragment(2, "/docs/source.md", "Supported fact.")],
        identity=KnowledgeEntityIdentity(1, 1, "Beta"),
    )

    assert corrected == 1
    assert discarded == 0
    assert "Beta 支持稳定检索。\n\n" in normalized
    assert "[source](/docs/source.md) 对该机制给出了完整示例。" in normalized
    assert normalized.endswith("## 参考资料\n\n- [source](/docs/source.md)\n")


def test_generated_references_move_old_unrecalled_trailing_source_link() -> None:
    existing = "# Beta\n\nOld fact.[old source](/docs/old.md)"
    generated = "# Beta\n\n## 核心事实\n\nOld fact.[old source](/docs/old.md)"

    normalized, corrected, discarded = normalize_generated_references(
        generated,
        existing_markdown=existing,
        evidence=[EvidenceFragment(2, "/docs/new.md", "New fact.")],
        identity=KnowledgeEntityIdentity(1, 1, "Beta"),
    )

    assert corrected == 1
    assert discarded == 0
    assert "Old fact.\n\n## 参考资料" in normalized
    assert normalized.endswith("- [old source](/docs/old.md)\n")


def test_generated_references_link_longest_recalled_entity_names_first() -> None:
    generated = (
        "# Beta\n\n## 相关实体\n\n"
        "Microsoft Fabric IQ Ontology 可向 Microsoft Fabric 提供语义模型，"
        "也常简称为 Fabric IQ。"
    )
    evidence = [
        EvidenceFragment(
            2,
            "/KnowledgeEntity/Microsoft-Fabric-IQ-Ontology.md",
            "Ontology evidence.",
            document_kind="knowledgeEntity",
            source_entity_name="Microsoft Fabric IQ Ontology",
            source_entity_aliases=("Fabric IQ",),
        ),
        EvidenceFragment(
            3,
            "/KnowledgeEntity/Microsoft-Fabric.md",
            "Fabric evidence.",
            document_kind="knowledgeEntity",
            source_entity_name="Microsoft Fabric",
        ),
    ]

    normalized, corrected, discarded = normalize_generated_references(
        generated,
        existing_markdown="# Beta",
        evidence=evidence,
        identity=KnowledgeEntityIdentity(1, 1, "Beta"),
    )

    assert corrected == 2
    assert discarded == 0
    assert (
        "[Microsoft Fabric IQ Ontology]"
        "(/KnowledgeEntity/Microsoft-Fabric-IQ-Ontology.md)"
    ) in normalized
    assert "[Microsoft Fabric](/KnowledgeEntity/Microsoft-Fabric.md)" in normalized
    assert "也常简称为 Fabric IQ" in normalized


def test_generated_references_do_not_duplicate_existing_entity_link() -> None:
    generated = (
        "# Beta\n\n## 设计来源\n\n"
        "Beta 受到 [GBrain](/KnowledgeEntity/GBrain.md) 启发，"
        "后续仍可与 GBrain 对比。"
    )

    normalized, corrected, discarded = normalize_generated_references(
        generated,
        existing_markdown="# Beta",
        evidence=[
            EvidenceFragment(
                2,
                "/KnowledgeEntity/GBrain.md",
                "GBrain evidence.",
                document_kind="knowledgeEntity",
                source_entity_name="Garry's Opinionated Agent Brain",
                source_entity_aliases=("GBrain",),
            )
        ],
        identity=KnowledgeEntityIdentity(1, 1, "Beta"),
    )

    assert corrected == 0
    assert discarded == 0
    assert normalized.count("](/KnowledgeEntity/GBrain.md)") == 1


@pytest.mark.asyncio
async def test_enrich_prompt_uses_topics_as_clustered_coverage_guidance():
    llm = _FakeLLM(
        json.dumps(
            {
                "markdown": (
                    "# Beta\n\n## 核心事实\n\nSupported fact.\n\n"
                    "## 参考资料\n\n- [source.md](/source.md)"
                ),
                "relations": [],
                "warnings": [],
            }
        )
    )

    await KnowledgeEntityEnricher(llm).enrich(
        KnowledgeEntityIdentity(1, 1, "Beta"),
        [EvidenceFragment(2, "/source.md", "Supported fact.")],
        topics=("Architecture", "Retrieval", "Deployment"),
    )

    system_prompt = llm.calls[0][0][0]["content"]
    user_prompt = llm.calls[0][0][1]["content"]
    normalized_prompt = " ".join(system_prompt.split())
    assert "可追溯不等于每段都要添加引用" in normalized_prompt
    assert "连续段落或列表项末尾" in normalized_prompt
    assert "应合并重叠 Topic" in user_prompt
    assert "- Architecture\n- Retrieval\n- Deployment" in user_prompt
    assert "EvidenceClaimGroup" not in user_prompt
    assert "claimGroupId" not in user_prompt
    assert "claimCoverage" not in system_prompt
    assert "citationPlan" not in system_prompt
    assert "sourceType=originalDocument" in user_prompt


@pytest.mark.asyncio
async def test_knowledge_entity_source_is_linked_naturally_in_body():
    llm = _FakeLLM(
        json.dumps(
            {
                "markdown": (
                    "# Beta\n\n## 设计来源\n\n"
                    "Beta 的记忆编排受到了 [GBrain](/KnowledgeEntity/GBrain.md) "
                    "分层思路的启发，并针对自身任务边界进行了调整。"
                ),
                "relations": [],
                "warnings": [],
            },
            ensure_ascii=False,
        )
    )

    result = await KnowledgeEntityEnricher(llm).enrich(
        KnowledgeEntityIdentity(1, 1, "Beta"),
        [
            EvidenceFragment(
                2,
                "/KnowledgeEntity/GBrain.md",
                "GBrain 使用分层记忆编排。",
                direct_mention=True,
                document_kind="knowledgeEntity",
            )
        ],
    )

    user_prompt = llm.calls[0][0][1]["content"]
    assert "sourceType=knowledgeEntity" in user_prompt
    assert "[GBrain](/KnowledgeEntity/GBrain.md)" in user_prompt
    assert "EvidenceClaimGroup" not in user_prompt
    assert result.quality_audit.untraceable_source_group_ids == ()
    assert result.quality_audit.hard_original_reference_count == 0


@pytest.mark.asyncio
async def test_enrich_adds_missing_recalled_knowledge_entity_link():
    llm = _FakeLLM(
        json.dumps(
            {
                "markdown": (
                    "# Beta\n\n## 对比\n\n"
                    "Beta 与 GBrain 都采用持久化记忆，但任务边界不同。"
                ),
                "relations": [],
                "warnings": [],
            },
            ensure_ascii=False,
        )
    )

    result = await KnowledgeEntityEnricher(llm).enrich(
        KnowledgeEntityIdentity(1, 1, "Beta"),
        [
            EvidenceFragment(
                2,
                "/KnowledgeEntity/GBrain.md",
                "GBrain 使用持久化记忆。",
                document_kind="knowledgeEntity",
                source_entity_name="Garry's Opinionated Agent Brain",
                source_entity_aliases=("GBrain",),
            )
        ],
    )

    assert (
        "Beta 与 [GBrain](/KnowledgeEntity/GBrain.md) 都采用持久化记忆"
        in result.markdown
    )
    assert result.quality_audit.untraceable_source_group_ids == ()


@pytest.mark.asyncio
async def test_enrich_does_not_add_a_semantic_quality_repair_call():
    first = {
        "markdown": "# Beta\n\n## 核心事实\n\nBeta 是一个系统。",
        "relations": [],
        "warnings": [],
    }
    llm = _FakeLLM(json.dumps(first, ensure_ascii=False))

    result = await KnowledgeEntityEnricher(llm).enrich(
        KnowledgeEntityIdentity(1, 1, "Beta"),
        [
            EvidenceFragment(
                2,
                "/source.md",
                "Beta 通过流水线处理请求，适用于批量任务，但限制是延迟可能增加。",
                direct_mention=True,
                matched_topics=("运行机制",),
            )
        ],
        topics=("运行机制",),
    )

    assert result.attempts == 1
    assert result.quality_audit.untraceable_source_group_ids == ("CG1",)
    assert result.quality_audit.hard_original_reference_count == 0
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_incremental_prompt_treats_new_evidence_as_non_exhaustive_delta():
    llm = _FakeLLM(
        json.dumps(
            {
                "markdown": "# Beta\n\n## 核心事实\n\nOld and new facts.",
                "relations": [],
                "warnings": [],
            }
        )
    )

    await KnowledgeEntityEnricher(llm).enrich(
        KnowledgeEntityIdentity(1, 1, "Beta"),
        [EvidenceFragment(2, "/new.md", "New fact.")],
        existing_markdown="# Beta\n\n## 核心事实\n\nOld fact [old](/old.md).",
        incremental=True,
    )

    user_prompt = " ".join(llm.calls[0][0][1]["content"].split())
    assert "更新模式：incremental（增量）" in user_prompt
    assert "新证据只是本轮增量，不是完整替代语料" in user_prompt
    assert "绝不是删除它的理由" in user_prompt


@pytest.mark.asyncio
async def test_enrich_prompt_keeps_complete_existing_markdown_without_truncation():
    llm = _FakeLLM(
        json.dumps(
            {
                "markdown": "# Beta\n\n## 核心事实\n\nUpdated.",
                "relations": [],
                "warnings": [],
            }
        )
    )
    final_marker = "OLD-DOCUMENT-FINAL-MARKER"
    existing = f"# Beta\n\n{'old fact. ' * 8_000}\n\n{final_marker}"

    await KnowledgeEntityEnricher(llm).enrich(
        KnowledgeEntityIdentity(1, 1, "Beta"),
        [EvidenceFragment(2, "/new.md", "New fact.")],
        existing_markdown=existing,
    )

    user_prompt = llm.calls[0][0][1]["content"]
    assert existing in user_prompt
    assert final_marker in user_prompt


@pytest.mark.asyncio
async def test_enrich_does_not_call_llm_without_usable_evidence() -> None:
    llm = _FakeLLM("should not be called")
    identity = KnowledgeEntityIdentity(1, 1, "OSOT")

    with pytest.raises(KnowledgeEntityOutputError, match="authorized evidence"):
        await KnowledgeEntityEnricher(llm).enrich(
            identity,
            [EvidenceFragment(2, "/denied.md", "secret", authorized=False)],
        )

    assert llm.calls == []


@pytest.mark.asyncio
async def test_openai_compatible_client_uses_core_provider_and_injected_http_client() -> (
    None
):
    captured: dict[str, Any] = {}

    class _Provider:
        async def get_config(self, model_type: str | LLMModelProfile) -> ModelConfig:
            captured["profile"] = model_type
            return ModelConfig(
                model_name="knowledge-model",
                temperature=0.1,
                base_url="https://llm.example/v1/",
                api_key="secret",
                extra_body={"thinking": {"type": "disabled"}},
            )

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": '{"ok":true}'}}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: Mapping[str, str],
            **kwargs: Any,
        ) -> _Response:
            captured.update(
                url=url, headers=dict(headers), payload=dict(kwargs["json"])
            )
            return _Response()

    def client_factory(*, timeout: float) -> _Client:
        captured["timeout"] = timeout
        return _Client()

    client = OpenAICompatibleKnowledgeEntityLLM(
        provider=_Provider(),
        temperature=0.0,
        timeout=12.5,
        client_factory=client_factory,
    )

    result = await client.complete(
        [{"role": "user", "content": "discover"}], json_mode=True
    )

    assert result == '{"ok":true}'
    assert captured["profile"] is LLMModelProfile.STANDARD
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["timeout"] == 12.5
    assert captured["payload"] == {
        "thinking": {"type": "disabled"},
        "model": "knowledge-model",
        "temperature": 0.0,
        "messages": [{"role": "user", "content": "discover"}],
        "response_format": {"type": "json_object"},
    }


@pytest.mark.asyncio
async def test_discovery_client_uses_lightweight_provider_config() -> None:
    captured: dict[str, Any] = {}

    class _Provider:
        async def get_config(self, model_type: str | LLMModelProfile) -> ModelConfig:
            captured["profile"] = model_type
            return ModelConfig(
                model_name="knowledge-model",
                temperature=0.8,
                base_url="https://llm.example/v1",
                api_key="",
                extra_body={
                    "thinking": {"type": "disabled"},
                    "reasoning_effort": "high",
                },
            )

    identity = json.loads(
        await build_discovery_llm(provider=_Provider()).cache_identity()
    )

    assert captured["profile"] is LLMModelProfile.LIGHTWEIGHT
    assert identity["temperature"] == 0.8
    assert identity["extraBody"] == {
        "thinking": {"type": "disabled"},
        "reasoning_effort": "high",
    }


@pytest.mark.asyncio
async def test_enrichment_client_uses_standard_provider_config() -> None:
    captured: dict[str, Any] = {}

    class _Provider:
        async def get_config(self, model_type: str | LLMModelProfile) -> ModelConfig:
            captured["profile"] = model_type
            return ModelConfig(
                model_name="knowledge-model",
                temperature=0.8,
                base_url="https://llm.example/v1",
                api_key="",
                extra_body={
                    "thinking": {"type": "disabled"},
                    "reasoning_effort": "high",
                },
            )

    identity = json.loads(
        await build_enrichment_llm(provider=_Provider()).cache_identity()
    )

    assert captured["profile"] is LLMModelProfile.STANDARD
    assert identity["temperature"] == 0.8
    assert identity["extraBody"] == {
        "thinking": {"type": "disabled"},
        "reasoning_effort": "high",
    }


@pytest.mark.asyncio
async def test_openai_compatible_client_uses_five_minute_default_timeout_and_logs_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    debug_messages: list[str] = []

    class _Provider:
        async def get_config(self, model_type: str | LLMModelProfile) -> ModelConfig:
            del model_type
            return ModelConfig(
                model_name="knowledge-model",
                temperature=0.1,
                base_url="https://llm.example/v1",
                api_key="secret-api-key",
            )

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "done"}}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _Response:
            captured["debug_logged_before_post"] = bool(debug_messages)
            return _Response()

    def client_factory(*, timeout: float) -> _Client:
        captured["timeout"] = timeout
        return _Client()

    def capture_debug(message: str, *args: Any, **_kwargs: Any) -> None:
        debug_messages.append(message % args)

    monkeypatch.setattr(intelligence_module.logger, "debug", capture_debug)
    client = OpenAICompatibleKnowledgeEntityLLM(
        provider=_Provider(), client_factory=client_factory
    )
    messages = [
        {"role": "system", "content": "你是知识实体助手"},
        {"role": "user", "content": "请分析最终文档"},
    ]

    result = await client.complete(messages, json_mode=True)

    assert result == "done"
    assert captured == {"timeout": 300.0, "debug_logged_before_post": True}
    assert len(debug_messages) == 2
    assert "model=knowledge-model" in debug_messages[0]
    assert "json_mode=True" in debug_messages[0]
    assert "message_count=2" in debug_messages[0]
    assert "message_chars=[8, 7]" in debug_messages[0]
    assert json.dumps(messages, ensure_ascii=False) not in debug_messages[0]
    assert "secret-api-key" not in debug_messages[0]
    assert "Authorization" not in debug_messages[0]
    assert "elapsed_ms=" in debug_messages[1]
    assert "content_chars=4" in debug_messages[1]
    assert "reasoning_chars=0" in debug_messages[1]


@pytest.mark.asyncio
async def test_openai_compatible_client_wraps_http_failures() -> None:
    class _Provider:
        async def get_config(
            self,
            model_type: str,  # pylint: disable=unused-argument
        ) -> ModelConfig:
            return ModelConfig("m", 0.0, "https://llm.example/v1", "")

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> Any:
            raise httpx.ConnectError("down")

    client = OpenAICompatibleKnowledgeEntityLLM(
        provider=_Provider(), client_factory=lambda **_kwargs: _Client()
    )

    with pytest.raises(KnowledgeEntityLLMError, match="request failed"):
        await client.complete([{"role": "user", "content": "x"}])
