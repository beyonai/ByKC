# src/by_qa/knowledge_common/markdown_reference.py
"""Shared markdown image/link reference detection.

Used by both the import-time reference rewriter (knowledge_base) and the
build-time chunker (knowledge_build) so the two stay consistent without
knowledge_base importing knowledge_build.
"""

from __future__ import annotations

import re

IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
LINK_REF_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def detect_reference_spans(text: str) -> list[tuple[int, int, str, str, bool]]:
    """Return non-overlapping (start, end, alt, target, is_image) spans.

    Image spans are matched first and take precedence: a link regex match
    that falls inside an image span is skipped so `![alt](url)` is not
    double-counted as `[alt](url)`.
    """
    spans: list[tuple[int, int, str, str, bool]] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(s: int, e: int) -> bool:
        return any(s < oe and e > os_ for os_, oe in occupied)

    for m in IMAGE_REF_RE.finditer(text):
        if not overlaps(m.start(), m.end()):
            spans.append((m.start(), m.end(), m.group(1), m.group(2), True))
            occupied.append((m.start(), m.end()))
    for m in LINK_REF_RE.finditer(text):
        if not overlaps(m.start(), m.end()):
            spans.append((m.start(), m.end(), m.group(1), m.group(2), False))
            occupied.append((m.start(), m.end()))
    spans.sort(key=lambda t: t[0])
    return spans


def split_target(target: str) -> tuple[str, str]:
    """Split a link target into (path_part, suffix).

    suffix begins at the first '?' or '#' and includes the delimiter; used
    so existence checks use only the path part while output can re-append
    the anchor/query after rewriting.
    """
    hash_idx = target.find("#")
    query_idx = target.find("?")
    candidates = [i for i in (hash_idx, query_idx) if i != -1]
    if not candidates:
        return target, ""
    cut = min(candidates)
    return target[:cut], target[cut:]
