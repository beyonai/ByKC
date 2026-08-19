"""KnowledgeEntity discovery models, prompts, normalization, and LLM workflow."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from by_qa.core import logger
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    _SPACE_RE,
    MAX_DISCOVERED_ENTITIES,
    DiscoveryDocumentContext,
    IdentityScope,
    KnowledgeEntityLLM,
    _complete_strict_json,
    _safe_log_context,
    build_discovery_context,
    normalize_surface,
)

DISCOVERY_SYSTEM_PROMPT = """\
你是文档级核心对象实体发现器，不是词语扫描器或事件抽取器。本次固定发现 KnowledgeEntity v1。

请先在内部完成以下判断，再只输出最终 JSON：
1. 识别文档的信息任务、直接研究对象、结构和结论。
2. 对每个候选回答：它是否拥有跨时间、跨文档可复用的稳定身份？如果候选描述的是一次发生、变化或状态事实，则 isEvent=true，不得作为实体输出。
3. 判断身份范围：只有可脱离本文独立引用、跨语境仍指向同一对象的候选才是 global；由本文研究对象定义、命名、组织或拥有的组成、机制、层级、角色、分类和内部工具都属于 subject，即使它有英文名或缩写也不例外。
4. 按重要性排序：直接研究对象、统领多个章节的上位对象、主要组成或类别、结论不可缺少的主体，优先于案例、工具、实现细节和偶然提及者。
5. 做删除测试：删除后不影响理解全文主题、主要结构、关键关系或结论的候选应删除。

通用约束：
- 实体发现只回答“它是谁/是什么”，不回答“发生了什么”。事件无论重要与否都不创建实体。
- 文档标题是研究对象的强信号，但标题本身、来源名、作者名或文件名不能自动成为实体或局部主语。
- 名称反复出现不等于重要；示例、引用、名单、依赖、字段、函数和实现细节不得挤占上位对象或关键结论。
- 若文档围绕一个直接研究对象展开，先输出该对象；文中专门为它介绍的内部候选默认归属于它。
- 主体档案优先保留决定身份、所有权或治理关系的主体；概念性资料优先保留并列章节反复定义的一级概念和总结结论。
- 关系名、属性名、角色名、泛称集合和普通章节标签本身不是具名对象。
- 最终覆盖检查先覆盖主要章节和总结结论；若已选案例或实现项却遗漏一级主题或结论，用后者替换前者。
- 明确作为第三方独立存在、在其他资料中仍可直接识别的对象保持 global。
- 技术分析优先显式具名的类、引擎、注册表、协议和 DSL，不要让内部数据库表或基础设施依赖挤占名额。
- 规范、策略或风格指南中，应把“被规范的稳定对象”作为主体，而不是把文档标题或“某某规范/指南/报告”当成主体；跨完整章节定义可复用约束的一级规则域是该主体的 subject 实体，应优先于 front matter 中的外部模板或素材。
- 若规范包含六个以上一级规则域，通常输出 8—12 个实体；先覆盖结构、媒介与格式、表达、标签、合规、互动和渠道差异等主要规则域，再考虑品牌、对比对象或外部引用。规则域的 localName 优先沿用一级章节中的原文名称，不要改写成近义词。
- 单主题资料通常输出 1—5 个，包含多个独立主题或复杂结构的资料输出 5—12 个，绝不超过 12 个。

身份与命名协议：
- global：subjectEntityName=""，localName 为稳定名称，entityName 必须等于 localName。
- subject：subjectEntityName 必须是文中明确存在、可独立识别的稳定主体；localName 是其内部局部概念；entityName 必须严格等于 subjectEntityName-localName，使用半角连字符且两侧无空格。
- 每个 subjectEntityName 必须也作为一条 global 实体出现在同一数组中。
- 同一对象同时出现缩写和解释性全称时，身份字段使用文中最常用、最简洁且无歧义的原文名称，其他形式放入 aliases。
- 如果局部概念没有稳定主体，或只是主体的普通描述，不输出。
- 每项必须令 isEvent=false；evidence 必须是能说明候选重要性及其稳定身份的连续原文。
- 已有词表只在抽取后做身份解析，不影响候选选择；同一内容不应因外部词表状态改变实体集。

