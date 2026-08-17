# src/by_qa/knowledge_base/services/markdown_reference_rewriter.py
"""Rewrite markdown image/link references to stable database-backed tokens."""

from __future__ import annotations

import hashlib
import re
import uuid
from bisect import bisect_right
from typing import Any
from urllib.parse import unquote

from by_qa.core import logger
from by_qa.knowledge_common.kb_path_utils import normalize_kb_path
from by_qa.knowledge_common.markdown_reference import (
    URL_SCHEME_RE,
    detect_reference_spans,
    detect_reference_token_spans,
    split_target,
)

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


class MarkdownReferenceRewriter:
    MAX_REFERENCES = 1024

    async def rewrite(
        self,
        text: str,
        *,
        source_dir: str | None = None,
        knowledge_base_id: int | None = None,
        source_fs_entry_id: int | None = None,
        cursor: Any | None = None,
        reference_repository: Any | None = None,
        fs_entry_repository: Any | None = None,
        producer_run_id: str | None = None,
    ) -> str:
        if source_dir is None:
            raise TypeError("transactional rewrite requires source_dir")
        if (
            knowledge_base_id is None
            or source_fs_entry_id is None
            or cursor is None
            or reference_repository is None
            or fs_entry_repository is None
        ):
            raise TypeError("transactional rewrite requires repository and cursor args")
        normalized_run_id = producer_run_id or uuid.uuid4().hex
        try:
            return await self._rewrite_transactional(
                text,
                source_dir=source_dir,
                knowledge_base_id=knowledge_base_id,
                source_fs_entry_id=source_fs_entry_id,
                cursor=cursor,
                reference_repository=reference_repository,
                fs_entry_repository=fs_entry_repository,
                producer_run_id=normalized_run_id,
            )
        except Exception:
            logger.exception(
                "markdown reference rewrite failed: kb_id=%s source_id=%s producer_run_id=%s",
                knowledge_base_id,
                source_fs_entry_id,
                normalized_run_id,
            )
            raise

    async def materialize_existing_tokens(
        self,
        text: str,
        *,
        cursor: Any,
        reference_repository: Any,
    ) -> str:
        """Restore stable tokens to current paths before their assertions are deleted."""

        token_spans = detect_reference_token_spans(text)
        if not token_spans:
            return text
        rows = await reference_repository.list_by_reference_ids(
            cursor,
            reference_ids=sorted({reference_id for _, _, reference_id in token_spans}),
        )
        locator_by_id = {
            int(self._row_value(row, "kid")): self._materialized_locator(row)
            for row in rows
            if self._row_value(row, "kid") is not None
        }
        unresolved_ids = {
            reference_id
            for _, _, reference_id in token_spans
            if not locator_by_id.get(reference_id)
        }
        if unresolved_ids:
            logger.warning(
                "markdown reference token materialization failed: token_count=%s missing_count=%s",
                len(token_spans),
                len(unresolved_ids),
            )
            missing = ", ".join(str(item) for item in sorted(unresolved_ids))
            raise ValueError(f"cannot materialize reference tokens: {missing}")
        replacements = [
            (start, end, locator_by_id[reference_id])
            for start, end, reference_id in token_spans
            if locator_by_id.get(reference_id)
        ]
        materialized = self._apply_replacements(text, replacements)
        logger.debug(
            "markdown reference tokens materialized: token_count=%s assertion_count=%s",
            len(token_spans),
            len(rows),
        )
        return materialized

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
        producer_run_id: str,
    ) -> str:
        spans = detect_reference_spans(text)
        if not spans:
            logger.debug(
                "markdown references rewritten: kb_id=%s source_id=%s producer_run_id=%s parsed_count=0 persisted_count=0 resolved_count=0 pending_count=0 skipped_count=0",
                knowledge_base_id,
                source_fs_entry_id,
                producer_run_id,
            )
            return text
        if len(spans) > self.MAX_REFERENCES:
            logger.warning(
                "markdown reference count exceeds cap, skipping rewrite: kb_id=%s source_id=%s producer_run_id=%s parsed_count=%s cap=%s",
                knowledge_base_id,
                source_fs_entry_id,
                producer_run_id,
                len(spans),
                self.MAX_REFERENCES,
            )
            return text

        source_contexts = self._source_contexts(text)
        replacements: list[tuple[int, int, str]] = []
        resolved_count = 0
        pending_count = 0
        skipped_count = 0
        for start, end, alt, target, is_image in spans:
            t = target.strip()
            if self._is_ineligible_target(t):
                skipped_count += 1
                continue
            path_part, suffix = split_target(t)
            decoded = unquote(path_part)
            resolved = normalize_kb_path(source_dir, decoded)
            if resolved is None or resolved == "/":
                skipped_count += 1
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
                    skipped_count += 1
                    continue

            start_line, heading_path = self._context_at_offset(
                start, source_contexts=source_contexts
            )
            end_line, _ = self._context_at_offset(
                max(start, end - 1), source_contexts=source_contexts
            )
            evidence_fingerprint = hashlib.sha256(
                f"{source_fs_entry_id}:{start}:{end}:{text[start:end]}".encode("utf-8")
            ).hexdigest()
            target_file_id = (
                self._row_value(target_file, "kid") if target_file is not None else None
            )
            reference = await reference_repository.upsert_relation_assertion(
                cursor,
                knowledge_base_id=knowledge_base_id,
                source_fs_entry_id=source_fs_entry_id,
                target_fs_entry_id=target_file_id,
                relation_code="MENTIONS",
                original_target=target,
                target_path=None if target_file is not None else resolved,
                target_suffix=suffix,
                target_kind="FILE",
                status="resolved" if target_file is not None else "unresolved",
                discovered_by="MARKDOWN_PARSER",
                producer_run_id=producer_run_id,
                evidence_fingerprint=evidence_fingerprint,
                source_heading_path=heading_path,
                start_line=start_line,
                end_line=end_line,
                start_offset=start,
                end_offset=end,
                target_locator_type="KB_PATH",
                target_locator_value=resolved,
            )
            reference_id = self._row_value(reference, "kid")
            if reference_id is None:
                raise ValueError("reference insert did not return kid")
            if target_file is None:
                pending_count += 1
            else:
                resolved_count += 1

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

        rewritten = self._apply_replacements(text, replacements)
        logger.info(
            "markdown references rewritten: kb_id=%s source_id=%s producer_run_id=%s parsed_count=%s persisted_count=%s resolved_count=%s pending_count=%s skipped_count=%s",
            knowledge_base_id,
            source_fs_entry_id,
            producer_run_id,
            len(spans),
            len(replacements),
            resolved_count,
            pending_count,
            skipped_count,
        )
        return rewritten

    @staticmethod
    def _apply_replacements(text: str, replacements: list[tuple[int, int, str]]) -> str:
        if not replacements:
            return text
        out: list[str] = []
        last = 0
        for start, end, replacement in sorted(replacements, key=lambda item: item[0]):
            out.append(text[last:start])
            out.append(replacement)
            last = end
        out.append(text[last:])
        return "".join(out)

    @staticmethod
    def _materialized_locator(row: Any) -> str:
        suffix = str(MarkdownReferenceRewriter._row_value(row, "target_suffix") or "")
        current_path = MarkdownReferenceRewriter._row_value(row, "target_virtual_path")
        if current_path:
            return f"{current_path}{suffix}"
        target_path = MarkdownReferenceRewriter._row_value(row, "target_path")
        if target_path:
            return f"{target_path}{suffix}"
        locator_type = MarkdownReferenceRewriter._row_value(row, "target_locator_type")
        locator_value = MarkdownReferenceRewriter._row_value(
            row, "target_locator_value"
        )
        if locator_value:
            return (
                f"{locator_value}{suffix}"
                if locator_type == "KB_PATH"
                else str(locator_value)
            )
        return str(MarkdownReferenceRewriter._row_value(row, "original_target") or "")

    @staticmethod
    def _source_contexts(text: str) -> tuple[list[int], list[str | None]]:
        line_starts: list[int] = []
        heading_paths: list[str | None] = []
        headings: dict[int, str] = {}
        offset = 0
        for line in text.splitlines(keepends=True) or [""]:
            line_starts.append(offset)
            heading = _HEADING_RE.match(line.rstrip("\r\n"))
            if heading:
                level = len(heading.group(1))
                headings = {
                    key: value for key, value in headings.items() if key < level
                }
                headings[level] = heading.group(2).strip()
            path = " / ".join(headings[level] for level in sorted(headings))
            heading_paths.append(path or None)
            offset += len(line)
        return line_starts, heading_paths

    @staticmethod
    def _context_at_offset(
        offset: int, *, source_contexts: tuple[list[int], list[str | None]]
    ) -> tuple[int, str | None]:
        line_starts, heading_paths = source_contexts
        line_index = max(0, bisect_right(line_starts, offset) - 1)
        return line_index + 1, heading_paths[line_index]

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
