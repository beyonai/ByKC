#!/usr/bin/env python3
"""Run real KnowledgeEntity enrichment without persisting generated documents.

The harness reads the configured database, object storage, search index, embedding
service, and enrichment LLM.  It deliberately stops before DocumentUpdateService,
so repeated evaluations do not mutate database rows or MinIO objects.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import quote, unquote

from by_qa.config import Settings, load_project_env_file
from by_qa.core.model_config import load_model_config_provider
from by_qa.knowledge_base.infrastructure.runtime import (
    build_knowledge_entity_processing_service,
)
from by_qa.knowledge_base.services.knowledge_entity_discovery import (
    DISCOVERY_SYSTEM_PROMPT,
    DiscoveryResult,
)
from by_qa.knowledge_base.services.knowledge_entity_enrichment import (
    DEFAULT_SOFT_TEMPLATE,
    EvidenceBundle,
    EvidenceFragment,
    RelationTarget,
    _build_enrichment_messages,
    build_evidence_claim_groups,
    format_source_reference,
    organize_evidence,
)
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    _complete_strict_json,
    build_discovery_llm,
    normalize_surface,
)
from by_qa.knowledge_base.services.knowledge_entity_task_worker import (
    KnowledgeEntityTaskContext,
)

_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\n]+)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_URL_RE = re.compile(r"https?://[^\s<>()\]]+")

QUALITY_JUDGE_PROMPT = """\
你是知识实体文档质量评审器。你只按通用知识编辑标准评审，不因实体名称、样本来源或预期结论调整标准。

输入包括实体身份、稳定 Topic、旧稿、授权证据和候选文档。Topic 是覆盖方向而不是事实；事实只能由证据或仍有引用的旧稿支持。

分别按 1—5 分评估：
- groundedness：事实、数字、机制、评价与不确定性是否有证据，是否存在臆造；
- coverage：是否覆盖证据充分的主要方向，证据丰富时是否退化成定义卡片；
- synthesis：是否真正综合材料，结构自然，避免证据清单和重复段落；
- citationQuality：引用策略是否符合来源类型。originalDocument 是原始材料，正文可以自然吸收，
  默认只需在“参考资料”中完整列出一次，不得因为缺少逐段行内引用而扣分；knowledgeEntity 只有在
  表达真实启发、比较、依赖或继承关系时才应在对应事实句中自然链接，不能作为段末脚注；同时检查
  是否重复引用、来源错配或遗漏实际使用的来源；
- maintainability：是否适合作为持续更新的实体页，边界、冲突和不确定性是否清楚。

若 mode=incremental，还要评估：
- retention：旧稿中未被新证据否定的事实和引用是否保留；
- integration：新内容是否融入原结构，而不是覆盖旧稿或简单追加一段。

pass 必须同时满足：groundedness、coverage、synthesis、citationQuality、maintainability 均不低于 4；增量模式下 retention、integration 也不低于 4；没有 critical 缺陷。不得因文档较长、标题较多或引用较多自动给高分。

