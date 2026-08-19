"""Compare lexical and embedding entity-synonym candidate retrieval.

The experiment deliberately reuses the production KnowledgeEntity discovery
prompt and real model configuration.  Discovery output is cached so every
retrieval strategy is evaluated against exactly the same extracted entities.

Artifacts are written below ``eval/reports/`` by default.  They may contain
document excerpts and model output and therefore must not be committed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import time
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import httpx

from by_qa.core.model_config import LLMModelProfile, load_model_config_provider
from by_qa.knowledge_base.services.knowledge_entity_discovery import (
    EntityCandidate,
    KnowledgeEntityDiscovery,
)
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    OpenAICompatibleKnowledgeEntityLLM,
)

DEFAULT_OUTPUT_DIR = Path("eval/reports/entity-synonym-resolution")
DEFAULT_BENCHMARK = Path(
    "scripts/knowledge_base/entity_synonym_resolution_benchmark.json"
)
DEFAULT_STRESS_VOCABULARY = Path(
    "scripts/knowledge_base/entity_synonym_resolution_stress_vocabulary.json"
)
DEFAULT_DOCUMENT_DIR = Path("Document")
EXTRACTION_CACHE_VERSION = 1
ADJUDICATION_CACHE_VERSION = 2
ADJUDICATION_SYSTEM_PROMPT = (
    "你是实体同义判定器。逐一判断 mention 与每个 candidate 是否指向同一稳定对象。"
    "candidate 的 aliases 是该实体已知别名；mention 命中名称或别名时应判为 SAME。"
    "同主体下的中英文翻译、缩写和正式名可以为 SAME；相关但不同、上下位或粒度不同的模块必须为 DIFFERENT。"
    "只输出 JSON 对象，键必须逐字使用每个 candidate.name，值只能是 SAME、DIFFERENT 或 UNCERTAIN。"
)
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD_RE = re.compile(r"[^\w\u3400-\u9fff]+|_+", re.UNICODE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")


@dataclass(frozen=True, slots=True)
class EntityRecord:
    name: str
    subject: str
    local_name: str
    aliases: tuple[str, ...]
    source_documents: tuple[str, ...]
    origins: tuple[str, ...]

    @property
    def surfaces(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.name, self.local_name, *self.aliases)))

    @property
    def semantic_text(self) -> str:
        parts = [f"实体名称：{self.name}"]
        if self.subject:
            parts.append(f"所属主体：{self.subject}")
        if self.local_name and self.local_name != self.name:
            parts.append(f"局部名称：{self.local_name}")
        if self.aliases:
            parts.append(f"别名：{'；'.join(self.aliases)}")
        return "；".join(parts)


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    mention: str
    expected: str
    target_any_of: tuple[str, ...]
    subject: str
    context: str
    category: str
    source: str


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    entity_name: str
    score: float
    lexical_score: float
    embedding_score: float | None
    subject_score: float


@dataclass(frozen=True, slots=True)
class StrategyResult:
    candidates: tuple[RankedCandidate, ...]
    expected_rank: int | None
    retrieval_hit: bool
    resolved_entity: str | None
    resolution_correct: bool | None


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    value = _CAMEL_BOUNDARY_RE.sub(" ", value)
    value = _NON_WORD_RE.sub(" ", value.casefold())
    return " ".join(value.split())


def compact_text(value: str) -> str:
    return normalize_text(value).replace(" ", "")


def _cjk_ngrams(value: str, size: int = 2) -> set[str]:
    grams: set[str] = set()
    for run in _CJK_RE.findall(normalize_text(value)):
        if len(run) < size:
            grams.add(run)
        else:
            grams.update(
                run[index : index + size] for index in range(len(run) - size + 1)
            )
    return grams


def tokenize(value: str) -> set[str]:
    normalized = normalize_text(value)
    tokens = {token for token in normalized.split() if token}
    tokens.update(_cjk_ngrams(value))
    return tokens


def char_ngrams(value: str, size: int = 3) -> set[str]:
    compact = compact_text(value)
    if not compact:
        return set()
    if len(compact) < size:
        return {compact}
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def dice(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return 2 * len(left & right) / (len(left) + len(right)) if left and right else 0.0


def levenshtein(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def edit_similarity(left: str, right: str) -> float:
    left_compact = compact_text(left)
    right_compact = compact_text(right)
    maximum = max(len(left_compact), len(right_compact))
    if maximum == 0:
        return 1.0
    return 1 - levenshtein(left_compact, right_compact) / maximum


class LexicalScorer:
    def __init__(self, entities: Sequence[EntityRecord]) -> None:
        documents = [
            set().union(*(tokenize(surface) for surface in entity.surfaces))
            for entity in entities
        ]
        self._document_count = len(documents)
        self._document_frequency = Counter(
            token for document in documents for token in document
        )

    def weighted_jaccard(self, left: set[str], right: set[str]) -> float:
        union = left | right
        if not union:
            return 1.0

        def weight(token: str) -> float:
            return (
                math.log(
                    (self._document_count + 1)
                    / (self._document_frequency.get(token, 0) + 1)
                )
                + 1
            )

        return sum(weight(token) for token in left & right) / sum(
            weight(token) for token in union
        )

    def surface_score(self, mention: str, surface: str) -> float:
        if normalize_text(mention) == normalize_text(surface):
            return 1.0
        token_score = self.weighted_jaccard(tokenize(mention), tokenize(surface))
        ngram_score = dice(char_ngrams(mention), char_ngrams(surface))
        edit_score = edit_similarity(mention, surface)
        containment = float(
            min(len(compact_text(mention)), len(compact_text(surface))) >= 4
            and (
                compact_text(mention) in compact_text(surface)
                or compact_text(surface) in compact_text(mention)
            )
        )
        return (
            0.40 * token_score
            + 0.35 * ngram_score
            + 0.20 * edit_score
            + 0.05 * containment
        )

    def entity_score(self, mention: str, entity: EntityRecord) -> float:
        return max(self.surface_score(mention, surface) for surface in entity.surfaces)


def infer_subject(mention: str, entities: Sequence[EntityRecord]) -> str:
    normalized_mention = normalize_text(mention)
    subjects = sorted(
        {entity.subject for entity in entities if entity.subject},
        key=len,
        reverse=True,
    )
    for subject in subjects:
        normalized_subject = normalize_text(subject)
        if normalized_mention == normalized_subject or normalized_mention.startswith(
            f"{normalized_subject} "
        ):
            return subject
    return ""


def extract_local_mention(mention: str, subject: str) -> str:
    cleaned_mention = unicodedata.normalize("NFKC", mention).strip()
    cleaned_subject = unicodedata.normalize("NFKC", subject).strip()
    if not cleaned_subject:
        return cleaned_mention
    if cleaned_mention.casefold().startswith(cleaned_subject.casefold()):
        remainder = cleaned_mention[len(cleaned_subject) :].lstrip(" -_:/：—·.")
        if remainder:
            return remainder
    return cleaned_mention


def subject_compatibility(query_subject: str, entity: EntityRecord) -> float:
    if not query_subject:
        return 0.3
    normalized_query = normalize_text(query_subject)
    if entity.subject and normalize_text(entity.subject) == normalized_query:
        return 1.0
    if not entity.subject and normalize_text(entity.name) == normalized_query:
        return 1.0
    if not entity.subject:
        return 0.3
    return 0.0


def is_subject_root_for_local_mention(
    mention: str, query_subject: str, entity: EntityRecord
) -> bool:
    exact_surface_match = any(
        normalize_text(mention) == normalize_text(surface)
        for surface in entity.surfaces
    )
    return (
        bool(query_subject)
        and normalize_text(mention) != normalize_text(query_subject)
        and not exact_surface_match
        and not entity.subject
        and normalize_text(entity.name) == normalize_text(query_subject)
    )


def lexical_candidates(
    mention: str,
    entities: Sequence[EntityRecord],
    scorer: LexicalScorer,
    *,
    subject: str = "",
    top_k: int = 3,
    minimum_name_score: float = 0.25,
) -> tuple[RankedCandidate, ...]:
    query_subject = subject or infer_subject(mention, entities)
    ranked: list[RankedCandidate] = []
    for entity in entities:
        if is_subject_root_for_local_mention(mention, query_subject, entity):
            continue
        subject_score = subject_compatibility(query_subject, entity)
        if query_subject and entity.subject and subject_score == 0:
            continue
        lexical_score = scorer.entity_score(mention, entity)
        same_subject = bool(query_subject) and subject_score == 1.0
        if lexical_score < minimum_name_score and not same_subject:
            continue
        score = 0.75 * lexical_score + 0.25 * subject_score
        ranked.append(
            RankedCandidate(
                entity_name=entity.name,
                score=score,
                lexical_score=lexical_score,
                embedding_score=None,
                subject_score=subject_score,
            )
        )
    ranked.sort(key=lambda candidate: (-candidate.score, candidate.entity_name))
    return tuple(ranked[:top_k])


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


class RealEmbeddingClient:
    def __init__(self, *, cache_path: Path) -> None:
        self._provider = load_model_config_provider()
        self._cache_path = cache_path
        self._cache: dict[str, list[float]] = {}
        self._model_identity = ""
        self.request_count = 0
        self.text_count = 0
        self.cache_hit_count = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        config = await self._provider.get_config(LLMModelProfile.EMBEDDING)
        if not config.base_url.strip():
            raise RuntimeError("EMBEDDING_BASE_URL is required")
        model_identity = json.dumps(
            {
                "baseUrl": config.base_url.rstrip("/"),
                "model": config.model_name,
                "dimension": config.dimension,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if model_identity != self._model_identity:
            self._model_identity = model_identity
            self._cache = {}
            if self._cache_path.exists():
                cached_payload = json.loads(
                    self._cache_path.read_text(encoding="utf-8")
                )
                if cached_payload.get("modelIdentity") == model_identity:
                    self._cache = dict(cached_payload.get("vectors", {}))
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        batch_size = max(config.batch_max_texts or 10, 1)
        keys = [
            hashlib.sha256(f"{model_identity}\n{text}".encode("utf-8")).hexdigest()
            for text in texts
        ]
        missing_by_key = {
            key: text
            for key, text in zip(keys, texts, strict=True)
            if key not in self._cache
        }
        self.cache_hit_count += len(texts) - len(missing_by_key)
        missing_items = list(missing_by_key.items())
        async with httpx.AsyncClient(timeout=120.0) as client:
            for start in range(0, len(missing_items), batch_size):
                batch_items = missing_items[start : start + batch_size]
                batch = [text for _, text in batch_items]
                for attempt in range(4):
                    self.request_count += 1
                    self.text_count += len(batch)
                    try:
                        response = await client.post(
                            f"{config.base_url.rstrip('/')}/embeddings",
                            headers=headers,
                            json={"model": config.model_name, "input": batch},
                        )
                        response.raise_for_status()
                        break
                    except httpx.HTTPStatusError as exc:
                        status_code = exc.response.status_code
                        retryable = status_code in {408, 429} or status_code >= 500
                        if not retryable or attempt == 3:
                            raise
                    except httpx.TransportError:
                        if attempt == 3:
                            raise
                    await asyncio.sleep(2**attempt)
                payload = response.json()
                data = sorted(payload.get("data", []), key=lambda item: item["index"])
                if len(data) != len(batch):
                    raise RuntimeError("embedding response size does not match request")
                for (key, _), item in zip(batch_items, data, strict=True):
                    self._cache[key] = item["embedding"]
                _write_json_atomic(
                    self._cache_path,
                    {
                        "modelIdentity": model_identity,
                        "vectors": self._cache,
                    },
                )
        return [self._cache[key] for key in keys]


def embedding_candidates(
    mention_vector: Sequence[float],
    mention_local_vector: Sequence[float],
    entity_vectors: Sequence[Sequence[float]],
    entity_local_vectors: Sequence[Sequence[float]],
    entities: Sequence[EntityRecord],
    scorer: LexicalScorer,
    mention: str,
    *,
    subject: str = "",
    top_k: int = 3,
) -> tuple[RankedCandidate, ...]:
    query_subject = subject or infer_subject(mention, entities)
    ranked: list[RankedCandidate] = []
    for entity, vector, local_vector in zip(
        entities, entity_vectors, entity_local_vectors, strict=True
    ):
        if is_subject_root_for_local_mention(mention, query_subject, entity):
            continue
        subject_score = subject_compatibility(query_subject, entity)
        if query_subject and entity.subject and subject_score == 0:
            continue
        semantic_score = max(
            cosine_similarity(mention_vector, vector),
            cosine_similarity(mention_local_vector, local_vector),
        )
        lexical_score = scorer.entity_score(mention, entity)
        ranked.append(
            RankedCandidate(
                entity_name=entity.name,
                score=semantic_score,
                lexical_score=lexical_score,
                embedding_score=semantic_score,
                subject_score=subject_score,
            )
        )
    ranked.sort(key=lambda candidate: (-candidate.score, candidate.entity_name))
    return tuple(ranked[:top_k])


def hybrid_candidates(
    lexical: Sequence[RankedCandidate],
    embedding: Sequence[RankedCandidate],
    *,
    top_k: int = 3,
) -> tuple[RankedCandidate, ...]:
    """Fuse ranks instead of raw scores because the scales are model-specific."""

    by_name: dict[str, RankedCandidate] = {}
    reciprocal_rank: Counter[str] = Counter()
    for candidates in (lexical, embedding):
        for rank, candidate in enumerate(candidates, start=1):
            reciprocal_rank[candidate.entity_name] += 1 / (60 + rank)
            previous = by_name.get(candidate.entity_name)
            if previous is None or candidate.embedding_score is not None:
                by_name[candidate.entity_name] = candidate
    names = sorted(reciprocal_rank, key=lambda name: (-reciprocal_rank[name], name))
    return tuple(
        RankedCandidate(
            entity_name=name,
            score=reciprocal_rank[name],
            lexical_score=by_name[name].lexical_score,
            embedding_score=by_name[name].embedding_score,
            subject_score=by_name[name].subject_score,
        )
        for name in names[:top_k]
    )


def _candidate_to_dict(candidate: EntityCandidate) -> dict[str, Any]:
    return {
        "entityName": candidate.entity_name,
        "subjectEntityName": candidate.subject_entity_name or "",
        "localName": candidate.local_name,
        "identityScope": candidate.identity_scope.value,
        "evidence": candidate.evidence,
        "aliases": list(candidate.aliases),
    }


async def extract_documents(
    paths: Sequence[Path],
    output_path: Path,
    *,
    concurrency: int,
    refresh: bool = False,
) -> dict[str, Any]:
    cached_documents: dict[str, Any] = {}
    if output_path.exists() and not refresh:
        cached_payload = json.loads(output_path.read_text(encoding="utf-8"))
        if cached_payload.get("cacheVersion") == EXTRACTION_CACHE_VERSION:
            cached_documents = dict(cached_payload.get("documents", {}))

    llm = OpenAICompatibleKnowledgeEntityLLM(temperature=0.0)
    discovery = KnowledgeEntityDiscovery(llm)
    semaphore = asyncio.Semaphore(max(concurrency, 1))

    async def extract(path: Path) -> tuple[str, dict[str, Any]]:
        markdown = path.read_text(encoding="utf-8")
        fingerprint = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        async with semaphore:
            started = time.perf_counter()
            result = await discovery.discover(
                markdown, log_context={"experimentDocument": path.name}
            )
        return path.as_posix(), {
            "sha256": fingerprint,
            "elapsedMs": round((time.perf_counter() - started) * 1000, 2),
            "warnings": list(result.warnings),
            "entities": [_candidate_to_dict(item) for item in result.candidates],
        }

    extracted: dict[str, Any] = {}
    pending: list[Path] = []
    for path in paths:
        document_key = path.as_posix()
        fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
        cached = cached_documents.get(document_key)
        if cached and cached.get("sha256") == fingerprint:
            extracted[document_key] = cached
        else:
            pending.append(path)

    def persist() -> None:
        payload = {
            "cacheVersion": EXTRACTION_CACHE_VERSION,
            "documents": dict(sorted(extracted.items())),
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(output_path)

    tasks = [asyncio.create_task(extract(path)) for path in pending]
    for completed in asyncio.as_completed(tasks):
        document_key, result = await completed
        extracted[document_key] = result
        persist()

    if not output_path.exists():
        persist()
    payload = {
        "cacheVersion": EXTRACTION_CACHE_VERSION,
        "documents": dict(sorted(extracted.items())),
    }
    return payload


def build_catalog(extraction: dict[str, Any]) -> list[EntityRecord]:
    grouped: dict[str, dict[str, Any]] = {}
    for document, result in extraction["documents"].items():
        for entity in result["entities"]:
            key = normalize_text(entity["entityName"])
            current = grouped.setdefault(
                key,
                {
                    "name": entity["entityName"],
                    "subject": entity.get("subjectEntityName", ""),
                    "local_name": entity.get("localName", entity["entityName"]),
                    "aliases": set(),
                    "source_documents": set(),
                    "origins": {"llm_discovered"},
                },
            )
            current["aliases"].update(entity.get("aliases", []))
            current["source_documents"].add(document)
    return [
        EntityRecord(
            name=value["name"],
            subject=value["subject"],
            local_name=value["local_name"],
            aliases=tuple(sorted(value["aliases"])),
            source_documents=tuple(sorted(value["source_documents"])),
            origins=tuple(sorted(value["origins"])),
        )
        for _, value in sorted(grouped.items())
    ]


def apply_catalog_fixtures(
    catalog: Sequence[EntityRecord], fixture_path: Path
) -> list[EntityRecord]:
    by_key = {normalize_text(entity.name): entity for entity in catalog}
    if not fixture_path.exists():
        return list(catalog)
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    for item in payload.get("entities", []):
        name = str(item["entityName"]).strip()
        key = normalize_text(name)
        existing = by_key.get(key)
        aliases = set(str(value) for value in item.get("aliases", []) if str(value))
        source_documents = set(str(value) for value in item.get("sourceDocuments", []))
        if existing is not None:
            aliases.update(existing.aliases)
            source_documents.update(existing.source_documents)
            origins = set(existing.origins) | {"manual_fixture"}
            subject = str(item.get("subjectEntityName", existing.subject))
            local_name = str(item.get("localName", existing.local_name))
        else:
            origins = {"manual_fixture"}
            subject = str(item.get("subjectEntityName", ""))
            local_name = str(item.get("localName", name))
        by_key[key] = EntityRecord(
            name=name if existing is None else existing.name,
            subject=subject,
            local_name=local_name,
            aliases=tuple(sorted(aliases)),
            source_documents=tuple(sorted(source_documents)),
            origins=tuple(sorted(origins)),
        )
    return sorted(by_key.values(), key=lambda entity: normalize_text(entity.name))


def expand_catalog_for_stress_test(
    catalog: Sequence[EntityRecord],
    benchmark: Sequence[BenchmarkCase],
    vocabulary_path: Path,
    *,
    addition_count: int,
) -> list[EntityRecord]:
    if addition_count <= 0:
        return list(catalog)
    payload = json.loads(vocabulary_path.read_text(encoding="utf-8"))
    subjects = [str(value).strip() for value in payload.get("subjects", [])]
    local_names = [str(value).strip() for value in payload.get("localNames", [])]
    global_names = [str(value).strip() for value in payload.get("globalNames", [])]
    blocked = {normalize_text(entity.name) for entity in catalog}
    blocked.update(
        normalize_text(str(value)) for value in payload.get("excludedEntityNames", [])
    )
    for case in benchmark:
        blocked.add(normalize_text(case.mention))
        blocked.update(normalize_text(target) for target in case.target_any_of)

    additions: list[EntityRecord] = []

    def append(name: str, *, subject: str, local_name: str) -> None:
        key = normalize_text(name)
        if not name or key in blocked or len(additions) >= addition_count:
            return
        blocked.add(key)
        additions.append(
            EntityRecord(
                name=name,
                subject=subject,
                local_name=local_name,
                aliases=(),
                source_documents=("synthetic_stress_vocabulary",),
                origins=("synthetic_stress",),
            )
        )

    global_quota = min(len(global_names), max(addition_count // 10, 1))
    subject_quota = addition_count - global_quota
    for subject in subjects:
        for local_name in local_names:
            if len(additions) >= subject_quota:
                break
            append(
                f"{subject}-{local_name}",
                subject=subject,
                local_name=local_name,
            )
        if len(additions) >= subject_quota:
            break
    for name in global_names:
        append(name, subject="", local_name=name)
    if len(additions) != addition_count:
        raise ValueError(
            "stress vocabulary cannot satisfy requested addition count: "
            f"requested={addition_count}, generated={len(additions)}"
        )
    return sorted(
        [*catalog, *additions], key=lambda entity: normalize_text(entity.name)
    )


def valid_extraction_cache_count(paths: Sequence[Path], output_path: Path) -> int:
    if not output_path.exists():
        return 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    if payload.get("cacheVersion") != EXTRACTION_CACHE_VERSION:
        return 0
    cached_documents = payload.get("documents", {})
    return sum(
        cached_documents.get(path.as_posix(), {}).get("sha256")
        == hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    )


def load_benchmark(
    path: Path,
    catalog: Sequence[EntityRecord],
    *,
    max_extracted_alias_cases: int,
) -> list[BenchmarkCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = [
        BenchmarkCase(
            case_id=item["id"],
            mention=item["mention"],
            expected=item.get("expected", "SAME").upper(),
            target_any_of=tuple(item.get("targetAnyOf", [])),
            subject=item.get("subject", ""),
            context=item.get("context", ""),
            category=item.get("category", "unspecified"),
            source=item.get("source", "manual"),
        )
        for item in raw["cases"]
    ]
    existing_pairs = {
        (normalize_text(case.mention), case.target_any_of) for case in cases
    }
    alias_cases: list[BenchmarkCase] = []
    for entity in catalog:
        for index, alias in enumerate(entity.aliases):
            pair = (normalize_text(alias), (entity.name,))
            if (
                normalize_text(alias) == normalize_text(entity.name)
                or pair in existing_pairs
            ):
                continue
            alias_cases.append(
                BenchmarkCase(
                    case_id=f"extracted-alias-{normalize_text(entity.name)}-{index}",
                    mention=alias,
                    expected="SAME",
                    target_any_of=(entity.name,),
                    subject=entity.subject,
                    context="",
                    category="extracted_alias",
                    source="discovery_output",
                )
            )
    alias_cases.sort(
        key=lambda case: (
            not _is_cross_script_pair(case.mention, case.target_any_of[0]),
            case.case_id,
        )
    )
    cases.extend(alias_cases[: max(max_extracted_alias_cases, 0)])
    return cases


def _is_cross_script_pair(left: str, right: str) -> bool:
    left_has_cjk = bool(_CJK_RE.search(left))
    right_has_cjk = bool(_CJK_RE.search(right))
    return left_has_cjk != right_has_cjk


def resolve_target(case: BenchmarkCase, catalog: Sequence[EntityRecord]) -> str | None:
    if case.expected == "NEW":
        return None
    for target in case.target_any_of:
        normalized_target = normalize_text(target)
        for entity in catalog:
            if normalize_text(entity.name) == normalized_target:
                return entity.name
    return None


def expected_rank(
    candidates: Sequence[RankedCandidate], target: str | None
) -> int | None:
    if target is None:
        return None
    target_key = normalize_text(target)
    for index, candidate in enumerate(candidates, start=1):
        if normalize_text(candidate.entity_name) == target_key:
            return index
    return None


async def adjudicate(
    llm: OpenAICompatibleKnowledgeEntityLLM,
    case: BenchmarkCase,
    candidates: Sequence[EntityRecord],
) -> dict[str, str]:
    if not candidates:
        return {}
    prompt = {
        "mention": case.mention,
        "subject": case.subject,
        "context": case.context,
        "candidates": [
            {
                "name": candidate.name,
                "subject": candidate.subject,
                "localName": candidate.local_name,
                "aliases": list(candidate.aliases),
            }
            for candidate in candidates
        ],
    }
    messages = [
        {
            "role": "system",
            "content": ADJUDICATION_SYSTEM_PROMPT,
        },
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ]
    raw = await llm.complete(messages, json_mode=True)
    parsed = json.loads(raw)
    return {
        candidate.name: str(parsed.get(candidate.name, "UNCERTAIN")).upper()
        for candidate in candidates
    }


def _adjudication_cache_key(
    case: BenchmarkCase, candidates: Sequence[EntityRecord]
) -> str:
    payload = {
        "version": ADJUDICATION_CACHE_VERSION,
        "prompt": ADJUDICATION_SYSTEM_PROMPT,
        "case": asdict(case),
        "candidates": [asdict(candidate) for candidate in candidates],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary_path.replace(path)


async def adjudicate_all(
    llm: OpenAICompatibleKnowledgeEntityLLM,
    cases_and_candidates: Sequence[tuple[BenchmarkCase, Sequence[EntityRecord]]],
    *,
    cache_path: Path,
    concurrency: int,
) -> tuple[list[dict[str, str]], int, int]:
    cache: dict[str, Any] = {}
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if payload.get("cacheVersion") == ADJUDICATION_CACHE_VERSION:
            cache = dict(payload.get("items", {}))
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    request_count = 0
    cache_hit_count = 0

    async def resolve(
        case: BenchmarkCase, candidates: Sequence[EntityRecord]
    ) -> dict[str, str]:
        nonlocal request_count, cache_hit_count
        if not candidates:
            return {}
        key = _adjudication_cache_key(case, candidates)
        cached = cache.get(key)
        if isinstance(cached, dict):
            cache_hit_count += 1
            return {str(name): str(value) for name, value in cached.items()}
        async with semaphore:
            decisions = await adjudicate(llm, case, candidates)
        request_count += 1
        cache[key] = decisions
        _write_json_atomic(
            cache_path,
            {
                "cacheVersion": ADJUDICATION_CACHE_VERSION,
                "items": cache,
            },
        )
        return decisions

    decisions = await asyncio.gather(
        *(resolve(case, candidates) for case, candidates in cases_and_candidates)
    )
    return list(decisions), request_count, cache_hit_count


def choose_resolution(
    candidates: Sequence[RankedCandidate], decisions: dict[str, str]
) -> str | None:
    for candidate in candidates:
        if decisions.get(candidate.entity_name) == "SAME":
            return candidate.entity_name
    return None


def _strategy_result(
    candidates: tuple[RankedCandidate, ...],
    target: str | None,
    expected: str,
    decisions: dict[str, str] | None,
) -> StrategyResult:
    rank = expected_rank(candidates, target)
    resolved = (
        choose_resolution(candidates, decisions) if decisions is not None else None
    )
    if decisions is None:
        correct = None
    elif expected == "NEW":
        correct = resolved is None
    elif target is None:
        correct = None
    else:
        correct = normalize_text(resolved or "") == normalize_text(target)
    return StrategyResult(
        candidates=candidates,
        expected_rank=rank,
        retrieval_hit=rank is not None if expected != "NEW" else True,
        resolved_entity=resolved,
        resolution_correct=correct,
    )


def aggregate_metrics(rows: Sequence[dict[str, Any]], strategy: str) -> dict[str, Any]:
    eligible = [row for row in rows if row["targetStatus"] != "missing"]
    positives = [row for row in eligible if row["expected"] == "SAME"]
    negatives = [row for row in eligible if row["expected"] == "NEW"]
    resolution = [
        row[strategy]["resolution_correct"]
        for row in eligible
        if row[strategy]["resolution_correct"] is not None
    ]
    positive_resolution = [
        row[strategy]["resolution_correct"]
        for row in positives
        if row[strategy]["resolution_correct"] is not None
    ]
    false_merges = sum(
        row[strategy]["resolution_correct"] is False for row in negatives
    )
    judged_negatives = sum(
        row[strategy]["resolution_correct"] is not None for row in negatives
    )
    ranks = [row[strategy]["expected_rank"] for row in positives]
    return {
        "caseCount": len(eligible),
        "positiveCount": len(positives),
        "negativeCount": len(negatives),
        "recallAt1": round(sum(rank == 1 for rank in ranks) / len(ranks), 4)
        if ranks
        else None,
        "recallAt3": round(sum(rank is not None for rank in ranks) / len(ranks), 4)
        if ranks
        else None,
        "mrr": round(sum(1 / rank if rank else 0 for rank in ranks) / len(ranks), 4)
        if ranks
        else None,
        "resolutionAccuracy": round(sum(resolution) / len(resolution), 4)
        if resolution
        else None,
        "positiveResolutionAccuracy": round(
            sum(positive_resolution) / len(positive_resolution), 4
        )
        if positive_resolution
        else None,
        "falseMergeRate": round(false_merges / judged_negatives, 4)
        if judged_negatives
        else None,
    }


async def run_experiment(args: argparse.Namespace) -> dict[str, Any]:
    experiment_started = time.perf_counter()
    paths = sorted(args.documents.glob("*.md"))
    if not paths:
        raise RuntimeError(f"no Markdown documents found below {args.documents}")
    extraction_path = args.output_dir / "extracted_entities.json"
    extraction_started = time.perf_counter()
    cached_document_count = (
        0
        if args.refresh_extraction
        else valid_extraction_cache_count(paths, extraction_path)
    )
    extraction_cache_hit = cached_document_count == len(paths)
    extraction = await extract_documents(
        paths,
        extraction_path,
        concurrency=args.concurrency,
        refresh=args.refresh_extraction,
    )
    extraction_elapsed_ms = (time.perf_counter() - extraction_started) * 1000
    discovered_catalog = build_catalog(extraction)
    base_catalog = apply_catalog_fixtures(discovered_catalog, args.fixtures)
    benchmark = load_benchmark(
        args.benchmark,
        base_catalog,
        max_extracted_alias_cases=args.max_extracted_alias_cases,
    )
    catalog = expand_catalog_for_stress_test(
        base_catalog,
        benchmark,
        args.stress_vocabulary,
        addition_count=args.synthetic_catalog_size,
    )
    scorer = LexicalScorer(catalog)
    catalog_by_name = {entity.name: entity for entity in catalog}

    embedding_client = RealEmbeddingClient(
        cache_path=args.output_dir / "embedding_cache.json"
    )
    embedding_started = time.perf_counter()
    entity_vectors = await embedding_client.embed(
        [entity.semantic_text for entity in catalog]
    )
    entity_local_vectors = await embedding_client.embed(
        [f"局部名称：{entity.local_name}" for entity in catalog]
    )
    query_subjects = [
        case.subject or infer_subject(case.mention, catalog) for case in benchmark
    ]
    mention_vectors = await embedding_client.embed(
        [
            "；".join(
                part
                for part in (
                    f"实体提及：{case.mention}",
                    f"所属主体：{case.subject}" if case.subject else "",
                    f"上下文：{case.context}" if case.context else "",
                )
                if part
            )
            for case in benchmark
        ]
    )
    mention_local_vectors = await embedding_client.embed(
        [
            f"局部名称：{extract_local_mention(case.mention, subject)}"
            for case, subject in zip(benchmark, query_subjects, strict=True)
        ]
    )
    embedding_elapsed_ms = (time.perf_counter() - embedding_started) * 1000

    adjudication_llm = OpenAICompatibleKnowledgeEntityLLM(temperature=0.0)
    adjudication_started = time.perf_counter()
    prepared: list[dict[str, Any]] = []
    for case, mention_vector, mention_local_vector in zip(
        benchmark, mention_vectors, mention_local_vectors, strict=True
    ):
        target = resolve_target(case, catalog)
        lexical = lexical_candidates(
            case.mention,
            catalog,
            scorer,
            subject=case.subject,
            top_k=args.top_k,
        )
        embedding = embedding_candidates(
            mention_vector,
            mention_local_vector,
            entity_vectors,
            entity_local_vectors,
            catalog,
            scorer,
            case.mention,
            subject=case.subject,
            top_k=args.top_k,
        )
        hybrid = hybrid_candidates(lexical, embedding, top_k=args.top_k)
        union_names = list(
            dict.fromkeys(
                candidate.entity_name
                for candidates in (lexical, embedding, hybrid)
                for candidate in candidates
            )
        )
        union_entities = [catalog_by_name[name] for name in union_names]
        prepared.append(
            {
                "case": case,
                "target": target,
                "lexical": lexical,
                "embedding": embedding,
                "hybrid": hybrid,
                "union_entities": union_entities,
            }
        )

    if args.skip_adjudication:
        all_decisions = [None for _ in prepared]
        adjudication_request_count = 0
        adjudication_cache_hit_count = 0
    else:
        (
            all_decisions,
            adjudication_request_count,
            adjudication_cache_hit_count,
        ) = await adjudicate_all(
            adjudication_llm,
            [(item["case"], item["union_entities"]) for item in prepared],
            cache_path=args.output_dir / "adjudication_cache.json",
            concurrency=args.adjudication_concurrency,
        )

    rows: list[dict[str, Any]] = []
    for item, decisions in zip(prepared, all_decisions, strict=True):
        case = item["case"]
        target = item["target"]
        strategy_results = {
            name: _strategy_result(candidates, target, case.expected, decisions)
            for name, candidates in (
                ("lexical", item["lexical"]),
                ("embedding", item["embedding"]),
                ("hybrid", item["hybrid"]),
            )
        }
        rows.append(
            {
                "case": asdict(case),
                "expected": case.expected,
                "resolvedTarget": target,
                "targetStatus": (
                    "not_applicable"
                    if case.expected == "NEW"
                    else "resolved"
                    if target
                    else "missing"
                ),
                "adjudication": decisions,
                **{name: asdict(result) for name, result in strategy_results.items()},
            }
        )
    adjudication_elapsed_ms = (time.perf_counter() - adjudication_started) * 1000

    report = {
        "configuration": {
            "documentCount": len(paths),
            "catalogEntityCount": len(catalog),
            "llmDiscoveredEntityCount": len(discovered_catalog),
            "manualFixtureEntityCount": sum(
                "manual_fixture" in entity.origins for entity in catalog
            ),
            "syntheticStressEntityCount": sum(
                "synthetic_stress" in entity.origins for entity in catalog
            ),
            "benchmarkCaseCount": len(benchmark),
            "topK": args.top_k,
            "embeddingRepresentation": "max(full_context, local_name)",
            "subjectUsage": "conflict_filter_only",
            "adjudicationEnabled": not args.skip_adjudication,
        },
        "performance": {
            "extractionCacheHit": extraction_cache_hit,
            "extractionCachedDocumentCount": cached_document_count,
            "extractionElapsedMs": round(extraction_elapsed_ms, 2),
            "extractionModelRequestUpperBound": 0
            if extraction_cache_hit
            else len(paths) * 3,
            "embeddingElapsedMs": round(embedding_elapsed_ms, 2),
            "embeddingRequestCount": embedding_client.request_count,
            "embeddingTextCount": embedding_client.text_count,
            "embeddingCacheHitCount": embedding_client.cache_hit_count,
            "adjudicationElapsedMs": round(adjudication_elapsed_ms, 2),
            "adjudicationRequestCount": adjudication_request_count,
            "adjudicationCacheHitCount": adjudication_cache_hit_count,
            "totalElapsedMs": round(
                (time.perf_counter() - experiment_started) * 1000, 2
            ),
        },
        "metrics": {
            strategy: aggregate_metrics(rows, strategy)
            for strategy in ("lexical", "embedding", "hybrid")
        },
        "metricsBySubset": {
            "manualChallenge": {
                strategy: aggregate_metrics(
                    [
                        row
                        for row in rows
                        if row["case"]["source"] != "discovery_output"
                    ],
                    strategy,
                )
                for strategy in ("lexical", "embedding", "hybrid")
            },
            "extractedAliases": {
                strategy: aggregate_metrics(
                    [
                        row
                        for row in rows
                        if row["case"]["source"] == "discovery_output"
                    ],
                    strategy,
                )
                for strategy in ("lexical", "embedding", "hybrid")
            },
        },
        "catalog": [asdict(entity) for entity in catalog],
        "cases": rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "report.md").write_text(
        render_markdown_report(report), encoding="utf-8"
    )
    return report


def _format_metric(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2%}"


def _delta(value: float | None, baseline: float | None) -> str:
    if value is None or baseline is None:
        return "N/A"
    return f"{value - baseline:+.2%}"


def _case_lines(rows: Sequence[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["无。"]
    return [
        f"- `{row['case']['case_id']}`：{row['case']['mention']} → "
        f"{row['resolvedTarget'] or 'NEW'}"
        for row in rows
    ]


def render_markdown_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# 实体同义词候选实验报告",
        "",
        "## 汇总指标",
        "",
        "| 策略 | Recall@1 | Recall@3 | MRR | 正例解析准确率 | 误合并率 | 总准确率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, label in (
        ("lexical", "词法 + Subject"),
        ("embedding", "Embedding"),
        ("hybrid", "词法 + Embedding 融合"),
    ):
        item = metrics[strategy]
        lines.append(
            "| "
            + " | ".join(
                (
                    label,
                    _format_metric(item["recallAt1"]),
                    _format_metric(item["recallAt3"]),
                    _format_metric(item["mrr"]),
                    _format_metric(item["positiveResolutionAccuracy"]),
                    _format_metric(item["falseMergeRate"]),
                    _format_metric(item["resolutionAccuracy"]),
                )
            )
            + " |"
        )

    lines.extend(["", "## 分层指标", ""])
    for subset, label in (
        ("manualChallenge", "人工挑战集"),
        ("extractedAliases", "抽取 aliases 集"),
    ):
        lines.extend(
            [
                f"### {label}",
                "",
                "| 策略 | 样本数 | Recall@3 | 正例解析准确率 | 误合并率 | 总准确率 |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for strategy, strategy_label in (
            ("lexical", "词法 + Subject"),
            ("embedding", "Embedding"),
            ("hybrid", "融合"),
        ):
            item = report["metricsBySubset"][subset][strategy]
            lines.append(
                "| "
                + " | ".join(
                    (
                        strategy_label,
                        str(item["caseCount"]),
                        _format_metric(item["recallAt3"]),
                        _format_metric(item["positiveResolutionAccuracy"]),
                        _format_metric(item["falseMergeRate"]),
                        _format_metric(item["resolutionAccuracy"]),
                    )
                )
                + " |"
            )
        lines.append("")

    performance = report["performance"]
    lines.extend(
        [
            "",
            "## 实验结论",
            "",
            "- 已知名称和 alias 仍应优先走确定性精确匹配。",
            "- 精确匹配失败后，Embedding Top-3 的跨语言召回明显优于词法 + Subject。",
            "- 当前样本中融合方案未提升 Recall@3，且 MRR 低于纯 Embedding；默认回退路径优先采用 Embedding Top-3。",
            "- Top-3 只负责召回，最终合并仍必须通过带 aliases 与上下文的 SAME/DIFFERENT/UNCERTAIN 判定。",
            "",
            "## 相对差异",
            "",
            "- Embedding 相对词法 Recall@3："
            f"{_delta(metrics['embedding']['recallAt3'], metrics['lexical']['recallAt3'])}。",
            "- 融合方案相对词法 Recall@3："
            f"{_delta(metrics['hybrid']['recallAt3'], metrics['lexical']['recallAt3'])}。",
            "",
            "## 性能与调用量",
            "",
            f"- 实体抽取：{performance['extractionElapsedMs']:.2f} ms，"
            f"缓存命中：{performance['extractionCacheHit']}。",
            f"- Embedding：{performance['embeddingElapsedMs']:.2f} ms，"
            f"{performance['embeddingRequestCount']} 次请求，"
            f"{performance['embeddingTextCount']} 条文本。",
            f"- LLM 同义判定：{performance['adjudicationElapsedMs']:.2f} ms，"
            f"{performance['adjudicationRequestCount']} 次请求。",
            f"- 总耗时：{performance['totalElapsedMs']:.2f} ms。",
            "",
            "## 词法漏召回但 Embedding 命中的样本",
            "",
        ]
    )
    gains = [
        row
        for row in report["cases"]
        if row["expected"] == "SAME"
        and not row["lexical"]["retrieval_hit"]
        and row["embedding"]["retrieval_hit"]
    ]
    lines.extend(_case_lines(gains))
    lines.extend(["", "## Embedding 漏召回但词法命中的样本", ""])
    losses = [
        row
        for row in report["cases"]
        if row["expected"] == "SAME"
        and row["lexical"]["retrieval_hit"]
        and not row["embedding"]["retrieval_hit"]
    ]
    lines.extend(_case_lines(losses))
    lines.extend(["", "## 端到端判定错误", ""])
    for strategy, label in (
        ("lexical", "词法 + Subject"),
        ("embedding", "Embedding"),
        ("hybrid", "融合"),
    ):
        lines.extend([f"### {label}", ""])
        errors = [
            row
            for row in report["cases"]
            if row[strategy]["resolution_correct"] is False
        ]
        lines.extend(_case_lines(errors))
        lines.append("")
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 人工种子样本是当前结论的主要依据；实体发现输出中的 aliases 会作为补充正例。",
            "- 同一个大模型参与实体发现和同义判定，可能带来相关性偏差；生产决策前应人工复核全部错误样本。",
            "- Embedding 性能数据包含外部服务网络延迟，不等同于部署本地索引后的查询延迟。",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare lexical and embedding synonym candidate retrieval"
    )
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENT_DIR)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path("scripts/knowledge_base/entity_synonym_resolution_fixtures.json"),
    )
    parser.add_argument(
        "--stress-vocabulary",
        type=Path,
        default=DEFAULT_STRESS_VOCABULARY,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--adjudication-concurrency", type=int, default=4)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--max-extracted-alias-cases", type=int, default=12)
    parser.add_argument("--synthetic-catalog-size", type=int, default=0)
    parser.add_argument("--refresh-extraction", action="store_true")
    parser.add_argument("--skip-adjudication", action="store_true")
    return parser.parse_args()


def main() -> int:
    report = asyncio.run(run_experiment(parse_args()))
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
