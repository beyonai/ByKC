"""Chinese text segmentation for PostgreSQL full-text search.

PostgreSQL's ``simple`` text search config splits tokens by whitespace only.
Chinese text lacks spaces between words, so jieba is used to insert them
before ``to_tsvector('simple', ...)`` / ``plainto_tsquery('simple', ...)``.
"""

from __future__ import annotations

import unicodedata

import jieba

# Common Chinese single-character function words that carry no semantic
# value for full-text search.
_STOP_CHARS: frozenset[str] = frozenset(
    "的了是在和也都能而及与着或"
    "从对为以到等便被把让给将"
    "中向之其这那我他她它些个"
    "但所要去说看可知道"
    "不没无比很太更最很"
    "啊吧吗呢哦哈呀哇"
)


def segment_for_fts(text: str) -> str:
    """Segment Chinese text for PostgreSQL full-text search.

    Uses jieba's ``cut_for_search`` mode which produces finer-grained
    (n-gram) tokens than the default precise mode, improving recall for
    partial keyword matches.
    """
    if not text:
        return text
    # Normalize CJK Compatibility Ideographs (e.g. ⾼ → 高, ⼤ → 大) so
    # jieba can recognize affected words.
    normalized = unicodedata.normalize("NFKC", text)
    tokens = jieba.cut_for_search(normalized)
    return " ".join(token.strip() for token in tokens if _keep_token(token))


def _keep_token(token: str) -> bool:
    stripped = token.strip()
    if not stripped:
        return False
    if len(stripped) == 1:
        if stripped in _STOP_CHARS:
            return False
        cat = unicodedata.category(stripped[0])
        if cat.startswith("P") or cat.startswith("S"):
            return False
    # Drop tokens that are entirely punctuation / symbols / whitespace
    if all(
        unicodedata.category(ch).startswith(("P", "S", "Z", "C")) for ch in stripped
    ):
        return False
    return True
