# src/by_qa/knowledge_base/services/markdown_reference_rewriter.py
"""Rewrite markdown image/link references to stable database-backed tokens."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
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

    def __init__(
        self,
        *,
        exists_check: Callable[[str, frozenset[str]], Awaitable[frozenset[str]]]
        | None = None,
    ) -> None:
        self._exists_check = exists_check

    async def rewrite(
        self,
        text: str,
        current_dir: str | None = None,
        kb_code: str | None = None,
        *,
        source_dir: str | None = None,
        knowledge_base_id: int | None = None,
        source_fs_entry_id: int | None = None,
        cursor: Any | None = None,
        reference_repository: Any | None = None,
        fs_entry_repository: Any | None = None,
    ) -> str:
        if source_dir is None:
            return await self._rewrite_legacy(
                text,
                current_dir=current_dir,
                kb_code=kb_code,
            )
        if (
            knowledge_base_id is None
            or source_fs_entry_id is None
            or cursor is None
            or reference_repository is None
            or fs_entry_repository is None
        ):
            raise TypeError("transactional rewrite requires repository and cursor args")
        return await self._rewrite_transactional(
            text,
            source_dir=source_dir,
            knowledge_base_id=knowledge_base_id,
            source_fs_entry_id=source_fs_entry_id,
            cursor=cursor,
            reference_repository=reference_repository,
            fs_entry_repository=fs_entry_repository,
        )

    async def _rewrite_transactional(
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
            if self._is_ineligible_target(t):
                continue
            path_part, suffix = split_target(t)
            decoded = unquote(path_part)
            resolved = normalize_kb_path(source_dir, decoded)
            if resolved is None or resolved == "/":
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

    async def _rewrite_legacy(
        self,
        text: str,
        *,
        current_dir: str | None,
        kb_code: str | None,
    ) -> str:
        if self._exists_check is None:
            raise TypeError("legacy rewrite requires exists_check")
        if current_dir is None or kb_code is None:
            raise TypeError("legacy rewrite requires current_dir and kb_code")

        spans = detect_reference_spans(text)
        if not spans:
            return text
        if len(spans) > self.MAX_REFERENCES:
            logger.warning(
                "markdown reference count exceeds cap, skipping rewrite: count=%s",
                len(spans),
            )
            return text

        decisions: list[tuple[int, int, str, str]] = []
        targets_to_check: set[str] = set()
        for start, end, alt, target, is_image in spans:
            t = target.strip()
            if self._is_ineligible_target(t):
                continue
            path_part, suffix = split_target(t)
            decoded = unquote(path_part)
            resolved = normalize_kb_path(current_dir, decoded)
            if resolved is None:
                continue
            target_start, target_end = self._target_bounds(
                text=text,
                start=start,
                end=end,
                alt=alt,
                is_image=is_image,
            )
            decisions.append((target_start, target_end, resolved, suffix))
            targets_to_check.add(resolved)

        if not targets_to_check:
            return text

        try:
            existing = await self._exists_check(kb_code, frozenset(targets_to_check))
        except Exception as exc:
            logger.warning(
                "reference exists_check failed, leaving references unchanged: %s",
                exc,
            )
            return text

        replacements = [
            (start, end, resolved + suffix)
            for start, end, resolved, suffix in decisions
            if resolved in existing
        ]
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
    def _is_ineligible_target(target: str) -> bool:
        return (
            not target
            or target.startswith("#")
            or target.startswith("//")
            or URL_SCHEME_RE.match(target) is not None
        )

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
