"""Resolve stable Markdown reference tokens for user-facing output."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from by_qa.knowledge_common.markdown_reference import detect_reference_token_spans


@dataclass
class MarkdownReferenceResolver:
    """Replace stable reference tokens with current visible targets."""

    connection_factory: Callable[[], Any]
    reference_repository: Any

    async def resolve_texts(
        self,
        *,
        knowledge_base_id: int,
        texts: list[str],
    ) -> list[str]:
        """Resolve all stable reference tokens across a batch of texts."""
        spans_by_text: list[list[tuple[int, int, int]]] = []
        reference_ids: list[int] = []
        seen_ids: set[int] = set()
        for text in texts:
            spans = detect_reference_token_spans(text)
            spans_by_text.append(spans)
            for span in spans:
                reference_id = span[2]
                if reference_id not in seen_ids:
                    seen_ids.add(reference_id)
                    reference_ids.append(reference_id)

        if not reference_ids:
            return list(texts)

        connection = await self._build_connection()
        try:
            cursor = connection.cursor()
            rows = await self.reference_repository.list_by_reference_ids(
                cursor,
                reference_ids=reference_ids,
            )
        finally:
            await connection.close()

        replacements = {
            int(row["kid"]): self._replacement_for_row(row)
            for row in rows
            if row.get("knowledge_base_id") is not None
            and int(row["knowledge_base_id"]) == knowledge_base_id
        }

        resolved_texts: list[str] = []
        for text, spans in zip(texts, spans_by_text, strict=True):
            if not spans:
                resolved_texts.append(text)
                continue
            parts: list[str] = []
            last = 0
            for start, end, reference_id in spans:
                parts.append(text[last:start])
                parts.append(replacements.get(reference_id, ""))
                last = end
            parts.append(text[last:])
            resolved_texts.append("".join(parts))
        return resolved_texts

    async def _build_connection(self) -> Any:
        connection = self.connection_factory()
        if inspect.isawaitable(connection):
            return await connection
        return connection

    def _replacement_for_row(self, row: dict[str, Any]) -> str:
        original_target = str(row.get("original_target") or "")
        if row.get("status") != "resolved":
            return original_target

        target_virtual_path = row.get("target_virtual_path")
        if not target_virtual_path or row.get("target_is_deleted") is True:
            return original_target

        return f"{target_virtual_path}{row.get('target_suffix') or ''}"
