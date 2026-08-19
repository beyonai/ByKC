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
    normalize_entity_candidates,
)
from by_qa.knowledge_base.services.knowledge_entity_enrichment import (
    EvidenceFragment,
    KnowledgeEntityEnricher,
    KnowledgeEntityIdentity,
    RelationTarget,
    organize_evidence,
)
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    IdentityScope,
    KnowledgeEntityLLMError,
    KnowledgeEntityOutputError,
    OpenAICompatibleKnowledgeEntityLLM,
    RelationCode,
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
    llm = _FakeLLM("```json\n[]\n```", json.dumps(raw_candidates, ensure_ascii=False))
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


def test_candidate_normalization_requires_a_stable_subject_and_caps_at_twelve() -> None:
    raw = [
        {
            "entityName": "无主体-局部概念",
            "localName": "局部概念",
            "subjectEntityName": "无主体",
            "identityScope": "subject",
            "evidence": "一段证据",
        },
        *[
            {
                "entityName": f"稳定实体{i}",
                "localName": f"稳定实体{i}",
                "identityScope": "global",
                "evidence": f"稳定实体{i}的证据",
            }
            for i in range(15)
        ],
    ]

    candidates, warnings = normalize_entity_candidates(raw)

    assert len(candidates) == 12
    assert all(candidate.entity_name != "无主体-局部概念" for candidate in candidates)
    assert any("subject_not_stable" in warning for warning in warnings)
    assert any("truncated" in warning for warning in warnings)


def test_candidate_normalization_repairs_paraphrased_evidence_from_source() -> None:
    candidates, warnings = normalize_entity_candidates(
        [
            {
                "entityName": "OSOT",
                "localName": "OSOT",
                "identityScope": "global",
                "evidence": "OSOT 是一套核心理论。",
            }
        ],
        source_text="文档原文只说：OSOT 是一种方法。",
    )

    assert [candidate.evidence for candidate in candidates] == [
        "文档原文只说：OSOT 是一种方法。"
    ]
    assert warnings == ("candidate[0] evidence repaired from document",)


def test_candidate_normalization_discards_invalid_evidence_without_name_mention() -> (
    None
):
    candidates, warnings = normalize_entity_candidates(
        [
            {
                "entityName": "OSOT",
                "localName": "OSOT",
                "identityScope": "global",
                "evidence": "OSOT 是一套核心理论。",
            }
        ],
        source_text="这段原文没有提到候选实体。",
    )

    assert candidates == ()
    assert warnings == ("candidate[0] discarded: evidence_not_in_document",)


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
    assert "template is writing guidance" in llm.calls[0][0][0]["content"]
    assert "sourceFileId=100" in llm.calls[0][0][1]["content"]
    assert "[osot.md](/papers/osot.md)" in llm.calls[0][0][1]["content"]
    assert "exact Markdown source reference" in llm.calls[0][0][0]["content"]


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
    assert len(debug_messages) == 1
    assert "model=knowledge-model" in debug_messages[0]
    assert "json_mode=True" in debug_messages[0]
    assert "message_count=2" in debug_messages[0]
    assert json.dumps(messages, ensure_ascii=False) in debug_messages[0]
    assert "secret-api-key" not in debug_messages[0]
    assert "Authorization" not in debug_messages[0]


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
