# src/by_qa/knowledge_base/services/markdown_reference_rewriter.py
"""Rewrite markdown image/link references to stable database-backed tokens."""

from __future__ import annotations

import logging
from typing import Any
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

    async def rewrite(
        self,
        text: str,
        *,
        source_dir: str,
        knowledge_base_id: int,
        source_fs_entry_id: int,
        cursor: Any,
        reference_repository: Any,
        fs_entry_repository: Any,
    ) -> str:
        spans = detect_reference_spans(text)
        if not spans:
            return text
        if len(spans) > self.MAX_REFERENCES:
            logger.warning(
                "markdown reference count exceeds cap, skipping rewrite: count=%s",
                len(spans),
            )
            return text

        replacements: list[tuple[int, int, str]] = []
        for start, end, alt, target, is_image in spans:
            t = target.strip()
            if not t or t.startswith("#") or URL_SCHEME_RE.match(t):
                continue
            path_part, suffix = split_target(t)
            decoded = unquote(path_part)
            resolved = normalize_kb_path(source_dir, decoded)
            if resolved is None:
                continue

            target_file = await fs_entry_repository.get_file_reference_target_by_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=resolved,
            )
            if target_file is None:
                target_directory = await fs_entry_repository.get_directory_by_path(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    full_path=resolved,
                )
                if target_directory is not None:
                    continue

            reference = await reference_repository.create_reference(
                cursor,
                knowledge_base_id=knowledge_base_id,
                source_fs_entry_id=source_fs_entry_id,
                target_fs_entry_id=self._row_value(target_file, "kid")
                if target_file is not None
                else None,
                original_target=target,
                target_path=None if target_file is not None else resolved,
                target_suffix=suffix,
                target_kind="FILE",
                status="resolved" if target_file is not None else "unresolved",
            )
            reference_id = self._row_value(reference, "kid")
            if reference_id is None:
                raise ValueError("reference insert did not return kid")

            target_start, target_end = self._target_bounds(
                text=text,
                start=start,
                end=end,
                alt=alt,
                is_image=is_image,
            )
            replacements.append(
                (target_start, target_end, f"byqa-ref://{reference_id}")
            )

        if not replacements:
            return text

        out: list[str] = []
        last = 0
        for start, end, replacement in replacements:
            out.append(text[last:start])
            out.append(replacement)
            last = end
        out.append(text[last:])
        return "".join(out)

    @staticmethod
    def _target_bounds(
        *, text: str, start: int, end: int, alt: str, is_image: bool
    ) -> tuple[int, int]:
        prefix = f"![{alt}](" if is_image else f"[{alt}]("
        target_start = start + len(prefix)
        target_end = end - 1
        if (
            target_start > target_end
            or not text.startswith(prefix, start)
            or text[target_end:end] != ")"
        ):
            span = text[start:end]
            opening_paren = span.rfind("(")
            if opening_paren == -1 or not span.endswith(")"):
                raise ValueError("invalid markdown reference span")
            target_start = start + opening_paren + 1
            target_end = end - 1
        return target_start, target_end

    @staticmethod
    def _row_value(row: Any, key: str) -> Any:
        if row is None:
            return None
        if isinstance(row, dict):
            return row.get(key)
        if hasattr(row, key):
            return getattr(row, key)
        try:
            return row[key]
        except (KeyError, TypeError):
            return None