只输出严格 JSON：
{"pass":true,"scores":{"groundedness":1,"coverage":1,"synthesis":1,"citationQuality":1,"maintainability":1,"retention":null,"integration":null},"defects":[{"severity":"critical|major|minor","code":"...","reason":"..."}],"summary":"..."}
""".strip()


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-code", default="1")
    parser.add_argument("--entity", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="maximum Entity evaluations running concurrently (default: 4)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("eval/reports/entity-enrich-quality"),
    )
    parser.add_argument("--skip-judge", action="store_true")
    parser.add_argument("--skip-incremental-replay", action="store_true")
    parser.add_argument(
        "--two-stage-entity",
        help="run one real two-source discover/enrich replay for this Entity",
    )
    parser.add_argument(
        "--stage-source",
        action="append",
        default=[],
        help="source path for the two-stage replay; specify exactly twice",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="audit local entity files and Topics without search or LLM calls",
    )
    return parser.parse_args()


def _seconds(started_at: float) -> float:
    return round(perf_counter() - started_at, 3)


async def run_bounded_concurrently(
    rows: list[dict[str, Any]],
    *,
    concurrency: int,
    evaluate: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> list[dict[str, Any] | Exception]:
    """Evaluate rows in stable input order with bounded concurrency."""

    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(row: dict[str, Any]) -> dict[str, Any] | Exception:
        async with semaphore:
            try:
                return await evaluate(row)
            except Exception as exc:  # return all failures after peers finish
                return exc

    return list(await asyncio.gather(*(run_one(row) for row in rows)))


def _links(markdown: str) -> list[dict[str, str]]:
    return [
        {"label": match.group(1), "target": match.group(2).strip()}
        for match in _LINK_RE.finditer(markdown or "")
    ]


def _canonical_target(target: str) -> str:
    return quote(unquote(target.strip()), safe="/:@-._~!$&'*+,;=%")


def analyze_document(
    *,
    entity_name: str,
    markdown: str,
    existing_markdown: str,
    evidence: list[EvidenceFragment],
) -> dict[str, Any]:
    """Return generic deterministic checks; no entity-specific expectations."""

    without_front_matter = _FRONT_MATTER_RE.sub("", markdown or "", count=1).strip()
    headings = [
        {"level": len(match.group(1)), "text": match.group(2).strip()}
        for match in _HEADING_RE.finditer(without_front_matter)
    ]
    paragraphs = [
        block.strip()
        for block in re.split(r"\n\s*\n", without_front_matter)
        if block.strip() and not block.lstrip().startswith("#")
    ]
    current_links = _links(markdown)
    old_targets = {
        _canonical_target(item["target"]) for item in _links(existing_markdown)
    }
    current_targets = {_canonical_target(item["target"]) for item in current_links}
    evidence_targets = {
        _canonical_target(
            _links(format_source_reference(item.document_path))[0]["target"]
        )
        for item in evidence
    }
    for item in evidence:
        evidence_targets.update(
            _canonical_target(link["target"]) for link in _links(item.content)
        )
        evidence_targets.update(
            _canonical_target(match.group(0).rstrip(".,;:"))
            for match in _URL_RE.finditer(item.content)
        )
    allowed_targets = old_targets | evidence_targets
    evidence_chars = sum(len(item.content) for item in evidence)
    body_chars = sum(len(paragraph) for paragraph in paragraphs)
    substantive_evidence = (
        evidence_chars >= 1_000 or len({e.document_file_id for e in evidence}) >= 2
    )
    reference_heading = re.search(r"(?m)^##\s+参考资料\s*$", without_front_matter)
    inline_body = (
        without_front_matter[: reference_heading.start()]
        if reference_heading
        else without_front_matter
    )
    body_paragraphs = [
        block.strip()
        for block in re.split(r"\n\s*\n", inline_body)
        if block.strip() and not block.lstrip().startswith("#")
    ]
    body_reference_count = len(_links(inline_body))
    trailing_cited_paragraph_count = sum(
        bool(re.search(r"\]\([^)\n]+\)[。.!！?？]?$", paragraph))
        for paragraph in body_paragraphs
    )
    trailing_citation_ratio = trailing_cited_paragraph_count / max(
        1, len(body_paragraphs)
    )
    checks = {
        "exactH1": bool(headings) and headings[0] == {"level": 1, "text": entity_name},
        "singleH1": sum(item["level"] == 1 for item in headings) == 1,
        "oldReferencesPreserved": old_targets <= current_targets,
        "onlyAuthorizedReferences": current_targets <= allowed_targets,
        "notDefinitionStub": not (
            substantive_evidence and body_chars < 400 and len(headings) < 3
        ),
        "citationsNotMechanical": (
            len(body_paragraphs) < 5 or trailing_citation_ratio <= 0.6
        ),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "headingCount": len(headings),
        "headings": headings,
        "paragraphCount": len(paragraphs),
        "bodyChars": body_chars,
        "evidenceChars": evidence_chars,
        "sourceCount": len({item.document_file_id for item in evidence}),
        "referenceCount": len(current_links),
        "uniqueReferenceCount": len(current_targets),
        "duplicateReferenceCount": len(current_links) - len(current_targets),
        "bodyReferenceCount": body_reference_count,
        "trailingCitedParagraphCount": trailing_cited_paragraph_count,
        "trailingCitationRatio": round(trailing_citation_ratio, 3),
        "missingOldReferences": sorted(old_targets - current_targets),
        "unauthorizedReferences": sorted(current_targets - allowed_targets),
    }


def analyze_context(
    *,
    topics: tuple[str, ...],
    evidence: list[EvidenceFragment],
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    counts = Counter(item.document_file_id for item in evidence)
    prompt_text = "\n".join(message["content"] for message in messages)
    claim_groups = build_evidence_claim_groups(
        EvidenceBundle(
            fragments=tuple(evidence),
            total_chars=sum(len(item.content) for item in evidence),
            discarded_count=0,
            warnings=(),
        )
    )
    topic_source_groups = sorted(
        {
            (
                topic,
                item.document_file_id,
                kind,
            )
            for item in evidence
            for topic in (item.matched_topics or ("[entity]",))
            for kind in (
                *(
                    ("mention",)
                    if item.direct_mention or item.explicit_reference
                    else ()
                ),
                *(("semantic",) if item.semantic_score > 0 else ()),
            )
        }
    )
    return {
        "topicCount": len(topics),
        "claimGroupCount": len(claim_groups),
        "requiredClaimGroupCount": sum(group.required for group in claim_groups),
        "supportedAspectCounts": dict(
            sorted(
                Counter(
                    aspect
                    for group in claim_groups
                    for aspect in group.supported_aspects
                ).items()
            )
        ),
        "semanticSearchQueryCount": max(1, len(topics)),
        "topicSourceQuotaGroupCount": len(topic_source_groups),
        "topicSourceQuotaGroups": [
            {"topic": topic, "sourceFileId": file_id, "kind": kind}
            for topic, file_id, kind in topic_source_groups
        ],
        "mentionFragmentCount": sum(
            item.direct_mention or item.explicit_reference for item in evidence
        ),
        "semanticFragmentCount": sum(item.semantic_score > 0 for item in evidence),
        "mergedMentionSemanticCount": sum(
            (item.direct_mention or item.explicit_reference) and item.semantic_score > 0
            for item in evidence
        ),
        "evidenceFragmentCount": len(evidence),
        "evidenceChars": sum(len(item.content) for item in evidence),
        "sourceCount": len(counts),
        "sourceKindCounts": dict(
            sorted(Counter(item.document_kind for item in evidence).items())
        ),
        "fragmentsPerSource": dict(sorted(counts.items())),
        "directMentionCount": sum(item.direct_mention for item in evidence),
        "explicitReferenceCount": sum(item.explicit_reference for item in evidence),
        "promptChars": sum(len(message["content"]) for message in messages),
        "claimGroupMetadataInPrompt": any(
            marker in prompt_text
            for marker in ("EvidenceClaimGroup", "claimGroupId", "claimCoverage")
        ),
        "withinEvidenceBudget": len(evidence) <= 50
        and sum(len(item.content) for item in evidence) <= 50_000,
        "allAuthorized": all(item.authorized for item in evidence),
    }


def enrichment_protocol_report(result: Any) -> dict[str, Any]:
    """Serialize the P0/P1 planning and deterministic audit for one output."""

    return {
        "attempts": result.attempts,
        "repairPerformed": result.repair_performed,
        "claimGroups": [asdict(item) for item in result.claim_groups],
        "editHints": [asdict(item) for item in result.edit_hints],
        "existingClaimAnchors": [
            asdict(item) for item in result.existing_claim_anchors
        ],
        "qualityAudit": asdict(result.quality_audit),
    }


def split_evidence_for_incremental_replay(
    evidence: list[EvidenceFragment],
) -> tuple[list[EvidenceFragment], list[EvidenceFragment]]:
    """Create a deterministic source-order replay without changing source data."""

    source_ids = sorted({item.document_file_id for item in evidence})
    if len(source_ids) >= 2:
        midpoint = max(1, len(source_ids) // 2)
        baseline_ids = set(source_ids[:midpoint])
        return (
            [item for item in evidence if item.document_file_id in baseline_ids],
            [item for item in evidence if item.document_file_id not in baseline_ids],
        )
    if len(evidence) >= 2:
        midpoint = max(1, len(evidence) // 2)
        return evidence[:midpoint], evidence[midpoint:]
    return evidence, []


def match_discovered_entity(
    discovery: DiscoveryResult, *, entity_name: str, aliases: tuple[str, ...] = ()
) -> Any | None:
    """Match a discovered candidate to the authoritative Entity identity."""

    identity_surfaces = {
        normalize_surface(value) for value in (entity_name, *aliases) if value.strip()
    }
    for candidate in discovery.entities:
        candidate_surfaces = {
            normalize_surface(value)
            for value in (candidate.name, *candidate.aliases)
            if value.strip()
        }
        if identity_surfaces & candidate_surfaces:
            return candidate
    return None


def topics_owned_by_candidate(
    discovery: DiscoveryResult, *, entity_ref: str
) -> tuple[str, ...]:
    return tuple(
        topic.name for topic in discovery.topics if topic.owner_entity_ref == entity_ref
    )


def incremental_topic_delta(
    previous: tuple[str, ...], current: tuple[str, ...]
) -> tuple[str, ...]:
    """Return only stage-current Topics not already available at the prior cutoff."""

    previous_keys = {normalize_surface(topic) for topic in previous}
    seen = set(previous_keys)
    delta: list[str] = []
    for topic in current:
        key = normalize_surface(topic)
        if not key or key in seen:
            continue
        seen.add(key)
        delta.append(topic)
    return tuple(delta)


def citation_transition(
    *, baseline_markdown: str, updated_markdown: str, stage_source: str
) -> dict[str, Any]:
    old_targets = {
        _canonical_target(item["target"]) for item in _links(baseline_markdown)
    }
    current_targets = {
        _canonical_target(item["target"]) for item in _links(updated_markdown)
    }
    source_target = _canonical_target(
        _links(format_source_reference(stage_source))[0]["target"]
    )
    return {
        "oldReferenceCount": len(old_targets),
        "currentReferenceCount": len(current_targets),
        "oldReferencesPreserved": old_targets <= current_targets,
        "missingOldReferences": sorted(old_targets - current_targets),
        "stageSourceReferenced": source_target in current_targets,
        "stageSourceTarget": source_target,
    }


def _serialize_discovery(
    discovery: DiscoveryResult, *, selected_entity_ref: str
) -> dict[str, Any]:
    return {
        "selectedEntityRef": selected_entity_ref,
        "entities": [asdict(item) for item in discovery.entities],
        "topics": [asdict(item) for item in discovery.topics],
        "warnings": list(discovery.warnings),
        "attempts": discovery.attempts,
        "context": asdict(discovery.context) if discovery.context else None,
        "rawJson": dict(discovery.raw_json),
    }


async def _load_source_row(worker: Any, *, kb_id: int, path: str) -> dict[str, Any]:
    connection = await worker._connection_factory()  # noqa: SLF001
    try:
        cursor = connection.cursor()
        row = await worker._entity_repository.get_file_with_metadata(  # noqa: SLF001
            cursor, knowledge_base_id=kb_id, file_path=path
        )
        if row is None:
            raise ValueError(f"stage source does not exist: {path}")
        return dict(row)
    finally:
        await connection.close()


async def _read_resolved_markdown(
    worker: Any, *, kb_id: int, row: dict[str, Any]
) -> str:
    markdown = await worker._read_markdown(row)  # noqa: SLF001
    return (
        await worker._search.resolve_markdown_texts(  # noqa: SLF001
            knowledge_base_id=kb_id, texts=[markdown]
        )
    )[0]


def _source_evidence(
    worker: Any,
    *,
    source_row: dict[str, Any],
    markdown: str,
    names: tuple[str, ...],
    topics: tuple[str, ...],
) -> list[EvidenceFragment]:
    evidence: list[EvidenceFragment] = []
    scopes = [((), False, False)]
    scopes.extend(((topic,), True, True) for topic in topics)
    for matched_topics, prefer_topics, require_topic_match in scopes:
        selected = worker._select_entity_sections(  # noqa: SLF001
            markdown,
            names=names,
            topics=matched_topics,
            prefer_topics=prefer_topics,
            require_topic_match=require_topic_match,
        )
        evidence.extend(
            EvidenceFragment(
                document_file_id=int(source_row["kid"]),
                document_path=str(source_row["file_path"]),
                content=fragment,
                direct_mention=True,
                explicit_reference=True,
                relation_code="MENTIONS",
                authorized=True,
                matched_topics=matched_topics,
                document_kind=str(
                    source_row.get("document_kind") or "originalDocument"
                ),
            )
            for fragment in worker._split_relation_evidence(selected)  # noqa: SLF001
            if fragment.strip()
        )
    return evidence


async def judge_document(
    llm: Any,
    *,
    mode: str,
    entity_name: str,
    topics: tuple[str, ...],
    existing_markdown: str,
    evidence: list[EvidenceFragment],
    candidate_markdown: str,
) -> dict[str, Any]:
    evidence_payload = [
        {
            "source": item.document_path,
            "sourceType": item.document_kind,
            "content": item.content,
        }
        for item in evidence
    ]
    payload = {
        "mode": mode,
        "entityName": entity_name,
        "topics": list(topics),
        "existingMarkdown": existing_markdown,
        "evidence": evidence_payload,
        "candidateMarkdown": candidate_markdown,
    }
    result, _ = await _complete_strict_json(
        llm,
        [
            {"role": "system", "content": QUALITY_JUDGE_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        expected_type=dict,
        max_attempts=2,
        retry_backoff_seconds=0.0,
        sleep=asyncio.sleep,
        operation="entity_enrich_quality_judge",
    )
    return dict(result)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


async def _build_runtime() -> tuple[Any, Any]:
    load_project_env_file()
    settings = Settings()
    provider = load_model_config_provider()
    runtime = await build_knowledge_entity_processing_service(
        settings, provider=provider, document_chunking_service=object()
    )
    return runtime, build_discovery_llm(provider=provider, timeout=600.0)


async def _load_rows(
    worker: Any, kb_id: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    connection = await worker._connection_factory()  # noqa: SLF001
    try:
        cursor = connection.cursor()
        rows = await worker._entity_repository.list_files_with_metadata(  # noqa: SLF001
            cursor,
            knowledge_base_id=kb_id,
            path_prefix="/KnowledgeEntity",
        )
        await cursor.execute(
            """
            SELECT kid, entity_name
            FROM knowledge_entity
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND object_kind = 'ENTITY'
              AND name_role = 'canonical'
              AND fs_entry_id IS NULL
            ORDER BY kid
            """,
            {"knowledge_base_id": kb_id},
        )
        unanchored = [dict(row) for row in await cursor.fetchall()]
        return [dict(row) for row in rows], unanchored
    finally:
        await connection.close()


async def _read_persistence_fingerprint(worker: Any, kb_id: int) -> str:
    """Fingerprint mutable enrich state before and after a dry-run evaluation."""

    connection = await worker._connection_factory()  # noqa: SLF001
    try:
        cursor = connection.cursor()
        snapshots: list[Any] = []
        statements = (
            """
            SELECT kid, checksum, updated_at
            FROM knowledge_fs_entry
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND is_deleted = FALSE
            ORDER BY kid
            """,
            """
            SELECT kid, fs_entry_id, property_name, value_type, value_string,
                   value_number, value_boolean, value_datetime, value_string_list,
                   is_deleted, updated_at
            FROM knowledge_file_metadata_value
            WHERE knowledge_base_id = %(knowledge_base_id)s
            ORDER BY kid
            """,
            """
            SELECT COUNT(*) AS count, MAX(kid) AS max_kid, MAX(updated_at) AS max_updated
            FROM knowledge_semantic_processing_task
            WHERE knowledge_base_id = %(knowledge_base_id)s
            """,
            """
            SELECT COUNT(*) AS count, MAX(batch_id) AS max_id, MAX(updated_at) AS max_updated
            FROM knowledge_semantic_processing_batch
            WHERE knowledge_base_id = %(knowledge_base_id)s
            """,
            """
            SELECT COUNT(*) AS count, MAX(kid) AS max_kid, MAX(updated_at) AS max_updated
            FROM knowledge_file_reference
            WHERE knowledge_base_id = %(knowledge_base_id)s
            """,
            """
            SELECT kid, fs_entry_id, canonical_entity_id, name_role, entity_name,
                   normalized_entity_name, subject_entity_id, entity_type,
                   object_kind, description, updated_at
            FROM knowledge_entity
            WHERE knowledge_base_id = %(knowledge_base_id)s
            ORDER BY kid
            """,
            """
            SELECT chunk_id, fs_entry_id, full_path, chunk_no, start_line,
                   end_line, chunk_text, search_text
            FROM knowledge_chunk_retrieval_mv
            WHERE knowledge_base_id = %(knowledge_base_id)s
            ORDER BY chunk_id
            """,
        )
        for statement in statements:
            await cursor.execute(statement, {"knowledge_base_id": kb_id})
            snapshots.append([dict(row) for row in await cursor.fetchall()])
        encoded = json.dumps(snapshots, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    finally:
        await connection.close()


async def _evaluate_entity(
    *,
    worker: Any,
    row: dict[str, Any],
    kb_code: str,
    output_dir: Path,
    top_k: int,
    judge: bool,
    incremental_replay: bool,
    judge_llm: Any,
) -> dict[str, Any]:
    entity_started_at = perf_counter()
    retrieval_started_at = perf_counter()
    context = KnowledgeEntityTaskContext(
        task_id=0,
        task_type="DOCUMENT_ENRICH",
        kb_code=kb_code,
        knowledge_base_id=int(row["knowledge_base_id"]),
        source_file_id=int(row["kid"]),
        file_path=str(row["file_path"]),
        request_params={"topK": top_k},
    )
    loaded_row, surfaces = await worker._load_entity_and_current_surfaces(  # noqa: SLF001
        context
    )
    identity = worker._validate_entity_identity(  # noqa: SLF001
        loaded_row, context, current_surfaces=surfaces
    )
    topics = await worker._load_entity_topics(identity)  # noqa: SLF001
    existing = await worker._read_markdown(loaded_row)  # noqa: SLF001
    existing = (
        await worker._search.resolve_markdown_texts(  # noqa: SLF001
            knowledge_base_id=identity.knowledge_base_id,
            texts=[existing],
        )
    )[0]
    evidence = await worker._collect_evidence(  # noqa: SLF001
        context,
        identity=identity,
        existing_markdown=existing,
        topics=topics,
    )
    relation_targets = tuple(
        RelationTarget(file_id=int(item["kid"]), entity_name=str(item["entity_name"]))
        for item in surfaces
        if int(item["kid"]) != identity.file_id
        and item.get("entity_name")
        and item.get("entity_enriched") is True
    )
    bundle = organize_evidence(evidence, target_file_id=identity.file_id)
    messages = _build_enrichment_messages(
        identity=identity,
        evidence=bundle,
        existing_markdown=existing,
        soft_template=DEFAULT_SOFT_TEMPLATE,
        relation_targets=relation_targets,
        topics=topics,
    )
    slug = re.sub(r"[^\w.-]+", "-", identity.entity_name, flags=re.UNICODE).strip("-")
    entity_dir = output_dir / f"{identity.file_id}-{slug}"
    _write_json(
        entity_dir / "full-input.json",
        {
            "messages": messages,
            "context": analyze_context(
                topics=topics, evidence=list(bundle.fragments), messages=messages
            ),
        },
    )
    (entity_dir / "baseline.md").write_text(existing, encoding="utf-8")
    retrieval_seconds = _seconds(retrieval_started_at)
    full_generation_started_at = perf_counter()
    full_result = await worker._enricher.enrich(  # noqa: SLF001
        identity,
        bundle,
        existing_markdown=existing,
        relation_targets=relation_targets,
        topics=topics,
        incremental=False,
        log_context={"qualityEvaluation": True, "mode": "full"},
    )
    full_generation_seconds = _seconds(full_generation_started_at)
    (entity_dir / "full-output.md").write_text(full_result.markdown, encoding="utf-8")
    full_analysis = analyze_document(
        entity_name=identity.entity_name,
        markdown=full_result.markdown,
        existing_markdown=existing,
        evidence=list(bundle.fragments),
    )
    report: dict[str, Any] = {
        "entity": asdict(identity),
        "topics": list(topics),
        "timingSeconds": {
            "retrievalAndContext": retrieval_seconds,
            "fullGeneration": full_generation_seconds,
        },
        "full": {
            "analysis": full_analysis,
            "judge": None,
            "warnings": list(full_result.warnings),
            "protocol": enrichment_protocol_report(full_result),
        },
    }
    _write_json(entity_dir / "report.json", report)
    full_judge_started_at = perf_counter()
    full_judge = (
        await judge_document(
            judge_llm,
            mode="full",
            entity_name=identity.entity_name,
            topics=topics,
            existing_markdown=existing,
            evidence=list(bundle.fragments),
            candidate_markdown=full_result.markdown,
        )
        if judge
        else None
    )
    full_judge_seconds = _seconds(full_judge_started_at) if judge else 0.0
    report["timingSeconds"]["fullJudge"] = full_judge_seconds
    report["full"]["judge"] = full_judge
    _write_json(entity_dir / "report.json", report)

    no_change_started_at = perf_counter()
    cutoff = datetime.now(UTC)
    incremental_topics = await worker._load_entity_topics(  # noqa: SLF001
        identity,
        updated_after=cutoff,
    )
    new_evidence = await worker._collect_evidence(  # noqa: SLF001
        context,
        identity=identity,
        existing_markdown=full_result.markdown,
        topics=incremental_topics,
        updated_after=cutoff,
    )
    report["noChangeReplay"] = {
        "cutoff": cutoff.isoformat(),
        "newTopicCount": len(incremental_topics),
        "newEvidenceCount": len(new_evidence),
        "passed": not new_evidence,
    }
    report["timingSeconds"]["noChangeReplay"] = _seconds(no_change_started_at)

    baseline_evidence, delta_evidence = split_evidence_for_incremental_replay(
        list(bundle.fragments)
    )
    if incremental_replay and delta_evidence:
        replay_baseline_started_at = perf_counter()
        baseline_result = await worker._enricher.enrich(  # noqa: SLF001
            identity,
            baseline_evidence,
            existing_markdown=existing,
            relation_targets=relation_targets,
            topics=topics,
            incremental=False,
            log_context={"qualityEvaluation": True, "mode": "replay-baseline"},
        )
        replay_baseline_seconds = _seconds(replay_baseline_started_at)
        incremental_generation_started_at = perf_counter()
        incremental_result = await worker._enricher.enrich(  # noqa: SLF001
            identity,
            delta_evidence,
            existing_markdown=baseline_result.markdown,
            relation_targets=relation_targets,
            topics=topics,
            incremental=True,
            log_context={"qualityEvaluation": True, "mode": "incremental"},
        )
        incremental_generation_seconds = _seconds(incremental_generation_started_at)
        (entity_dir / "replay-baseline.md").write_text(
            baseline_result.markdown, encoding="utf-8"
        )
        (entity_dir / "incremental-output.md").write_text(
            incremental_result.markdown, encoding="utf-8"
        )
        incremental_analysis = analyze_document(
            entity_name=identity.entity_name,
            markdown=incremental_result.markdown,
            existing_markdown=baseline_result.markdown,
            evidence=[*baseline_evidence, *delta_evidence],
        )
        report["incrementalReplay"] = {
            "baselineEvidenceCount": len(baseline_evidence),
            "deltaEvidenceCount": len(delta_evidence),
            "analysis": incremental_analysis,
            "judge": None,
            "warnings": list(incremental_result.warnings),
            "baselineProtocol": enrichment_protocol_report(baseline_result),
            "protocol": enrichment_protocol_report(incremental_result),
        }
        report["timingSeconds"].update(
            {
                "replayBaselineGeneration": replay_baseline_seconds,
                "incrementalGeneration": incremental_generation_seconds,
            }
        )
        _write_json(entity_dir / "report.json", report)
        incremental_judge_started_at = perf_counter()
        incremental_judge = (
            await judge_document(
                judge_llm,
                mode="incremental",
                entity_name=identity.entity_name,
                topics=topics,
                existing_markdown=baseline_result.markdown,
                evidence=[*baseline_evidence, *delta_evidence],
                candidate_markdown=incremental_result.markdown,
            )
            if judge
            else None
        )
        report["timingSeconds"]["incrementalJudge"] = (
            _seconds(incremental_judge_started_at) if judge else 0.0
        )
        report["incrementalReplay"]["judge"] = incremental_judge
    else:
        report["incrementalReplay"] = {
            "skipped": True,
            "reason": "insufficient separable evidence",
        }
        report["timingSeconds"].update(
            {
                "replayBaselineGeneration": 0.0,
                "incrementalGeneration": 0.0,
                "incrementalJudge": 0.0,
            }
        )
    report["timingSeconds"]["total"] = _seconds(entity_started_at)
    _write_json(entity_dir / "report.json", report)
    return report


async def _audit_baseline(
    worker: Any, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        context = KnowledgeEntityTaskContext(
            task_id=0,
            task_type="DOCUMENT_ENRICH",
            kb_code=str(row["knowledge_base_id"]),
            knowledge_base_id=int(row["knowledge_base_id"]),
            source_file_id=int(row["kid"]),
            file_path=str(row["file_path"]),
        )
        loaded_row, surfaces = await worker._load_entity_and_current_surfaces(  # noqa: SLF001
            context
        )
        identity = worker._validate_entity_identity(  # noqa: SLF001
            loaded_row, context, current_surfaces=surfaces
        )
        topics = await worker._load_entity_topics(identity)  # noqa: SLF001
        markdown = await worker._read_markdown(loaded_row)  # noqa: SLF001
        result.append(
            {
                "fileId": identity.file_id,
                "entityName": identity.entity_name,
                "filePath": row["file_path"],
                "entityEnriched": row.get("entity_enriched"),
                "topicCount": len(topics),
                "topics": list(topics),
                "markdownChars": len(markdown),
                "referenceCount": len(_links(markdown)),
            }
        )
    return result


async def _evaluate_two_stage_scenario(
    *,
    worker: Any,
    entity_row: dict[str, Any],
    source_paths: tuple[str, str],
    kb_code: str,
    output_dir: Path,
    judge: bool,
    judge_llm: Any,
) -> dict[str, Any]:
    """Replay two real source arrivals without persisting discovery or enrichment."""

    scenario_started_at = perf_counter()
    kb_id = int(entity_row["knowledge_base_id"])
    context = KnowledgeEntityTaskContext(
        task_id=0,
        task_type="DOCUMENT_ENRICH",
        kb_code=kb_code,
        knowledge_base_id=kb_id,
        source_file_id=int(entity_row["kid"]),
        file_path=str(entity_row["file_path"]),
    )
    loaded_row, surfaces = await worker._load_entity_and_current_surfaces(  # noqa: SLF001
        context
    )
    identity = worker._validate_entity_identity(  # noqa: SLF001
        loaded_row, context, current_surfaces=surfaces
    )
    source_rows = [
        await _load_source_row(worker, kb_id=kb_id, path=path) for path in source_paths
    ]
    source_markdowns = [
        await _read_resolved_markdown(worker, kb_id=kb_id, row=row)
        for row in source_rows
    ]
    slug = re.sub(r"[^\w.-]+", "-", identity.entity_name, flags=re.UNICODE).strip("-")
    scenario_dir = output_dir / f"{identity.file_id}-{slug}-two-stage"
    logical_times = (
        "T1: first source imported and discovered",
        "T2: second source imported and discovered after T1 enrich completed",
    )
    stub = worker._render_entity_markdown(  # noqa: SLF001
        entity_name=identity.entity_name,
        body=f"# {identity.entity_name}",
        aliases=identity.aliases,
        subject_file_id=identity.subject_file_id,
        entity_type=None,
        entity_enriched=False,
    )
    (scenario_dir / "initial-entity.md").parent.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "initial-entity.md").write_text(stub, encoding="utf-8")

    stage_records: list[dict[str, Any]] = []
    discoveries: list[DiscoveryResult] = []
    stage_topics: list[tuple[str, ...]] = []
    stage_evidence: list[list[EvidenceFragment]] = []
    names = (identity.entity_name, *identity.aliases)
    for index, (source_path, source_row, markdown) in enumerate(
        zip(source_paths, source_rows, source_markdowns, strict=True), start=1
    ):
        stage_dir = scenario_dir / f"stage-{index}"
        discovery_started_at = perf_counter()
        discovery = await worker._discovery.discover(  # noqa: SLF001
            markdown,
            log_context={
                "qualityEvaluation": True,
                "mode": "two-stage-discovery",
                "stage": index,
                "sourcePath": source_path,
            },
        )
        discovery_seconds = _seconds(discovery_started_at)
        candidate = match_discovered_entity(
            discovery,
            entity_name=identity.entity_name,
            aliases=identity.aliases,
        )
        if candidate is None:
            discovered_names = [item.name for item in discovery.entities]
            raise RuntimeError(
                f"stage {index} did not discover {identity.entity_name}; "
                f"discovered={discovered_names}"
            )
        topics = topics_owned_by_candidate(discovery, entity_ref=candidate.entity_ref)
        evidence = _source_evidence(
            worker,
            source_row=source_row,
            markdown=markdown,
            names=names,
            topics=topics,
        )
        if not evidence:
            raise RuntimeError(f"stage {index} produced no Entity-scoped evidence")
        discoveries.append(discovery)
        stage_topics.append(topics)
        stage_evidence.append(evidence)
        _write_json(
            stage_dir / "discovery-input.json",
            {
                "messages": [
                    {"role": "system", "content": DISCOVERY_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": discovery.context.excerpt
                        if discovery.context
                        else "",
                    },
                ],
                "sourcePath": source_path,
                "sourceFileId": int(source_row["kid"]),
                "sourceMarkdownChars": len(markdown),
            },
        )
        _write_json(
            stage_dir / "discovery-output.json",
            _serialize_discovery(discovery, selected_entity_ref=candidate.entity_ref),
        )
        stage_records.append(
            {
                "stage": index,
                "logicalTime": logical_times[index - 1],
                "sourcePath": source_path,
                "sourceFileId": int(source_row["kid"]),
                "matchedEntity": candidate.name,
                "matchedEntityRef": candidate.entity_ref,
                "discoveredTopics": list(topics),
                "evidenceFragmentCount": len(evidence),
                "evidenceChars": sum(len(item.content) for item in evidence),
                "timingSeconds": {"discovery": discovery_seconds},
            }
        )

    incremental_topics = incremental_topic_delta(stage_topics[0], stage_topics[1])
    enrich_topics = (stage_topics[0], incremental_topics)
    existing_markdowns = [stub]
    outputs: list[str] = []
    analyses: list[dict[str, Any]] = []
    judges: list[dict[str, Any] | None] = []
    relation_targets: tuple[RelationTarget, ...] = ()
    for index in (1, 2):
        stage_dir = scenario_dir / f"stage-{index}"
        evidence = stage_evidence[index - 1]
        bundle = organize_evidence(evidence, target_file_id=identity.file_id)
        existing = existing_markdowns[index - 1]
        topics = enrich_topics[index - 1]
        messages = _build_enrichment_messages(
            identity=identity,
            evidence=bundle,
            existing_markdown=existing,
            soft_template=DEFAULT_SOFT_TEMPLATE,
            relation_targets=relation_targets,
            topics=topics,
            incremental=index == 2,
        )
        _write_json(
            stage_dir / "enrich-input.json",
            {
                "messages": messages,
                "context": analyze_context(
                    topics=topics,
                    evidence=list(bundle.fragments),
                    messages=messages,
                ),
                "topicWindow": {
                    "availableAtStage": list(stage_topics[index - 1]),
                    "passedToEnrich": list(topics),
                    "priorTopicsExcluded": list(stage_topics[0]) if index == 2 else [],
                },
            },
        )
        enrich_started_at = perf_counter()
        result = await worker._enricher.enrich(  # noqa: SLF001
            identity,
            bundle,
            existing_markdown=existing,
            relation_targets=relation_targets,
            topics=topics,
            incremental=index == 2,
            log_context={
                "qualityEvaluation": True,
                "mode": "two-stage-incremental" if index == 2 else "two-stage-initial",
                "stage": index,
            },
        )
        enrich_seconds = _seconds(enrich_started_at)
        output_path = stage_dir / "enrich-output.md"
        output_path.write_text(result.markdown, encoding="utf-8")
        outputs.append(result.markdown)
        if index == 1:
            existing_markdowns.append(result.markdown)
        analysis_evidence = (
            evidence if index == 1 else [*stage_evidence[0], *stage_evidence[1]]
        )
        analysis = analyze_document(
            entity_name=identity.entity_name,
            markdown=result.markdown,
            existing_markdown=existing,
            evidence=analysis_evidence,
        )
        analyses.append(analysis)
        judge_started_at = perf_counter()
        judged = (
            await judge_document(
                judge_llm,
                mode="incremental" if index == 2 else "full",
                entity_name=identity.entity_name,
                topics=(
                    tuple(dict.fromkeys((*stage_topics[0], *stage_topics[1])))
                    if index == 2
                    else stage_topics[0]
                ),
                existing_markdown=existing,
                evidence=analysis_evidence,
                candidate_markdown=result.markdown,
            )
            if judge
            else None
        )
        judge_seconds = _seconds(judge_started_at) if judge else 0.0
        judges.append(judged)
        transition = citation_transition(
            baseline_markdown=existing,
            updated_markdown=result.markdown,
            stage_source=source_paths[index - 1],
        )
        stage_records[index - 1].update(
            {
                "topicsPassedToEnrich": list(topics),
                "incremental": index == 2,
                "analysis": analysis,
                "judge": judged,
                "citationTransition": transition,
                "warnings": list(result.warnings),
                "protocol": enrichment_protocol_report(result),
            }
        )
        stage_records[index - 1]["timingSeconds"].update(
            {"enrich": enrich_seconds, "judge": judge_seconds}
        )
        _write_json(stage_dir / "stage-report.json", stage_records[index - 1])

    stage_passes = [
        bool(
            stage["analysis"]["passed"]
            and stage["citationTransition"]["stageSourceReferenced"]
            and stage["citationTransition"]["oldReferencesPreserved"]
            and (not judge or stage["judge"].get("pass") is True)
        )
        for stage in stage_records
    ]
    report = {
        "scenario": "two-stage-real-source-replay",
        "readOnly": True,
        "entity": asdict(identity),
        "timeline": stage_records,
        "topicTransition": {
            "stage1": list(stage_topics[0]),
            "stage2Discovered": list(stage_topics[1]),
            "stage2Incremental": list(incremental_topics),
        },
        "stagePasses": stage_passes,
        "overallPass": all(stage_passes),
        "timingSeconds": {
            "total": _seconds(scenario_started_at),
            "stage1": round(sum(stage_records[0]["timingSeconds"].values()), 3),
            "stage2": round(sum(stage_records[1]["timingSeconds"].values()), 3),
        },
    }
    _write_json(scenario_dir / "report.json", report)
    return report


async def main() -> int:
    args = _args()
    if args.limit < 0 or args.top_k < 1 or args.concurrency < 1:
        raise SystemExit(
            "--limit must be non-negative; --top-k and --concurrency must be positive"
        )
    if bool(args.two_stage_entity) != bool(args.stage_source):
        raise SystemExit(
            "--two-stage-entity and exactly two --stage-source values must be used together"
        )
    if args.two_stage_entity and len(args.stage_source) != 2:
        raise SystemExit("two-stage replay requires exactly two --stage-source values")
    run_started_at = perf_counter()
    run_started_utc = datetime.now(UTC)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = args.output_dir / run_id
    runtime, judge_llm = await _build_runtime()
    worker = runtime.worker
    kb_id = int(args.kb_code)
    before_fingerprint = await _read_persistence_fingerprint(worker, kb_id)
    rows, unanchored = await _load_rows(worker, kb_id)
    if args.two_stage_entity:
        matching_rows = [
            row
            for row in rows
            if normalize_surface(str(row.get("entity_name") or ""))
            == normalize_surface(args.two_stage_entity)
        ]
        if len(matching_rows) != 1:
            raise SystemExit(
                f"expected exactly one KnowledgeEntity for {args.two_stage_entity!r}; "
                f"found {len(matching_rows)}"
            )
        try:
            report = await _evaluate_two_stage_scenario(
                worker=worker,
                entity_row=matching_rows[0],
                source_paths=(args.stage_source[0], args.stage_source[1]),
                kb_code=args.kb_code,
                output_dir=output_dir,
                judge=not args.skip_judge,
                judge_llm=judge_llm,
            )
        finally:
            after_fingerprint = await _read_persistence_fingerprint(worker, kb_id)
            if after_fingerprint != before_fingerprint:
                raise RuntimeError(
                    "read-only invariant violated: knowledge-base persistence changed"
                )
        summary = {
            "runId": run_id,
            "scenario": report["scenario"],
            "readOnly": True,
            "knowledgeBaseCode": args.kb_code,
            "startedAt": run_started_utc.isoformat(),
            "finishedAt": datetime.now(UTC).isoformat(),
            "durationSeconds": _seconds(run_started_at),
            "persistenceFingerprintBefore": before_fingerprint,
            "persistenceFingerprintAfter": after_fingerprint,
            "persistenceUnchanged": after_fingerprint == before_fingerprint,
            "entity": report["entity"],
            "sources": args.stage_source,
            "stagePasses": report["stagePasses"],
            "overallPass": report["overallPass"],
            "timingSeconds": report["timingSeconds"],
        }
        _write_json(output_dir / "summary.json", summary)
        print(
            json.dumps(
                {"outputDir": str(output_dir), **summary},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if report["overallPass"] else 1
    entity_filters = {item.casefold() for item in args.entity}
    if entity_filters:
        rows = [
            row
            for row in rows
            if str(row.get("entity_name") or "").casefold() in entity_filters
        ]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit("no matching KnowledgeEntity files")

    if args.baseline_only:
        baseline_started_at = perf_counter()
        baseline = await _audit_baseline(worker, rows)
        after_fingerprint = await _read_persistence_fingerprint(worker, kb_id)
        if after_fingerprint != before_fingerprint:
            raise RuntimeError(
                "read-only invariant violated: knowledge-base persistence changed"
            )
        summary = {
            "runId": run_id,
            "readOnly": True,
            "baselineOnly": True,
            "knowledgeBaseCode": args.kb_code,
            "concurrency": 1,
            "startedAt": run_started_utc.isoformat(),
            "finishedAt": datetime.now(UTC).isoformat(),
            "durationSeconds": _seconds(run_started_at),
            "auditDurationSeconds": _seconds(baseline_started_at),
            "persistenceFingerprint": before_fingerprint,
            "entityCount": len(baseline),
            "unanchoredEntities": unanchored,
            "entities": baseline,
        }
        _write_json(output_dir / "baseline-summary.json", summary)
        print(
            json.dumps(
                {"outputDir": str(output_dir), **summary},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    evaluation_started_at = perf_counter()
    results: list[dict[str, Any] | Exception] = []
    try:
        results = await run_bounded_concurrently(
            rows,
            concurrency=args.concurrency,
            evaluate=lambda row: _evaluate_entity(
                worker=worker,
                row=row,
                kb_code=args.kb_code,
                output_dir=output_dir,
                top_k=args.top_k,
                judge=not args.skip_judge,
                incremental_replay=not args.skip_incremental_replay,
                judge_llm=judge_llm,
            ),
        )
    finally:
        after_fingerprint = await _read_persistence_fingerprint(worker, kb_id)
        if after_fingerprint != before_fingerprint:
            raise RuntimeError(
                "read-only invariant violated: knowledge-base persistence changed"
            )
    reports = [item for item in results if isinstance(item, dict)]
    failures = [
        {
            "entity": str(row.get("entity_name") or row.get("file_path") or ""),
            "errorType": type(result).__name__,
            "error": str(result),
        }
        for row, result in zip(rows, results, strict=True)
        if isinstance(result, Exception)
    ]
    evaluation_seconds = _seconds(evaluation_started_at)
    if failures:
        completed_entity_seconds = round(
            sum(item["timingSeconds"]["total"] for item in reports), 3
        )
        failure_summary = {
            "runId": run_id,
            "readOnly": True,
            "concurrency": args.concurrency,
            "startedAt": run_started_utc.isoformat(),
            "finishedAt": datetime.now(UTC).isoformat(),
            "durationSeconds": _seconds(run_started_at),
            "evaluationWallClockSeconds": evaluation_seconds,
            "completedEntitySeconds": completed_entity_seconds,
            "averageCompletedEntitySeconds": (
                round(completed_entity_seconds / len(reports), 3) if reports else None
            ),
            "persistenceFingerprintBefore": before_fingerprint,
            "persistenceFingerprintAfter": after_fingerprint,
            "persistenceUnchanged": after_fingerprint == before_fingerprint,
            "completedEntityCount": len(reports),
            "failedEntityCount": len(failures),
            "failures": failures,
        }
        _write_json(output_dir / "failure-summary.json", failure_summary)
        print(
            json.dumps(
                {"outputDir": str(output_dir), **failure_summary},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    require_judge = not args.skip_judge
    require_incremental = not args.skip_incremental_replay
    report_summaries = []
    for item in reports:
        incremental = item.get("incrementalReplay", {})
        incremental_pass = (
            None
            if incremental.get("skipped")
            else bool(
                incremental.get("analysis", {}).get("passed")
                and (
                    not require_judge
                    or incremental.get("judge", {}).get("pass") is True
                )
            )
        )
        report_summaries.append(
            {
                "entity": item["entity"]["entity_name"],
                "durationSeconds": item["timingSeconds"]["total"],
                "fullDeterministicPass": item["full"]["analysis"]["passed"],
                "fullJudgePass": (
                    bool(item["full"]["judge"] and item["full"]["judge"].get("pass"))
                    if require_judge
                    else None
                ),
                "noChangePass": item["noChangeReplay"]["passed"],
                "incrementalReplayPass": incremental_pass,
            }
        )
    overall_pass = all(
        item["fullDeterministicPass"]
        and (not require_judge or item["fullJudgePass"] is True)
        and item["noChangePass"]
        and (
            not require_incremental
            or item["incrementalReplayPass"] is True
            or item["incrementalReplayPass"] is None
        )
        for item in report_summaries
    )
    entity_durations = [item["timingSeconds"]["total"] for item in reports]
    cumulative_entity_seconds = round(sum(entity_durations), 3)
    summary = {
        "runId": run_id,
        "readOnly": True,
        "concurrency": args.concurrency,
        "startedAt": run_started_utc.isoformat(),
        "finishedAt": datetime.now(UTC).isoformat(),
        "durationSeconds": _seconds(run_started_at),
        "evaluationWallClockSeconds": evaluation_seconds,
        "cumulativeEntitySeconds": cumulative_entity_seconds,
        "effectiveParallelism": round(
            cumulative_entity_seconds / max(evaluation_seconds, 0.001), 3
        ),
        "averageEntitySeconds": round(cumulative_entity_seconds / len(reports), 3),
        "minEntitySeconds": min(entity_durations),
        "maxEntitySeconds": max(entity_durations),
        "persistenceFingerprint": before_fingerprint,
        "knowledgeBaseCode": args.kb_code,
        "entityCount": len(reports),
        "unanchoredEntities": unanchored,
        "fullDeterministicPassCount": sum(
            item["full"]["analysis"]["passed"] for item in reports
        ),
        "fullJudgePassCount": (
            sum(
                bool(item["full"]["judge"] and item["full"]["judge"].get("pass"))
                for item in reports
            )
            if require_judge
            else None
        ),
        "noChangePassCount": sum(item["noChangeReplay"]["passed"] for item in reports),
        "overallPass": overall_pass,
        "reports": report_summaries,
    }
    _write_json(output_dir / "summary.json", summary)
    print(
        json.dumps(
            {"outputDir": str(output_dir), **summary}, ensure_ascii=False, indent=2
        )
    )
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
