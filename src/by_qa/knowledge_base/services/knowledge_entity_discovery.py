"""Document-level KnowledgeEntity and Topic discovery.

The model, worker, and evaluator share this one protocol. It deliberately has no
persistence identity, subject scope, entity type, confidence, or copied evidence.
"""

from __future__ import annotations

import asyncio
import bisect
import hashlib
import heapq
import inspect
import json
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from by_qa.core import logger
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    DISCOVERY_CONTEXT_CHARS,
    MAX_DISCOVERED_ENTITIES,
    KnowledgeEntityLLM,
    KnowledgeEntityOutputError,
    _parse_json_output,
    _safe_log_context,
    normalize_surface,
)

MAX_DISCOVERED_TOPICS = 24
MAX_EVIDENCE_SUMMARY_CODEPOINTS = 160
MAX_ENTITY_DESCRIPTION_CODEPOINTS = 160
DISCOVERY_PROTOCOL_VERSION = "entity-topic/2.2"

DISCOVERY_SYSTEM_PROMPT = """\
你是文档级 Entity/Topic 发现器，不是专有名词、章节、事件、事实或关系抽取器。

首要目标是 precision：宁可遗漏，不要把可疑对象升为 Entity 或 Topic。任一核心条件不确定就不输出；entities 和 topics 都可为空，不得为接近数量上限而凑数。

KnowledgeEntity 是脱离当前原材料后，仍能被稳定、基本无歧义地指向，可在多篇文档中持续归并和维护的对象。不需要也不允许猜测 Entity 类型。
Topic 是依赖某个 Entity 才成立的稳定描述方向，用于限定该 Entity 的证据召回；Topic 没有独立身份，不是低等级 Entity。

处理顺序：
1. 结合 documentOverview 和全文分布的 documentEvidence，识别文档的信息任务、核心研究对象和并列研究对象。不得仅凭文件名、文档类型或章节名决定输出。
2. 先冻结 Entity 集和候选角色，再选 Topic，最后选规范名；名称显眼或有独立章节不得反向改变角色判断。

Entity 精度门：以下条件必须全部成立。
1. 独立身份：假设删除当前原材料及其临时语境，该名称仍能在预期知识范围内基本无歧义地指向同一对象。宽泛、多领域或依赖当前 owner 才能确定含义的名称不通过；例如“熔断机制”在金融和计算机领域含义不同，单独使用不是基本无歧义的 Entity。
2. 稳定指代：不是作者为本文临时建立的修辞名、分组名、局部角色或一次性标签。文档明确说“将 X 归纳/分为 Y”或“分类单位是 X”时，Y 默认是分类标签而非 Entity，优先对 X 及其具体成员做本精度门判断。只有 Y 在本文分类之外也有公认、稳定的独立身份时才例外。
3. 直接研究：文档直接给出该对象的定义、能力/机制、边界、局限或变化，而不是只作背景、名单项、依赖、例子或单项对比。
4. 独立知识：只依靠本文证据能形成有用的独立知识页，而不是某个 owner 的子功能、内部机制、实现策略、步骤、参数或局部细节。
5. 归并价值：未来其他文档的知识可以安全归并到该对象，而不会与同名不同义或另一作者的分类框架混合。

对 owner 相关候选做反事实测试：移除 owner 后，证据若不再指向同一稳定对象，则不建 Entity，只可能作 Topic 或原文证据。产品、标准、组织、人物、公共概念或案例均不按类型自动保留或删除，只按上述精度门判断。事件、时间点、状态、数值、单条事实、关系名、属性名和章节标题不是 Entity。
若候选就是当前被定义和维护的规范、标准或制度资产本身，“脱离原材料”指脱离当前段落语境后，其原文完整正式标题仍能在本知识库内唯一定位该受维护资产；不得因它恰好由当前文件承载而排除。若文档同时提供一个公共概念不依赖 owner 的定义、边界或比较，并充分研究 owner 的特化实践，允许该概念作 Entity，并同时作 owner 的 Topic；两者必须各有直接证据。行业通用术语若在本文中只用于组织 owner 的分析维度或章节，即使名称本身稳定，也只作 owner Topic。

Topic 精度门：
- 必须通过 ownerEntityRef 唯一绑定本次已保留的 Entity；无法确定 owner 就不输出。
- 必须依赖 owner 而成立，且是未来会被独立持续检索的稳定方向。不按章节凑 Topic；重叠的局部机制、步骤和分析视角应合并到上位方向。
- 只用一句能力概括、营销表述或单项对比来说明一个 Entity 为何被提及，不足以再创建 Topic。
- 只适用于下游产品、单个业务、特定活动、一次任务或局部受众的适配表、例子或建议，不是 owner 的稳定 Topic，只保留为原文证据。
- 对规范、标准或制度 Entity，规则域仍须先通过 owner 整体适用范围判断；正文分别定义且适用于 owner 整体的稳定规则域可以作为 Topic。只面向特定品牌、产品、业务或局部受众的“内容适配/建议”即使有独立章节也只能作证据，不能作该规范的 Topic。precision 优先不等于删除已有充分直接证据的合法规则域。

命名规则：
- 在角色确定后再命名。Entity name 依次选原文连续出现的官方名/全称、稳定专名、最精确无歧义名；保留区分身份必需的限定词。不得用 evidenceSummary 弥补一个本身宽泛或有歧义的 name。
- 不得临时翻译、拼接分散片段或自创“owner-局部名”。Topic 选原文连续出现的最小充分名，保留消除歧义所必需的“规范、系统、架构、方案、机制、工程”等限定。
- aliases 只收录原文明确声明的同指全称、简称、缩写或旧称，每项只含一个名称。“本文、本规范、本系统、该方案、上述机制”等篇章内指代，以及上下位词、子功能、关键词和描述短语，不是 alias。

证据与输出：
- Entity 和 Topic 必须有 documentEvidence 正文直接支持。documentOverview 只是结构索引；truncated=true 或 headingIndexComplete=false 时不得猜测未展示内容。
- entityDescription 是脱离当前文档仍成立的一句话身份描述：必须以 Entity 本身为主语，说清“它是什么”及区分身份必需的信息，且全部受本文正文支持。不得出现“本文、文档、核心研究对象、代表、某某派”等来源视角或临时分类措辞，不得只写效果、评价或局部能力，最长 160 个 Unicode code point。
- evidenceSummary 用一句话说明为何必要输出，最长 160 个 Unicode code point，不复制原文、不输出 sourceRef。已有实体词表只在抽取完后用于身份归一，不得改变候选集。
- 只输出严格 JSON 对象，顶层只允许 entities 和 topics。entities 每项只允许 entityRef、name、aliases、entityDescription、evidenceSummary；topics 每项只允许 ownerEntityRef、name、evidenceSummary。按研究重要性降序排列，不输出解释或 Markdown。
""".strip()

