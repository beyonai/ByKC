# src/by_qa/knowledge_base/services/markdown_reference_rewriter.py
"""Rewrite markdown image/link references to KB-absolute paths when the
resolved target exists in the knowledge base.

Pure over (text, current_dir, kb_code, exists_check): no DB access of its
own. Runs for every markdown upload (single file or zip entry) before the
bytes are stored.

The existence check is batched: `exists_check(kb_code, paths)` receives the
full set of resolved KB-absolute paths in the document and returns the
subset that exist, so a single DB connection can answer the whole document
(DoS hardening against markdowns with thousands of references).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from urllib.parse import unquote

from by_qa.knowledge_common.kb_path_utils import normalize_kb_path
from by_qa.knowledge_common.markdown_reference import (
    URL_SCHEME_RE,
    detect_reference_spans,
    split_target,
)

logger = logging.getLogger(__name__)


class MarkdownReferenceRewriter:
    MAX_REFERENCES = 1024

    def __init__(
        self,
        *,
        exists_check: Callable[[str, frozenset[str]], Awaitable[frozenset[str]]],
    ) -> None:
        self._exists_check = exists_check

    async def rewrite(self, text: str, current_dir: str, kb_code: str) -> str:
        spans = detect_reference_spans(text)
        if not spans:
            return text
        if len(spans) > self.MAX_REFERENCES:
            logger.warning(
                "markdown reference count exceeds cap, skipping rewrite: count=%s",
                len(spans),
            )
            return text

        # Per-span rewrite decision (minus the existence check). Each entry is
        # (start, end, stripped_target, suffix, resolved_or_None, alt, is_image).
        decisions: list[tuple[int, int, str, str, str | None, str, bool]] = []
        targets_to_check: set[str] = set()
        for start, end, alt, target, is_image in spans:
            t = target.strip()
            if not t or t.startswith("#") or URL_SCHEME_RE.match(t):
                decisions.append((start, end, t, "", None, alt, is_image))
                continue
            path_part, suffix = split_target(t)
            decoded = unquote(path_part)
            resolved = normalize_kb_path(current_dir, decoded)
            if resolved is None:
                decisions.append((start, end, t, suffix, None, alt, is_image))
                continue
            decisions.append((start, end, t, suffix, resolved, alt, is_image))
            targets_to_check.add(resolved)

        if targets_to_check:
            try:
                existing = await self._exists_check(
                    kb_code, frozenset(targets_to_check)
                )
            except Exception as exc:
                logger.warning(
                    "reference exists_check failed, leaving references unchanged: %s",
                    exc,
                )
                return text
        else:
            existing = frozenset()

        out: list[str] = []
        last = 0
        for start, end, original_target, suffix, resolved, alt, is_image in decisions:
            out.append(text[last:start])
            if resolved is not None and resolved in existing:
                out.append(self._emit(alt, resolved + suffix, is_image))
            else:
                out.append(self._emit(alt, original_target, is_image))
            last = end
        out.append(text[last:])
        return "".join(out)

    @staticmethod
    def _emit(alt: str, target: str, is_image: bool) -> str:
        return f"![{alt}]({target})" if is_image else f"[{alt}]({target})"
