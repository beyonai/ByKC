# src/by_qa/knowledge_base/services/markdown_reference_rewriter.py
"""Rewrite markdown image/link references to KB-absolute paths when the
resolved target exists in the knowledge base.

Pure over (text, current_dir, kb_code, exists_check): no DB access of its
own. Runs for every markdown upload (single file or zip entry) before the
bytes are stored.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from by_qa.knowledge_common.kb_path_utils import normalize_kb_path
from by_qa.knowledge_common.markdown_reference import (
    URL_SCHEME_RE,
    detect_reference_spans,
    split_target,
)


class MarkdownReferenceRewriter:
    def __init__(
        self,
        *,
        exists_check: Callable[[str, str], Awaitable[bool]],
    ) -> None:
        self._exists_check = exists_check

    async def rewrite(self, text: str, current_dir: str, kb_code: str) -> str:
        spans = detect_reference_spans(text)
        if not spans:
            return text
        out: list[str] = []
        last = 0
        for start, end, alt, target, is_image in spans:
            out.append(text[last:start])
            out.append(
                await self._maybe_rewrite(alt, target, current_dir, kb_code, is_image)
            )
            last = end
        out.append(text[last:])
        return "".join(out)

    async def _maybe_rewrite(
        self, alt: str, target: str, current_dir: str, kb_code: str, is_image: bool
    ) -> str:
        target = target.strip()
        if not target or target.startswith("#") or URL_SCHEME_RE.match(target):
            return self._emit(alt, target, is_image)
        path_part, suffix = split_target(target)
        resolved = normalize_kb_path(current_dir, path_part)
        if resolved is None:
            return self._emit(alt, target, is_image)
        try:
            exists = await self._exists_check(kb_code, resolved)
        except Exception:
            exists = False
        if not exists:
            return self._emit(alt, target, is_image)
        return self._emit(alt, resolved + suffix, is_image)

    @staticmethod
    def _emit(alt: str, target: str, is_image: bool) -> str:
        return f"![{alt}]({target})" if is_image else f"[{alt}]({target})"