DISCOVERY_PROMPT_HASH = hashlib.sha256(DISCOVERY_SYSTEM_PROMPT.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceReference:
    source_ref: str
    content: str
    start: int
    end: int
    kind: str = "content"
    heading_level: int | None = None
    section_path: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveryDocumentContext:
    excerpt: str
    source_references: tuple[SourceReference, ...]
    truncated: bool

    @property
    def source_text(self) -> str:
        return "\n".join(item.content for item in self.source_references)


@dataclass(frozen=True, slots=True)
class DiscoveredEntity:
    entity_ref: str
    name: str
    aliases: tuple[str, ...]
    evidence_summary: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class DiscoveredTopic:
    owner_entity_ref: str
    name: str
    evidence_summary: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    entities: tuple[DiscoveredEntity, ...]
    topics: tuple[DiscoveredTopic, ...]
    warnings: tuple[str, ...] = ()
    attempts: int = 1
    context: DiscoveryDocumentContext | None = None
    raw_json: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _ContextSlice:
    content: str
    start: int
    end: int
    kind: str
    heading_level: int | None
    section_path: tuple[str, ...]
    owner_heading_start: int | None


class KnowledgeEntityDiscovery:
    def __init__(
        self,
        llm: KnowledgeEntityLLM,
        *,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.0,
        cache_size: int = 128,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_attempts < 1 or cache_size < 0:
            raise ValueError("invalid discovery retry/cache configuration")
        self._llm = llm
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._cache_size = cache_size
        self._sleep = sleep
        self._result_cache: OrderedDict[str, DiscoveryResult] = OrderedDict()

    async def discover(
        self,
        markdown: str,
        *,
        max_entities: int = MAX_DISCOVERED_ENTITIES,
        max_topics: int = MAX_DISCOVERED_TOPICS,
        log_context: Mapping[str, Any] | None = None,
    ) -> DiscoveryResult:
        started_at = time.perf_counter()
        context = build_discovery_context(markdown)
        entity_limit = min(max(int(max_entities), 0), MAX_DISCOVERED_ENTITIES)
        topic_limit = min(max(int(max_topics), 0), MAX_DISCOVERED_TOPICS)
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "promptHash": DISCOVERY_PROMPT_HASH,
                    "protocol": DISCOVERY_PROTOCOL_VERSION,
                    "context": context.excerpt,
                    "maxEntities": entity_limit,
                    "maxTopics": topic_limit,
                    "modelIdentity": await _llm_cache_identity(self._llm),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        cached = self._result_cache.get(cache_key)
        if cached is not None:
            self._result_cache.move_to_end(cache_key)
            return cached
        base_messages = [
            {"role": "system", "content": DISCOVERY_SYSTEM_PROMPT},
            {"role": "user", "content": context.excerpt},
        ]
        raw_result: Mapping[str, Any] | None = None
        validation_error = ""
        attempts = 0
        for attempts in range(1, self._max_attempts + 1):
            if attempts > 1 and self._retry_backoff_seconds:
                await self._sleep(self._retry_backoff_seconds * (attempts - 1))
            messages = list(base_messages)
            if validation_error:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"上次输出无效（{validation_error}）。请按规定字段重新只输出严格 JSON 对象，不要 Markdown 或解释。"
                        ),
                    }
                )
            try:
                parsed = _parse_json_output(
                    await self._llm.complete(messages, json_mode=True)
                )
                validation_error = _strict_shape_error(parsed)
                if not validation_error:
                    raw_result = parsed
                    break
            except (json.JSONDecodeError, TypeError) as exc:
                validation_error = f"JSON parse error: {exc}"
            logger.warning(
                "knowledge_entity discovery output invalid: attempt=%s error=%s%s",
                attempts,
                validation_error,
                _safe_log_context(log_context),
            )
        if raw_result is None:
            raise KnowledgeEntityOutputError(
                f"LLM output remained invalid after {attempts} attempts: {validation_error}"
            )
        entities, topics, warnings = normalize_discovery_result(
            raw_result,
            context=context,
            max_entities=entity_limit,
            max_topics=topic_limit,
        )
        result = DiscoveryResult(
            entities, topics, warnings, attempts, context, dict(raw_result)
        )
        if self._cache_size:
            self._result_cache[cache_key] = result
            while len(self._result_cache) > self._cache_size:
                self._result_cache.popitem(last=False)
        logger.info(
            "knowledge_entity discovery completed: entities=%s topics=%s attempts=%s elapsed_ms=%.2f%s",
            len(entities),
            len(topics),
            attempts,
            (time.perf_counter() - started_at) * 1000,
            _safe_log_context(log_context),
        )
        return result