抽象示例（名称均为虚构，仅说明协议）：
- 一篇文章研究“系统甲”，并介绍它自己的“模块乙”和“恢复机制”。输出“系统甲”（global）、“系统甲-模块乙”（subject）、“系统甲-恢复机制”（subject）。
- 一份机构档案列出具名负责人“人物甲”。输出机构和“人物甲”两个 global 实体，不输出“机构-人物甲”。

只输出严格 JSON 数组，每项只包含 entityName、subjectEntityName、localName、identityScope、isEvent、evidence、aliases。按重要性从高到低排列，不要输出解释或 Markdown。
""".strip()


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    """Normalized, non-persisted candidate returned by discovery."""

    entity_name: str
    local_name: str
    identity_scope: IdentityScope
    evidence: str
    aliases: tuple[str, ...] = ()
    subject_entity_name: str | None = None
    subject_file_id: int | None = None
    entity_type: str | None = None
    identity_reason: str | None = None
    salience_reason: str | None = None

    @property
    def identity_key(self) -> str:
        if self.identity_scope is IdentityScope.SUBJECT:
            subject_key = (
                str(self.subject_file_id)
                if self.subject_file_id is not None
                else normalize_surface(self.subject_entity_name or "")
            )
            return f"s:{subject_key}:{normalize_surface(self.local_name)}"
        return f"g:{normalize_surface(self.entity_name)}"


@dataclass(frozen=True, slots=True)
class EntityDiscoveryResult:
    candidates: tuple[EntityCandidate, ...]
    warnings: tuple[str, ...]
    attempts: int
    context: DiscoveryDocumentContext


class KnowledgeEntityDiscovery:
    """LLM candidate discovery with deterministic normalization and filtering."""

    def __init__(
        self,
        llm: KnowledgeEntityLLM,
        *,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.0,
        cache_size: int = 128,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if cache_size < 0:
            raise ValueError("cache_size must not be negative")
        self._llm = llm
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._cache_size = cache_size
        self._result_cache: OrderedDict[str, EntityDiscoveryResult] = OrderedDict()
        self._sleep = sleep

    async def discover(
        self,
        markdown: str,
        *,
        max_entities: int = MAX_DISCOVERED_ENTITIES,
        log_context: Mapping[str, Any] | None = None,
    ) -> EntityDiscoveryResult:
        discovery_started_at = time.perf_counter()
        context = build_discovery_context(markdown)
        bounded_max_entities = min(max(max_entities, 1), MAX_DISCOVERED_ENTITIES)
        model_identity = await _llm_cache_identity(self._llm)
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "prompt": DISCOVERY_SYSTEM_PROMPT,
                    "context": context.excerpt,
                    "maxEntities": bounded_max_entities,
                    "modelIdentity": model_identity,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        cached = self._result_cache.get(cache_key)
        if cached is not None:
            self._result_cache.move_to_end(cache_key)
            logger.info(
                "knowledge_entity_intelligence discovery cache hit: "
                "candidate_count=%s context_truncated=%s%s",
                len(cached.candidates),
                cached.context.truncated,
                _safe_log_context(log_context),
            )
            return cached
        logger.info(
            "knowledge_entity_intelligence discovery started: document_chars=%s "
            "max_entities=%s context_truncated=%s%s",
            len(markdown),
            max_entities,
            context.truncated,
            _safe_log_context(log_context),
        )
        base_messages: list[dict[str, str]] = [
            {"role": "system", "content": DISCOVERY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": context.excerpt,
            },
        ]
        raw_items, attempts = await _complete_strict_json(
            self._llm,
            base_messages,
            expected_type=list,
            max_attempts=self._max_attempts,
            retry_backoff_seconds=self._retry_backoff_seconds,
            sleep=self._sleep,
            operation="discovery",
            log_context=log_context,
        )
        candidates, warnings = normalize_entity_candidates(
            raw_items,
            max_entities=bounded_max_entities,
            source_text=context.excerpt,
        )
        result = EntityDiscoveryResult(
            candidates=candidates,
            warnings=warnings,
            attempts=attempts,
            context=context,
        )
        if self._cache_size:
            self._result_cache[cache_key] = result
            self._result_cache.move_to_end(cache_key)
            while len(self._result_cache) > self._cache_size:
                self._result_cache.popitem(last=False)
        logger.info(
            "knowledge_entity_intelligence discovery completed: "
            "candidate_count=%s warning_count=%s attempts=%s elapsed_ms=%.2f%s",
            len(result.candidates),
            len(result.warnings),
            result.attempts,
            (time.perf_counter() - discovery_started_at) * 1000,
            _safe_log_context(log_context),
        )
        return result


def normalize_entity_candidates(
    raw_items: Sequence[Any],
    *,
    max_entities: int = MAX_DISCOVERED_ENTITIES,
    source_text: str | None = None,
) -> tuple[tuple[EntityCandidate, ...], tuple[str, ...]]:
    """Normalize identity fields and reject mentions, events, and facts."""

    warnings: list[str] = []
    preliminary: list[EntityCandidate] = []
    normalized_source = (
        normalize_surface(source_text) if source_text is not None else None
    )
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            warnings.append(f"candidate[{index}] discarded: expected object")
            continue
        kind = _text(item, "candidateKind", "candidate_kind", "kind").casefold()
        if kind and kind not in {"entity", "knowledgeentity", "knowledge_entity"}:
            warnings.append(f"candidate[{index}] discarded: kind={kind}")
            continue
        if _truthy(item, "isEvent", "is_event") or _truthy(item, "isFact", "is_fact"):
            warnings.append(f"candidate[{index}] discarded: event_or_fact")
            continue
        if _has_explicit_false(item, "stableIdentity", "stable_identity", "isStable"):
            warnings.append(f"candidate[{index}] discarded: unstable_identity")
            continue
        raw_scope = _text(item, "identityScope", "identity_scope", "scope").casefold()
        if raw_scope not in {IdentityScope.GLOBAL, IdentityScope.SUBJECT}:
            warnings.append(f"candidate[{index}] discarded: invalid_scope")
            continue
        scope = IdentityScope(raw_scope)
        entity_name = _clean_name(
            _text(item, "entityName", "entity_name", "termName", "term_name")
        )
        local_name = _clean_name(_text(item, "localName", "local_name"))
        subject_name = _clean_name(
            _text(
                item,
                "subjectEntityName",
                "subject_entity_name",
                "subjectName",
                "subject_name",
            )
        )
        subject_file_id = _optional_positive_int(
            item.get("subjectFileId", item.get("subject_file_id"))
        )
        if scope is IdentityScope.GLOBAL:
            local_name = local_name or entity_name
            entity_name = local_name
            subject_name = ""
            subject_file_id = None
        else:
            if not local_name:
                prefix = f"{subject_name}-" if subject_name else ""
                local_name = (
                    entity_name[len(prefix) :]
                    if prefix and entity_name.startswith(prefix)
                    else ""
                )
            if not local_name or (not subject_name and subject_file_id is None):
                warnings.append(
                    f"candidate[{index}] discarded: invalid_subject_identity"
                )
                continue
            if subject_name:
                entity_name = f"{subject_name}-{local_name}"
            elif not entity_name:
                entity_name = local_name
        if not entity_name:
            warnings.append(f"candidate[{index}] discarded: missing_name")
            continue
        aliases = _normalize_aliases(item.get("aliases"), entity_name)
        evidence = _SPACE_RE.sub(" ", _text(item, "evidence")).strip()
        evidence_is_valid = bool(evidence) and (
            normalized_source is None
            or normalize_surface(evidence) in normalized_source
        )
        if not evidence_is_valid and source_text is not None:
            repaired_evidence = _find_source_evidence(
                source_text,
                (entity_name, local_name, *aliases),
            )
            if repaired_evidence:
                evidence = repaired_evidence
                warnings.append(f"candidate[{index}] evidence repaired from document")
                evidence_is_valid = True
        if not evidence_is_valid:
            reason = "missing_evidence" if not evidence else "evidence_not_in_document"
            warnings.append(f"candidate[{index}] discarded: {reason}")
            continue
        preliminary.append(
            EntityCandidate(
                entity_name=entity_name,
                local_name=local_name,
                identity_scope=scope,
                evidence=evidence,
                aliases=aliases,
                subject_entity_name=subject_name or None,
                subject_file_id=subject_file_id,
                entity_type=_optional_text(item, "entityType", "entity_type"),
                identity_reason=_optional_text(
                    item, "identityReason", "identity_reason"
                ),
                salience_reason=_optional_text(
                    item, "salienceReason", "salience_reason"
                ),
            )
        )

    global_names = {
        normalize_surface(candidate.entity_name)
        for candidate in preliminary
        if candidate.identity_scope is IdentityScope.GLOBAL
    }
    filtered: list[EntityCandidate] = []
    seen: set[str] = set()
    for candidate in preliminary:
        if candidate.identity_scope is IdentityScope.SUBJECT:
            parent_name = normalize_surface(candidate.subject_entity_name or "")
            if not parent_name or parent_name not in global_names:
                warnings.append(
                    f"candidate {candidate.entity_name!r} discarded: subject_not_stable"
                )
                continue
        if candidate.identity_key in seen:
            warnings.append(
                f"candidate {candidate.entity_name!r} discarded: duplicate_identity"
            )
            continue
        seen.add(candidate.identity_key)
        filtered.append(candidate)

    parent_names = {
        normalize_surface(candidate.subject_entity_name or "")
        for candidate in filtered
        if candidate.identity_scope is IdentityScope.SUBJECT
        and candidate.subject_entity_name
    }
    parent_first = [
        candidate
        for candidate in filtered
        if candidate.identity_scope is IdentityScope.GLOBAL
        and normalize_surface(candidate.entity_name) in parent_names
    ]
    ordered = parent_first + [
        candidate for candidate in filtered if candidate not in parent_first
    ]
    limited = ordered[: min(max(max_entities, 1), MAX_DISCOVERED_ENTITIES)]
    if len(ordered) > len(limited):
        warnings.append(
            f"candidate list truncated from {len(ordered)} to {len(limited)}"
        )
    return tuple(limited), tuple(warnings)


def _text(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _optional_text(item: Mapping[str, Any], *keys: str) -> str | None:
    value = _text(item, *keys)
    return value or None


def _clean_name(value: str) -> str:
    return _SPACE_RE.sub(" ", value or "").strip(" \t\r\n-")


def _find_source_evidence(source_text: str, surfaces: Sequence[str]) -> str | None:
    """Return the first real source line containing a candidate surface."""

    normalized_surfaces = {
        normalize_surface(surface) for surface in surfaces if normalize_surface(surface)
    }
    if not normalized_surfaces:
        return None
    for raw_line in source_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("[文档"):
            continue
        normalized_line = normalize_surface(line)
        if any(surface in normalized_line for surface in normalized_surfaces):
            return line
    return None


async def _llm_cache_identity(llm: KnowledgeEntityLLM) -> str:
    """Resolve an optional model/config identity without requiring it from fakes."""

    provider = getattr(llm, "cache_identity", None)
    if provider is None:
        return f"{type(llm).__module__}.{type(llm).__qualname__}"
    value = provider()
    if inspect.isawaitable(value):
        value = await value
    return str(value)


def _truthy(item: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        value = item.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().casefold() in {"true", "yes", "1"}:
            return True
    return False


def _has_explicit_false(item: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        if key not in item:
            continue
        value = item[key]
        if value is False:
            return True
        if isinstance(value, str) and value.strip().casefold() in {"false", "no", "0"}:
            return True
    return False


def _normalize_aliases(raw: Any, entity_name: str) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    aliases: list[str] = []
    seen = {normalize_surface(entity_name)}
    for value in raw:
        alias = _clean_name(str(value))
        key = normalize_surface(alias)
        if alias and key not in seen:
            aliases.append(alias)
            seen.add(key)
    return tuple(aliases)


def _optional_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


__all__ = [
    "DISCOVERY_SYSTEM_PROMPT",
    "DiscoveryDocumentContext",
    "EntityCandidate",
    "EntityDiscoveryResult",
    "KnowledgeEntityDiscovery",
    "build_discovery_context",
    "normalize_entity_candidates",
]
