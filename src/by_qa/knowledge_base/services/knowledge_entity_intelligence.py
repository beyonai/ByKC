"""Shared KnowledgeEntity normalization, matching, and LLM primitives.

This module deliberately contains no database, object-storage, HTTP-route, or task
orchestration code.  Persistence callers supply the vocabulary, document content,
evidence, and relation targets; all model calls are represented by an injectable
protocol.  The default client speaks the OpenAI-compatible chat-completions API
using the neutral model configuration provider from :mod:`by_qa.core`.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import deque
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import httpx

from by_qa.core import logger
from by_qa.core.model_config import (
    LLMModelProfile,
    ModelConfigProvider,
    load_model_config_provider,
)

DISCOVERY_CONTEXT_CHARS = 16_000
MAX_DISCOVERED_ENTITIES = 12
DEFAULT_EVIDENCE_CHARS = 24_000
DEFAULT_EVIDENCE_FRAGMENTS = 20
DEFAULT_FRAGMENT_CHARS = 2_000
ALLOWED_RELATION_CODES = frozenset({"MENTIONS", "PART_OF", "IS_A", "DEPENDS_ON"})

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_H1_RE = re.compile(r"^\s{0,3}#(?!#)\s+.*$")
_MARKDOWN_FENCE_RE = re.compile(
    r"\A\s*```(?:markdown|md)?\s*\n(?P<body>.*?)\n```\s*\Z",
    re.DOTALL | re.IGNORECASE,
)
_FRONT_MATTER_RE = re.compile(r"\A\s*---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}")
_SPACE_RE = re.compile(r"\s+")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]")


class KnowledgeEntityIntelligenceError(RuntimeError):
    """Base failure raised by the pure KnowledgeEntity intelligence layer."""


class KnowledgeEntityLLMError(KnowledgeEntityIntelligenceError):
    """The configured LLM endpoint failed or returned an invalid envelope."""


class KnowledgeEntityOutputError(KnowledgeEntityIntelligenceError):
    """The model did not produce a valid structured result after retries."""


class IdentityScope(StrEnum):
    """Lightweight KnowledgeEntity identity scope."""

    GLOBAL = "global"
    SUBJECT = "subject"


class RelationCode(StrEnum):
    """The semantic relation whitelist supported in KnowledgeEntity v1."""

    MENTIONS = "MENTIONS"
    PART_OF = "PART_OF"
    IS_A = "IS_A"
    DEPENDS_ON = "DEPENDS_ON"


def normalize_surface(value: str) -> str:
    """Normalize a vocabulary surface using Unicode NFKC and case folding.

    Internal whitespace is collapsed to one ASCII space.  This makes visually
    equivalent full-width forms and case variants share one vocabulary key while
    retaining punctuation, which remains semantically significant.
    """

    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return _SPACE_RE.sub(" ", normalized).strip()


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """Normalized document text with a best-effort original offset mapping."""

    text: str
    original_starts: tuple[int, ...]
    original_ends: tuple[int, ...]

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        """Map a non-empty normalized half-open span back to original offsets."""

        if start < 0 or end <= start or end > len(self.text):
            raise ValueError("normalized span must be non-empty and in range")
        return self.original_starts[start], self.original_ends[end - 1]


def normalize_text_with_offsets(value: str) -> NormalizedText:
    """Apply NFKC/casefold while retaining offsets for evidence snippets.

    Unicode normalization can expand one source code point (for example ``ß`` to
    ``ss``).  Each resulting code point maps back to that source code point.  Runs
    of whitespace are collapsed and map to the complete original whitespace run.
    """

    raw_chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for source_start, source_end in _normalization_clusters(value or ""):
        source_cluster = value[source_start:source_end]
        for normalized_char in unicodedata.normalize("NFKC", source_cluster).casefold():
            raw_chars.append(normalized_char)
            starts.append(source_start)
            ends.append(source_end)

    collapsed_chars: list[str] = []
    collapsed_starts: list[int] = []
    collapsed_ends: list[int] = []
    index = 0
    while index < len(raw_chars):
        if not raw_chars[index].isspace():
            collapsed_chars.append(raw_chars[index])
            collapsed_starts.append(starts[index])
            collapsed_ends.append(ends[index])
            index += 1
            continue
        run_start = index
        while index < len(raw_chars) and raw_chars[index].isspace():
            index += 1
        if collapsed_chars and index < len(raw_chars):
            collapsed_chars.append(" ")
            collapsed_starts.append(starts[run_start])
            collapsed_ends.append(ends[index - 1])
    return NormalizedText(
        text="".join(collapsed_chars),
        original_starts=tuple(collapsed_starts),
        original_ends=tuple(collapsed_ends),
    )


def _normalization_clusters(value: str) -> Iterable[tuple[int, int]]:
    """Yield normalization-safe source spans for offset bookkeeping.

    Canonical combining marks must be normalized with their starter; doing NFKC
    one code point at a time would make ``e + COMBINING ACUTE`` differ from ``é``.
    Hangul Jamo composition is the other common composition across characters and
    is kept in one span as well.
    """

    start = 0
    index = 1
    while index <= len(value):
        if index < len(value) and (
            unicodedata.combining(value[index])
            or _continues_hangul_cluster(value[start:index], value[index])
        ):
            index += 1
            continue
        if start < index:
            yield start, index
        start = index
        index += 1


def _continues_hangul_cluster(cluster: str, next_char: str) -> bool:
    if not cluster:
        return False
    next_code = ord(next_char)
    is_vowel = 0x1160 <= next_code <= 0x11A7 or 0xD7B0 <= next_code <= 0xD7C6
    is_trailing = 0x11A8 <= next_code <= 0x11FF or 0xD7CB <= next_code <= 0xD7FB
    first_code = ord(cluster[0])
    starts_with_leading = (
        0x1100 <= first_code <= 0x115F or 0xA960 <= first_code <= 0xA97C
    )
    if len(cluster) == 1 and starts_with_leading and is_vowel:
        return True
    if starts_with_leading and any(
        0x1160 <= ord(char) <= 0x11A7 or 0xD7B0 <= ord(char) <= 0xD7C6
        for char in cluster[1:]
    ):
        return is_trailing
    last_code = ord(cluster[-1])
    is_lv_syllable = 0xAC00 <= last_code <= 0xD7A3 and (last_code - 0xAC00) % 28 == 0
    return is_lv_syllable and is_trailing


@dataclass(frozen=True, slots=True)
class SurfacePosting:
    """One entity identity associated with a normalized vocabulary surface."""

    entity_file_id: int
    knowledge_base_id: int
    entity_name: str
    surface_type: str = "entityName"
    subject_file_id: int | None = None
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class SurfaceEntry:
    """A raw surface and its posting used to construct an AC snapshot."""

    surface: str
    posting: SurfacePosting


@dataclass(frozen=True, slots=True)
class SurfaceMatch:
    """One longest-priority AC hit mapped to original document offsets."""

    surface: str
    normalized_surface: str
    matched_text: str
    start: int
    end: int
    postings: tuple[SurfacePosting, ...]
    anchorable_postings: tuple[SurfacePosting, ...]

    @property
    def is_anchorable(self) -> bool:
        """Whether at least one posting belongs to the current knowledge base."""

        return bool(self.anchorable_postings)


@dataclass(slots=True)
class _ACNode:
    transitions: dict[str, int] = field(default_factory=dict)
    failure: int = 0
    outputs: list[str] = field(default_factory=list)


class AhoCorasickIndex:
    """Immutable multi-posting Aho-Corasick vocabulary snapshot.

    Construction builds the trie and failure links once.  :meth:`scan` traverses
    the normalized document once; it does not loop over vocabulary surfaces.
    """

    def __init__(self, entries: Iterable[SurfaceEntry], *, version: str = "") -> None:
        postings: dict[str, list[SurfacePosting]] = {}
        display_surfaces: dict[str, str] = {}
        for entry in entries:
            normalized = normalize_surface(entry.surface)
            if not normalized:
                continue
            display_surfaces.setdefault(normalized, entry.surface.strip())
            bucket = postings.setdefault(normalized, [])
            if entry.posting not in bucket:
                bucket.append(entry.posting)
        self.version = version
        self._postings = {key: tuple(value) for key, value in postings.items()}
        self._display_surfaces = display_surfaces
        self._nodes = [_ACNode()]
        for surface in self._postings:
            self._insert(surface)
        self._build_failure_links()

    @classmethod
    def from_mapping(
        cls,
        mapping: Mapping[str, Sequence[SurfacePosting]],
        *,
        version: str = "",
    ) -> AhoCorasickIndex:
        """Construct a snapshot from ``surface -> postings`` input."""

        return cls(
            (
                SurfaceEntry(surface=surface, posting=posting)
                for surface, surface_postings in mapping.items()
                for posting in surface_postings
            ),
            version=version,
        )

    def _insert(self, surface: str) -> None:
        state = 0
        for char in surface:
            next_state = self._nodes[state].transitions.get(char)
            if next_state is None:
                next_state = len(self._nodes)
                self._nodes[state].transitions[char] = next_state
                self._nodes.append(_ACNode())
            state = next_state
        self._nodes[state].outputs.append(surface)

    def _build_failure_links(self) -> None:
        queue: deque[int] = deque()
        for child in self._nodes[0].transitions.values():
            self._nodes[child].failure = 0
            queue.append(child)
        while queue:
            state = queue.popleft()
            for char, child in self._nodes[state].transitions.items():
                queue.append(child)
                failure = self._nodes[state].failure
                while failure and char not in self._nodes[failure].transitions:
                    failure = self._nodes[failure].failure
                self._nodes[child].failure = self._nodes[failure].transitions.get(
                    char, 0
                )
                inherited = self._nodes[self._nodes[child].failure].outputs
                if inherited:
                    self._nodes[child].outputs.extend(inherited)

    def scan(
        self,
        document: str,
        *,
        current_knowledge_base_id: int,
        subject_context_file_ids: Collection[int] = (),
    ) -> tuple[SurfaceMatch, ...]:
        """Scan once, enforcing boundaries and longest-overlap priority.

        All postings for the winning surface remain on one match.  The
        ``anchorable_postings`` field is restricted to the current knowledge base;
        system-wide postings from other knowledge bases are never silently bound.
        A subject entity's qualified canonical name can anchor directly, while its
        local alias requires that subject in ``subject_context_file_ids``.
        """

        normalized = normalize_text_with_offsets(document)
        candidates: list[tuple[int, int, str]] = []
        state = 0
        for index, char in enumerate(normalized.text):
            while state and char not in self._nodes[state].transitions:
                state = self._nodes[state].failure
            state = self._nodes[state].transitions.get(char, 0)
            for surface in self._nodes[state].outputs:
                start = index - len(surface) + 1
                end = index + 1
                if self._has_valid_word_boundaries(
                    normalized.text, start, end, surface
                ):
                    candidates.append((start, end, surface))

        selected = self._prefer_longest_non_overlapping(candidates)
        matches: list[SurfaceMatch] = []
        for start, end, surface in selected:
            original_start, original_end = normalized.original_span(start, end)
            surface_postings = self._postings[surface]
            matches.append(
                SurfaceMatch(
                    surface=self._display_surfaces[surface],
                    normalized_surface=surface,
                    matched_text=document[original_start:original_end],
                    start=original_start,
                    end=original_end,
                    postings=surface_postings,
                    anchorable_postings=tuple(
                        posting
                        for posting in surface_postings
                        if self._is_anchorable_posting(
                            posting,
                            surface=surface,
                            current_knowledge_base_id=current_knowledge_base_id,
                            subject_context_file_ids=subject_context_file_ids,
                        )
                    ),
                )
            )
        return tuple(matches)

    @staticmethod
    def _is_anchorable_posting(
        posting: SurfacePosting,
        *,
        surface: str,
        current_knowledge_base_id: int,
        subject_context_file_ids: Collection[int],
    ) -> bool:
        if posting.knowledge_base_id != current_knowledge_base_id:
            return False
        if posting.subject_file_id is None:
            return True
        if normalize_surface(posting.entity_name) == surface:
            return True
        return posting.subject_file_id in subject_context_file_ids

    @staticmethod
    def _has_valid_word_boundaries(
        text: str, start: int, end: int, surface: str
    ) -> bool:
        if surface and surface[0].isascii() and surface[0].isalnum():
            if start > 0 and _ASCII_WORD_RE.fullmatch(text[start - 1]):
                return False
        if surface and surface[-1].isascii() and surface[-1].isalnum():
            if end < len(text) and _ASCII_WORD_RE.fullmatch(text[end]):
                return False
        return True

    @staticmethod
    def _prefer_longest_non_overlapping(
        candidates: Iterable[tuple[int, int, str]],
    ) -> list[tuple[int, int, str]]:
        unique = set(candidates)
        prioritized = sorted(
            unique,
            key=lambda item: (-(item[1] - item[0]), item[0], item[1], item[2]),
        )
        selected: list[tuple[int, int, str]] = []
        for candidate in prioritized:
            start, end = candidate[0], candidate[1]
            if any(start < chosen[1] and end > chosen[0] for chosen in selected):
                continue
            selected.append(candidate)
        return sorted(selected, key=lambda item: (item[0], item[1]))


@dataclass(frozen=True, slots=True)
class DiscoveryDocumentContext:
    """A bounded long-document excerpt and its heading map."""

    excerpt: str
    heading_map: tuple[str, ...]
    truncated: bool


def build_discovery_context(
    markdown: str, *, max_chars: int = DISCOVERY_CONTEXT_CHARS
) -> DiscoveryDocumentContext:
    """Build the original discovery frame: leading body, heading map, and tail."""

    if max_chars < 256:
        raise ValueError("max_chars must be at least 256")
    normalized = (markdown or "").strip()
    heading_lines: list[str] = []
    for line_number, line in enumerate(normalized.splitlines(), start=1):
        heading = _HEADING_RE.match(line)
        if heading:
            heading_lines.append(
                f"L{line_number} {'#' * len(heading.group(1))} {heading.group(2).strip()}"
            )
    if len(normalized) <= max_chars:
        return DiscoveryDocumentContext(
            excerpt=normalized,
            heading_map=tuple(heading_lines),
            truncated=False,
        )

    map_budget = min(3_000, max_chars // 5)
    heading_text = "\n".join(heading_lines)[:map_budget].rstrip()
    separator = f"\n\n[DOCUMENT HEADING MAP]\n{heading_text}\n\n[DOCUMENT END]\n"
    remaining = max(max_chars - len(separator) - 1, 2)
    head_budget = max(remaining * 2 // 3, 1)
    tail_budget = max(remaining - head_budget, 1)
    excerpt = (
        normalized[:head_budget].rstrip()
        + separator
        + normalized[-tail_budget:].lstrip()
    )[:max_chars]
    return DiscoveryDocumentContext(
        excerpt=excerpt,
        heading_map=tuple(heading_lines),
        truncated=True,
    )


class KnowledgeEntityLLM(Protocol):
    """Minimal injectable LLM contract used by discovery and enrichment."""

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        json_mode: bool = False,
    ) -> str: ...


class OpenAICompatibleKnowledgeEntityLLM:
    """Small async OpenAI-compatible client without a dependency on ``qa``."""

    def __init__(
        self,
        *,
        provider: ModelConfigProvider | None = None,
        profile: str | LLMModelProfile = LLMModelProfile.STANDARD,
        temperature: float | None = None,
        timeout: float = 300.0,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._provider = provider or load_model_config_provider()
        self._profile = profile
        self._temperature = temperature
        self._timeout = timeout
        self._client_factory = client_factory or httpx.AsyncClient

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        json_mode: bool = False,
    ) -> str:
        config = await self._provider.get_config(self._profile)
        base_url = config.base_url.rstrip("/")
        if not base_url:
            raise KnowledgeEntityLLMError("LLM base_url is required")
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        payload: dict[str, Any] = {
            **config.extra_body,
            "model": config.model_name,
            "temperature": (
                self._temperature
                if self._temperature is not None
                else config.temperature
            ),
            "messages": [dict(message) for message in messages],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with self._client_factory(timeout=self._timeout) as client:
                logger.debug(
                    "knowledge entity llm request prompt: model=%s, "
                    "json_mode=%s, message_count=%d, messages=%s",
                    config.model_name,
                    json_mode,
                    len(payload["messages"]),
                    json.dumps(payload["messages"], ensure_ascii=False),
                )
                response = await client.post(
                    f"{base_url}/chat/completions", headers=headers, json=payload
                )
                response.raise_for_status()
                response_payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise KnowledgeEntityLLMError(
                f"OpenAI-compatible LLM request failed: {exc}"
            ) from exc
        try:
            content = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise KnowledgeEntityLLMError(
                "LLM response did not include choices[0].message.content"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise KnowledgeEntityLLMError("LLM response content must be non-empty text")
        return content


async def _complete_strict_json(
    llm: KnowledgeEntityLLM,
    messages: Sequence[Mapping[str, str]],
    *,
    expected_type: type[list[Any]] | type[dict[str, Any]],
    max_attempts: int,
    retry_backoff_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
    operation: str,
    log_context: Mapping[str, Any] | None = None,
) -> tuple[Any, int]:
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        if attempt > 1 and retry_backoff_seconds:
            await sleep(retry_backoff_seconds * (attempt - 1))
        retry_messages = list(messages)
        if last_error:
            type_name = "array" if expected_type is list else "object"
            retry_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Previous output was invalid ({last_error}). Return only one "
                        f"strict JSON {type_name}; no Markdown fences or explanation."
                    ),
                }
            )
        # OpenAI-compatible ``json_object`` mode requires an object at the top
        # level. Discovery intentionally returns an array, so rely on the strict
        # prompt and retry validator instead of sending a contradictory format.
        raw = await llm.complete(retry_messages, json_mode=expected_type is dict)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            last_error = f"JSON parse error: {exc}"
            logger.warning(
                "knowledge_entity_intelligence llm output invalid: operation=%s "
                "attempt=%s max_attempts=%s error_type=json_decode retry=%s%s",
                operation,
                attempt,
                max_attempts,
                attempt < max_attempts,
                _safe_log_context(log_context),
            )
            continue
        if not isinstance(parsed, expected_type):
            last_error = (
                f"expected {expected_type.__name__}, got {type(parsed).__name__}"
            )
            logger.warning(
                "knowledge_entity_intelligence llm output invalid: operation=%s "
                "attempt=%s max_attempts=%s error_type=unexpected_json_type "
                "retry=%s%s",
                operation,
                attempt,
                max_attempts,
                attempt < max_attempts,
                _safe_log_context(log_context),
            )
            continue
        if attempt > 1:
            logger.info(
                "knowledge_entity_intelligence llm retry recovered: operation=%s "
                "attempt=%s%s",
                operation,
                attempt,
                _safe_log_context(log_context),
            )
        return parsed, attempt
    logger.error(
        "knowledge_entity_intelligence llm retries exhausted: operation=%s "
        "attempts=%s%s",
        operation,
        max_attempts,
        _safe_log_context(log_context),
    )
    raise KnowledgeEntityOutputError(
        f"LLM output remained invalid after {max_attempts} attempts: {last_error}"
    )


def _safe_log_context(context: Mapping[str, Any] | None) -> str:
    """Render only the approved task correlation fields, never arbitrary payloads."""

    if not context:
        return ""
    fields = (
        "batch_id",
        "task_id",
        "kb_code",
        "source_file_id",
        "file_path",
        "task_type",
    )
    values = " ".join(f"{name}={context.get(name, '-')}" for name in fields)
    return f" {values}"


def _clean_name(value: str) -> str:
    return _SPACE_RE.sub(" ", value or "").strip(" \t\r\n-")


__all__ = [
    "ALLOWED_RELATION_CODES",
    "AhoCorasickIndex",
    "IdentityScope",
    "KnowledgeEntityIntelligenceError",
    "KnowledgeEntityLLM",
    "KnowledgeEntityLLMError",
    "KnowledgeEntityOutputError",
    "NormalizedText",
    "OpenAICompatibleKnowledgeEntityLLM",
    "RelationCode",
    "SurfaceEntry",
    "SurfaceMatch",
    "SurfacePosting",
    "normalize_surface",
    "normalize_text_with_offsets",
]