def build_discovery_context(
    markdown: str, *, max_chars: int = DISCOVERY_CONTEXT_CHARS
) -> DiscoveryDocumentContext:
    if max_chars < 256:
        raise ValueError("max_chars must be at least 256")
    original = markdown or ""
    leading = len(original) - len(original.lstrip())
    trailing = len(original) - len(original.rstrip())
    source_end = len(original) - trailing if trailing else len(original)
    if leading >= source_end:
        return DiscoveryDocumentContext(
            "[documentOverview]\noriginalChars=0 selectedChars=0 truncated=false\n"
            "[headingIndex]\n[/headingIndex]\n[/documentOverview]\n\n"
            "[documentEvidence]\n[/documentEvidence]",
            (),
            False,
        )

    source_chars = source_end - leading
    chunk_chars = min(1200, max(128, max_chars // 12))
    slices = _parse_context_slices(
        original,
        start=leading,
        end=source_end,
        chunk_chars=chunk_chars,
        coalesce_content=source_chars > 4096,
    )
    selection_budget = max_chars
    while True:
        selected = _select_context_slices(slices, max_chars=selection_budget)
        selected_keys = {(item.start, item.end) for item in selected}
        truncated = any((item.start, item.end) not in selected_keys for item in slices)
        context = _render_discovery_context(
            selected,
            original_chars=len(original),
            considered_chars=source_chars,
            truncated=truncated,
            heading_index_complete=all(
                item.kind != "heading" or (item.start, item.end) in selected_keys
                for item in slices
            ),
        )
        overflow = len(context.excerpt) - max_chars
        if overflow <= 0 or selection_budget == 0:
            return context
        selection_budget = max(
            0,
            selection_budget - max(overflow + 64, selection_budget // 10),
        )


def _render_discovery_context(
    selected: tuple[_ContextSlice, ...],
    *,
    original_chars: int,
    considered_chars: int,
    truncated: bool,
    heading_index_complete: bool,
) -> DiscoveryDocumentContext:
    references = tuple(
        SourceReference(
            f"s{index}",
            item.content,
            item.start,
            item.end,
            item.kind,
            item.heading_level,
            item.section_path,
        )
        for index, item in enumerate(selected, start=1)
    )
    selected_chars = sum(len(item.content) for item in references)
    heading_lines = []
    evidence_blocks = []
    for item in references:
        path = " > ".join(item.section_path) or "(document root)"
        if item.kind == "heading":
            heading_lines.append(
                f"[sourceRef={item.source_ref} level={item.heading_level} "
                f"position={item.start}/{original_chars}]\n{item.content}"
            )
        else:
            evidence_blocks.append(
                f"[sourceRef={item.source_ref} position={item.start}/{original_chars} "
                f"section={json.dumps(path, ensure_ascii=False)}]\n{item.content}"
            )
    excerpt = (
        "[documentOverview]\n"
        f"originalChars={original_chars} consideredChars={considered_chars} "
        f"selectedChars={selected_chars} truncated={str(truncated).lower()} "
        f"headingIndexComplete={str(heading_index_complete).lower()}\n"
        "[headingIndex]\n"
        + "\n\n".join(heading_lines)
        + "\n[/headingIndex]\n[/documentOverview]\n\n[documentEvidence]\n"
        + "\n\n".join(evidence_blocks)
        + "\n[/documentEvidence]"
    )
    return DiscoveryDocumentContext(excerpt, references, truncated)


def _parse_context_slices(
    source: str,
    *,
    start: int,
    end: int,
    chunk_chars: int,
    coalesce_content: bool,
) -> tuple[_ContextSlice, ...]:
    slices: list[_ContextSlice] = []
    heading_stack: list[tuple[int, str]] = []
    owner_heading_start: int | None = None
    paragraph_start: int | None = None
    cursor = start
    fence_marker: tuple[str, int] | None = None

    def section_path() -> tuple[str, ...]:
        return tuple(title for _, title in heading_stack)

    def flush_paragraph(paragraph_end: int) -> None:
        nonlocal paragraph_start
        if paragraph_start is None:
            return
        _append_content_slices(
            slices,
            source,
            paragraph_start,
            paragraph_end,
            chunk_chars=chunk_chars,
            section_path=section_path(),
            owner_heading_start=owner_heading_start,
        )
        paragraph_start = None

    for line in source[start:end].splitlines(keepends=True):
        line_end = cursor + len(line)
        content_end = line_end
        while content_end > cursor and source[content_end - 1] in "\r\n":
            content_end -= 1
        stripped = source[cursor:content_end].strip()
        current_fence = _markdown_fence_marker(stripped)
        heading_level = (
            None if fence_marker is not None else _markdown_heading_level(stripped)
        )
        if heading_level is not None:
            flush_paragraph(cursor)
            content_start = cursor
            while content_start < content_end and source[content_start].isspace():
                content_start += 1
            title = stripped[heading_level:].strip()
            heading_stack[:] = [
                item for item in heading_stack if item[0] < heading_level
            ]
            heading_stack.append((heading_level, title))
            owner_heading_start = content_start
            slices.append(
                _ContextSlice(
                    source[content_start:content_end],
                    content_start,
                    content_end,
                    "heading",
                    heading_level,
                    section_path(),
                    owner_heading_start,
                )
            )
        elif not stripped and not coalesce_content:
            flush_paragraph(cursor)
        elif paragraph_start is None:
            paragraph_start = cursor
        if current_fence is not None:
            if fence_marker is None:
                fence_marker = current_fence
            elif (
                current_fence[0] == fence_marker[0]
                and current_fence[1] >= fence_marker[1]
            ):
                fence_marker = None
        cursor = line_end
    flush_paragraph(end)
    return tuple(slices)


def _markdown_heading_level(stripped_line: str) -> int | None:
    level = 0
    while level < len(stripped_line) and stripped_line[level] == "#" and level < 6:
        level += 1
    if not level or level >= len(stripped_line):
        return None
    return level if stripped_line[level].isspace() else None


def _markdown_fence_marker(stripped_line: str) -> tuple[str, int] | None:
    if not stripped_line or stripped_line[0] not in {"`", "~"}:
        return None
    marker = stripped_line[0]
    length = 0
    while length < len(stripped_line) and stripped_line[length] == marker:
        length += 1
    return (marker, length) if length >= 3 else None


def _append_content_slices(
    output: list[_ContextSlice],
    source: str,
    start: int,
    end: int,
    *,
    chunk_chars: int,
    section_path: tuple[str, ...],
    owner_heading_start: int | None,
) -> None:
    while start < end and source[start].isspace():
        start += 1
    while end > start and source[end - 1].isspace():
        end -= 1
    while end - start > chunk_chars:
        target = start + chunk_chars
        lower_bound = start + chunk_chars * 3 // 5
        break_at = max(
            source.rfind("\n", lower_bound, target),
            source.rfind(" ", lower_bound, target),
        )
        if break_at < lower_bound:
            break_at = target
        chunk_end = break_at
        while chunk_end > start and source[chunk_end - 1].isspace():
            chunk_end -= 1
        if start < chunk_end:
            output.append(
                _ContextSlice(
                    source[start:chunk_end],
                    start,
                    chunk_end,
                    "content",
                    None,
                    section_path,
                    owner_heading_start,
                )
            )
        start = break_at
        while start < end and source[start].isspace():
            start += 1
    if start < end:
        output.append(
            _ContextSlice(
                source[start:end],
                start,
                end,
                "content",
                None,
                section_path,
                owner_heading_start,
            )
        )


def _select_context_slices(
    slices: tuple[_ContextSlice, ...], *, max_chars: int
) -> tuple[_ContextSlice, ...]:
    if sum(len(item.content) for item in slices) <= max_chars:
        return slices
    headings = [item for item in slices if item.kind == "heading"]
    contents = [item for item in slices if item.kind == "content"]
    selected: dict[tuple[int, int], _ContextSlice] = {}
    used = 0

    def add(item: _ContextSlice, *, budget: int = max_chars) -> bool:
        nonlocal used
        key = (item.start, item.end)
        size = len(item.content)
        if key in selected or used + size > min(budget, max_chars):
            return False
        selected[key] = item
        used += size
        return True

    heading_budget = max_chars // 3
    for item in _position_balanced_order(headings):
        add(item, budget=heading_budget)

    if contents:
        add(contents[0])
        add(contents[-1])
    first_by_heading: dict[int | None, _ContextSlice] = {}
    for item in contents:
        first_by_heading.setdefault(item.owner_heading_start, item)
    heading_levels = {item.start: item.heading_level for item in headings}
    primary_representatives = [
        item
        for owner, item in first_by_heading.items()
        if owner is None or (heading_levels.get(owner) or 7) <= 3
    ]
    for item in _position_balanced_order(primary_representatives):
        add(item)
    for item in _position_balanced_order(contents):
        add(item)
    return tuple(sorted(selected.values(), key=lambda item: (item.start, item.end)))


def _position_balanced_order(items: list[_ContextSlice]) -> tuple[_ContextSlice, ...]:
    ordered = sorted(items, key=lambda item: item.start)
    if not ordered:
        return ()
    if len(ordered) == 1:
        return (ordered[0],)
    positions = [item.start for item in ordered]
    result = [ordered[0], ordered[-1]]
    gaps: list[tuple[int, int, int]] = []

    def push_gap(left: int, right: int) -> None:
        if right - left > 1:
            heapq.heappush(gaps, (-(positions[right] - positions[left]), left, right))

    push_gap(0, len(ordered) - 1)
    while gaps:
        _, left, right = heapq.heappop(gaps)
        target = (positions[left] + positions[right]) // 2
        insertion = bisect.bisect_left(positions, target, left + 1, right)
        candidates = {min(max(insertion, left + 1), right - 1)}
        if insertion - 1 > left:
            candidates.add(insertion - 1)
        middle = min(
            candidates,
            key=lambda index: (abs(positions[index] - target), positions[index]),
        )
        result.append(ordered[middle])
        push_gap(left, middle)
        push_gap(middle, right)
    return tuple(result)


def normalize_discovery_result(
    raw: Mapping[str, Any],
    *,
    context: DiscoveryDocumentContext,
    max_entities: int = MAX_DISCOVERED_ENTITIES,
    max_topics: int = MAX_DISCOVERED_TOPICS,
) -> tuple[tuple[DiscoveredEntity, ...], tuple[DiscoveredTopic, ...], tuple[str, ...]]:
    warnings: list[str] = []
    source_text = normalize_surface(context.source_text)
    entities: list[DiscoveredEntity] = []
    redirects: dict[str, str] = {}
    seen_names: dict[str, int] = {}
    valid_refs: set[str] = set()
    for index, item in enumerate(raw["entities"]):
        entity_ref, name = _clean(item["entityRef"]), _clean(item["name"])
        if not _surface_in_context(name, source_text):
            warnings.append(f"entity[{index}] discarded: name_not_in_context")
            continue
        aliases: list[str] = []
        alias_seen = {normalize_surface(name)}
        for raw_alias in item["aliases"]:
            alias, key = _clean(raw_alias), normalize_surface(raw_alias)
            if (
                alias
                and key not in alias_seen
                and _surface_in_context(alias, source_text)
            ):
                alias_seen.add(key)
                aliases.append(alias)
            elif alias and not _surface_in_context(alias, source_text):
                warnings.append(f"entity[{index}] alias discarded: not_in_context")
        entity = DiscoveredEntity(
            entity_ref=entity_ref,
            name=name,
            aliases=tuple(aliases),
            evidence_summary=_clean(item["evidenceSummary"]),
            description=_clean(item["entityDescription"]),
        )
        key = normalize_surface(name)
        if key in seen_names:
            retained_index = seen_names[key]
            retained = entities[retained_index]
            entities[retained_index] = DiscoveredEntity(
                retained.entity_ref,
                retained.name,
                tuple(dict.fromkeys((*retained.aliases, *entity.aliases))),
                retained.evidence_summary,
                retained.description,
            )
            redirects[entity_ref] = retained.entity_ref
            warnings.append(f"entity[{index}] deduplicated: exact_name")
            continue
        seen_names[key] = len(entities)
        entities.append(entity)
        valid_refs.add(entity_ref)
    limited_entities = entities[:max_entities]
    limited_refs = {item.entity_ref for item in limited_entities}
    if len(entities) > len(limited_entities):
        warnings.append(
            f"entity list truncated from {len(entities)} to {len(limited_entities)}"
        )
    topics: list[DiscoveredTopic] = []
    seen_topics: set[tuple[str, str]] = set()
    for index, item in enumerate(raw["topics"]):
        owner = redirects.get(
            _clean(item["ownerEntityRef"]), _clean(item["ownerEntityRef"])
        )
        if owner not in valid_refs or owner not in limited_refs:
            warnings.append(f"topic[{index}] discarded: unresolved_owner")
            continue
        name = _clean(item["name"])
        if not _surface_in_context(name, source_text):
            warnings.append(f"topic[{index}] discarded: name_not_in_context")
            continue
        key = (owner, normalize_surface(name))
        if key in seen_topics:
            warnings.append(f"topic[{index}] deduplicated: exact_owner_name")
            continue
        seen_topics.add(key)
        topics.append(
            DiscoveredTopic(
                owner,
                name,
                _clean(item["evidenceSummary"]),
            )
        )
    limited_topics = topics[:max_topics]
    if len(topics) > len(limited_topics):
        warnings.append(
            f"topic list truncated from {len(topics)} to {len(limited_topics)}"
        )
    return tuple(limited_entities), tuple(limited_topics), tuple(warnings)


_CJK_RE = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_TYPOGRAPHIC_SPACE_RE = re.compile(rf"(?<=[{_CJK_RE}])\s+|\s+(?=[{_CJK_RE}])")
_ASCII_WORD_RE = re.compile(r"[a-z0-9_]")


def _surface_in_context(surface: str, normalized_context: str) -> bool:
    """Match a source surface while tolerating only CJK-adjacent layout spaces.

    The discovery protocol still requires a literal source surface.  This helper
    accepts typography such as ``Emoji 使用规范`` versus ``Emoji使用规范`` but
    does not collapse whitespace between two Latin words.  Latin names also use
    word boundaries so ``OWL`` cannot be justified by ``BOWL``.
    """

    candidate = _TYPOGRAPHIC_SPACE_RE.sub("", normalize_surface(surface))
    context = _TYPOGRAPHIC_SPACE_RE.sub("", normalized_context)
    if not candidate:
        return False
    start = context.find(candidate)
    while start >= 0:
        end = start + len(candidate)
        left_invalid = (
            bool(_ASCII_WORD_RE.fullmatch(candidate[0]))
            and start > 0
            and bool(_ASCII_WORD_RE.fullmatch(context[start - 1]))
        )
        right_invalid = (
            bool(_ASCII_WORD_RE.fullmatch(candidate[-1]))
            and end < len(context)
            and bool(_ASCII_WORD_RE.fullmatch(context[end]))
        )
        if not left_invalid and not right_invalid:
            return True
        start = context.find(candidate, start + 1)
    return False


def _strict_shape_error(value: Any) -> str:
    if not isinstance(value, Mapping) or set(value) != {"entities", "topics"}:
        return "top level must contain exactly entities and topics"
    if not isinstance(value["entities"], list) or not isinstance(value["topics"], list):
        return "entities and topics must be arrays"
    refs: set[str] = set()
    entity_fields = {
        "entityRef",
        "name",
        "aliases",
        "entityDescription",
        "evidenceSummary",
    }
    topic_fields = {"ownerEntityRef", "name", "evidenceSummary"}
    for index, item in enumerate(value["entities"]):
        error = _strict_item_error(item, entity_fields, "entityRef")
        if error:
            return f"entity[{index}] {error}"
        ref = item["entityRef"].strip()
        if ref in refs:
            return f"entity[{index}] duplicate entityRef"
        refs.add(ref)
        if not isinstance(item["aliases"], list) or any(
            not isinstance(alias, str) for alias in item["aliases"]
        ):
            return f"entity[{index}] aliases must be a string array"
        description = item["entityDescription"]
        if not isinstance(description, str) or not description.strip():
            return f"entity[{index}] entityDescription must be a non-empty string"
        if len(description.strip()) > MAX_ENTITY_DESCRIPTION_CODEPOINTS:
            return f"entity[{index}] entityDescription exceeds 160 code points"
    for index, item in enumerate(value["topics"]):
        error = _strict_item_error(item, topic_fields, "ownerEntityRef")
        if error:
            return f"topic[{index}] {error}"
        if item["ownerEntityRef"].strip() not in refs:
            return f"topic[{index}] ownerEntityRef does not reference an entity"
    return ""


def _strict_item_error(
    item: Any,
    fields: set[str],
    ref_field: str,
) -> str:
    if not isinstance(item, Mapping) or set(item) != fields:
        return f"fields must be exactly {sorted(fields)}"
    for name in (ref_field, "name", "evidenceSummary"):
        if not isinstance(item[name], str) or not item[name].strip():
            return f"{name} must be a non-empty string"
    if len(item["evidenceSummary"].strip()) > MAX_EVIDENCE_SUMMARY_CODEPOINTS:
        return "evidenceSummary exceeds 160 code points"
    return ""


def _clean(value: str) -> str:
    return " ".join(value.strip().split())


async def _llm_cache_identity(llm: KnowledgeEntityLLM) -> str:
    provider = getattr(llm, "cache_identity", None)
    if provider is None:
        return f"{type(llm).__module__}.{type(llm).__qualname__}"
    value = provider()
    if inspect.isawaitable(value):
        value = await value
    return str(value)


__all__ = [
    "DISCOVERY_PROMPT_HASH",
    "DISCOVERY_PROTOCOL_VERSION",
    "DISCOVERY_SYSTEM_PROMPT",
    "DiscoveredEntity",
    "DiscoveredTopic",
    "DiscoveryDocumentContext",
    "DiscoveryResult",
    "KnowledgeEntityDiscovery",
    "MAX_DISCOVERED_TOPICS",
    "SourceReference",
    "build_discovery_context",
    "normalize_discovery_result",
]
