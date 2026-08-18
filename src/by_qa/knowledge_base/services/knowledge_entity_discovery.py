"""KnowledgeEntity discovery models, prompts, normalization, and LLM workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
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
    SurfaceMatch,
    _complete_strict_json,
    _safe_log_context,
    build_discovery_context,
    normalize_surface,
)

DISCOVERY_SYSTEM_PROMPT = """\
You are a document-level core object entity discoverer, not a word scanner or
event extractor. Discover KnowledgeEntity v1 identities from one document.

Before producing JSON, internally perform this protocol:
1. Identify the document's information task, direct research subject, structure,
   and conclusions.
2. For each candidate, ask whether it has a stable identity reusable across time
   and documents. If it describes an occurrence, change, or state fact, it is an
   event and must not be returned.
3. Decide identity scope. Only an object independently referable outside this
   document and still denoting the same object is global. Components, mechanisms,
   layers, roles, classifications, and internal tools defined, named, organized,
   or owned by the research subject are subject-scoped, even when they have an
   English name or abbreviation.
4. Rank direct research subjects, cross-section parent objects, main components or
   categories, and conclusion-critical subjects ahead of cases, tools,
   implementation details, and incidental mentions.
5. Apply the deletion test: remove a candidate if its deletion does not impair
   understanding of the main topic, structure, key relations, or conclusions.

General constraints:
- Entity discovery answers "who/what is it", not "what happened". Events never
  become entities; their time, action, and result may only support stable subjects.
- When one fact involves several objects, keep only stable subjects indispensable
  to understanding that fact.
- A title is a strong signal for the research subject, but the title, source,
  author, and filename do not automatically become entities or local subjects.
- A document, report, specification, style guide, or announcement collection is
  not an entity merely because it has a title or metadata object type. Extract the
  stable subjects discussed by it instead.
- Repetition alone does not imply importance. Examples, quotations, lists,
  dependencies, fields, functions, and implementation details must not displace
  parent objects or key conclusions.
- Front-matter references may be governance dependencies, but they are secondary
  to stable concepts the document itself defines and must not displace them.
- If the document centers on one direct subject, return it first. Internal
  candidates introduced specifically for it are subject-scoped by default. If the
  document defines a comparison or classification framework, its categories are
  scoped to that framework rather than made global.
- For a subject profile, prioritize objects deciding identity, ownership, or
  governance. For conceptual material, prioritize first-level concepts repeatedly
  defined across parallel sections and the summary.
- A result defined or implemented by the research subject remains subject-scoped
  in this document.
- Relation names, property names, role names, generic collections, and mere section
  labels are not named objects. However, in a policy, specification, or style
  guide, a first-level rule domain that defines reusable constraints across a full
  section is a stable subject-scoped concept, not a mere heading. Extract the
  major rule domains needed to understand and apply the guide.
- A profile or list about one subject normally keeps that subject and at most four
  named objects deciding its identity, ownership, or governance.
- Coverage must favor major sections and conclusions. Replace selected cases or
  implementation items when a first-level topic or conclusion was omitted.
- A clearly independent third-party object recognizable in other material remains
  global. Named people, organizations, places, and independent products stay
  global when independently identifiable; relationship ownership is not identity
  ownership.
- Single-topic material normally returns 1-5 entities; complex or multi-topic
  material returns 5-12; never return more than 12.
- For a technical analysis with five or more first-level architecture topics,
  normally return 8-12 entities and cover every conclusion-critical first-level
  topic before optional dependencies or examples. Prefer explicitly named public
  classes, registries, engines, protocols, and DSLs that define the architecture
  over internal database table identifiers.
- For a policy, specification, or style guide with several first-level rule
  domains, return the governed subject plus the 6-10 most important rule domains.
  Cover structure, media/format, expression, compliance, and interaction before
  externally referenced templates or assets.

Identity and naming protocol:
- global: subjectEntityName is empty, entityName equals localName.
- subject: subjectEntityName is a stable subject explicitly present in the
  document; localName is its internal local concept; entityName must equal
  "{subjectEntityName}-{localName}" using one ASCII hyphen without spaces.
- Every subjectEntityName must also appear as a global candidate in the same array.
  Choose the nearest stable object that truly owns the local concept.
- If an object has both an abbreviation and explanatory full name, use the most
  common concise unambiguous form for identity and put other forms in aliases.
- Preserve the canonical spelling and word boundaries used in source text; do not
  remove spaces or invent a new compound identifier.
- Do not return a local concept without a stable owner, or an ordinary description
  of its owner.
- evidence must be a short, verbatim, continuous quote from the supplied context.

Existing vocabulary is intentionally resolved after extraction. It must not
influence candidate selection, and identical content must yield the same semantic
identities regardless of external state, including vocabulary contents.

Output one strict JSON array and nothing else. Each item may contain only:
entityName, localName, aliases, identityScope, subjectEntityName, subjectFileId,
entityType, candidateKind, stableIdentity, isEvent, isFact, identityReason,
salienceReason, evidence. candidateKind must be "entity".
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
        known_matches: Sequence[SurfaceMatch] = (),
        max_entities: int = MAX_DISCOVERED_ENTITIES,
        log_context: Mapping[str, Any] | None = None,
    ) -> EntityDiscoveryResult:
        discovery_started_at = time.perf_counter()
        context = build_discovery_context(markdown)
        bounded_max_entities = min(max(max_entities, 1), MAX_DISCOVERED_ENTITIES)
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "prompt": DISCOVERY_SYSTEM_PROMPT,
                    "context": context.excerpt,
                    "maxEntities": bounded_max_entities,
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
            "known_match_count=%s max_entities=%s context_truncated=%s%s",
            len(markdown),
            len(known_matches),
            max_entities,
            context.truncated,
            _safe_log_context(log_context),
        )
        base_messages: list[dict[str, str]] = [
            {"role": "system", "content": DISCOVERY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Document context:\n{context.excerpt}",
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
        if (
            not line
            or line.startswith("[DOCUMENT ")
            or re.match(r"^L\d+\s+#{1,6}\s", line)
        ):
            continue
        normalized_line = normalize_surface(line)
        if any(surface in normalized_line for surface in normalized_surfaces):
            return line
    return None


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
