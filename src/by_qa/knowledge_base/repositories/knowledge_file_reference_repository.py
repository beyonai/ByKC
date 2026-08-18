"""Persistence helpers for unified document relation assertions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from by_qa.core import logger


class KnowledgeFileReferenceRepository:
    """Repository for exact assertions and deduplicated logical relations."""

    MARKDOWN_PRODUCER = "MARKDOWN_PARSER"
    _RELATION_CODES = frozenset({"MENTIONS", "PART_OF", "IS_A", "DEPENDS_ON"})
    _TARGET_LOCATOR_TYPES = frozenset({"FS_ENTRY_ID", "KB_PATH", "ENTITY_SURFACE"})

    async def upsert_relation_assertion(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        source_fs_entry_id: int,
        original_target: str,
        discovered_by: str,
        target_fs_entry_id: int | None = None,
        relation_code: str = "MENTIONS",
        target_path: str | None = None,
        target_suffix: str = "",
        target_kind: str = "FILE",
        status: str = "resolved",
        confidence: float | None = None,
        producer_run_id: str | None = None,
        evidence_fingerprint: str | None = None,
        source_heading_path: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        start_offset: int | None = None,
        end_offset: int | None = None,
        target_locator_type: str | None = None,
        target_locator_value: str | None = None,
        source_task_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Insert or refresh one exact producer-owned relation assertion."""
        normalized_relation = self._normalize_relation_code(relation_code)
        normalized_producer = self._normalize_producer(discovered_by)
        normalized_run_id = self._normalize_optional_text(producer_run_id)
        if not original_target:
            raise ValueError("original_target must not be empty")
        if target_fs_entry_id is not None and source_fs_entry_id == target_fs_entry_id:
            raise ValueError("relation assertion source and target must differ")
        if target_kind != "FILE":
            raise ValueError("target_kind must be FILE")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        self._validate_source_range(
            start_line=start_line,
            end_line=end_line,
            start_offset=start_offset,
            end_offset=end_offset,
        )
        locator_type, locator_value = self._normalize_target_locator(
            target_fs_entry_id=target_fs_entry_id,
            target_path=target_path,
            target_locator_type=target_locator_type,
            target_locator_value=target_locator_value,
        )
        self._validate_target_state(
            target_fs_entry_id=target_fs_entry_id,
            target_path=target_path,
            status=status,
        )
        fingerprint = evidence_fingerprint or self._build_evidence_fingerprint(
            original_target=original_target,
            target_suffix=target_suffix,
            source_heading_path=source_heading_path,
            start_line=start_line,
            end_line=end_line,
            start_offset=start_offset,
            end_offset=end_offset,
            target_locator_type=locator_type,
            target_locator_value=locator_value,
        )

        await cursor.execute(
            f"""
            INSERT INTO knowledge_file_reference (
                knowledge_base_id,
                source_fs_entry_id,
                target_fs_entry_id,
                original_target,
                target_path,
                target_suffix,
                target_kind,
                status,
                relation_code,
                confidence,
                discovered_by,
                producer_run_id,
                evidence_fingerprint,
                source_heading_path,
                start_line,
                end_line,
                start_offset,
                end_offset,
                target_locator_type,
                target_locator_value,
                source_task_id,
                last_resolved_at,
                created_at,
                updated_at
            )
            SELECT
                %(knowledge_base_id)s,
                source.kid,
                target.kid,
                %(original_target)s,
                %(target_path)s,
                %(target_suffix)s,
                %(target_kind)s,
                %(status)s,
                %(relation_code)s,
                %(confidence)s,
                %(discovered_by)s,
                %(producer_run_id)s,
                %(evidence_fingerprint)s,
                %(source_heading_path)s,
                %(start_line)s,
                %(end_line)s,
                %(start_offset)s,
                %(end_offset)s,
                %(target_locator_type)s,
                %(target_locator_value)s,
                %(source_task_id)s,
                CASE WHEN %(status)s = 'resolved' THEN NOW() ELSE NULL END,
                NOW(),
                NOW()
            FROM knowledge_fs_entry source
            LEFT JOIN knowledge_fs_entry target
              ON target.kid = %(target_fs_entry_id)s
             AND target.knowledge_base_id = %(knowledge_base_id)s
            WHERE source.kid = %(source_fs_entry_id)s
              AND source.knowledge_base_id = %(knowledge_base_id)s
              AND (
                  (%(target_fs_entry_id)s IS NULL AND target.kid IS NULL)
                  OR target.kid IS NOT NULL
              )
            ON DUPLICATE KEY UPDATE
                target_fs_entry_id = EXCLUDED.target_fs_entry_id,
                original_target = EXCLUDED.original_target,
                target_path = EXCLUDED.target_path,
                target_suffix = EXCLUDED.target_suffix,
                target_kind = EXCLUDED.target_kind,
                status = EXCLUDED.status,
                confidence = EXCLUDED.confidence,
                source_heading_path = EXCLUDED.source_heading_path,
                start_line = EXCLUDED.start_line,
                end_line = EXCLUDED.end_line,
                start_offset = EXCLUDED.start_offset,
                end_offset = EXCLUDED.end_offset,
                source_task_id = EXCLUDED.source_task_id,
                last_resolved_at = CASE
                    WHEN EXCLUDED.status = 'resolved' THEN NOW()
                    ELSE knowledge_file_reference.last_resolved_at
                END,
                updated_at = NOW()
            RETURNING {self._assertion_columns()}
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "source_fs_entry_id": source_fs_entry_id,
                "target_fs_entry_id": target_fs_entry_id,
                "original_target": original_target,
                "target_path": target_path,
                "target_suffix": target_suffix,
                "target_kind": target_kind,
                "status": status,
                "relation_code": normalized_relation,
                "confidence": confidence,
                "discovered_by": normalized_producer,
                "producer_run_id": normalized_run_id,
                "evidence_fingerprint": fingerprint,
                "source_heading_path": source_heading_path,
                "start_line": start_line,
                "end_line": end_line,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "target_locator_type": locator_type,
                "target_locator_value": locator_value,
                "source_task_id": source_task_id,
            },
        )
        row = await cursor.fetchone()
        if row is None:
            logger.warning(
                "relation assertion upsert returned no row: kb_id=%s source_id=%s target_id=%s relation=%s producer=%s producer_run_id=%s task_id=%s",
                knowledge_base_id,
                source_fs_entry_id,
                target_fs_entry_id,
                normalized_relation,
                normalized_producer,
                normalized_run_id,
                source_task_id,
            )
        else:
            logger.debug(
                "relation assertion persisted: assertion_id=%s kb_id=%s source_id=%s target_id=%s relation=%s producer=%s producer_run_id=%s task_id=%s status=%s",
                row.get("kid"),
                knowledge_base_id,
                source_fs_entry_id,
                target_fs_entry_id,
                normalized_relation,
                normalized_producer,
                normalized_run_id,
                source_task_id,
                status,
            )
        return row

    async def create_reference(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        source_fs_entry_id: int,
        target_fs_entry_id: int | None,
        original_target: str,
        target_path: str | None,
        target_suffix: str = "",
        target_kind: str = "FILE",
        status: str,
        discovered_by: str = MARKDOWN_PRODUCER,
        producer_run_id: str | None = None,
        evidence_fingerprint: str | None = None,
        source_heading_path: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        start_offset: int | None = None,
        end_offset: int | None = None,
        target_locator_type: str | None = None,
        target_locator_value: str | None = None,
    ) -> dict[str, Any] | None:
        """Compatibility adapter for a parsed Markdown MENTIONS assertion."""
        return await self.upsert_relation_assertion(
            cursor,
            knowledge_base_id=knowledge_base_id,
            source_fs_entry_id=source_fs_entry_id,
            target_fs_entry_id=target_fs_entry_id,
            relation_code="MENTIONS",
            original_target=original_target,
            target_path=target_path,
            target_suffix=target_suffix,
            target_kind=target_kind,
            status=status,
            discovered_by=discovered_by,
            producer_run_id=producer_run_id,
            evidence_fingerprint=evidence_fingerprint,
            source_heading_path=source_heading_path,
            start_line=start_line,
            end_line=end_line,
            start_offset=start_offset,
            end_offset=end_offset,
            target_locator_type=target_locator_type,
            target_locator_value=target_locator_value,
        )

    async def delete_outgoing_for_source_fs_entry_id(
        self,
        cursor: Any,
        *,
        source_fs_entry_id: int,
        knowledge_base_id: int | None = None,
        relation_code: str | Sequence[str] | None = None,
        discovered_by: str | Sequence[str] | None = None,
        producer_run_id: str | None = None,
        source_task_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Delete producer-owned outgoing assertions, never inbound assertions."""
        conditions = ["source_fs_entry_id = %(source_fs_entry_id)s"]
        params: dict[str, Any] = {"source_fs_entry_id": source_fs_entry_id}
        if knowledge_base_id is not None:
            conditions.append("knowledge_base_id = %(knowledge_base_id)s")
            params["knowledge_base_id"] = knowledge_base_id
        relation_sql, relation_params = self._relation_filter(
            relation_code, table_alias=None
        )
        if relation_sql:
            conditions.append(relation_sql.removeprefix("AND "))
            params.update(relation_params)
        producer_sql, producer_params = self._producer_filter(
            discovered_by, table_alias=None
        )
        if producer_sql:
            conditions.append(producer_sql.removeprefix("AND "))
            params.update(producer_params)
        if producer_run_id is not None:
            conditions.append("producer_run_id = %(producer_run_id)s")
            params["producer_run_id"] = producer_run_id
        if source_task_id is not None:
            conditions.append("source_task_id = %(source_task_id)s")
            params["source_task_id"] = source_task_id
        await cursor.execute(
            f"""
            DELETE FROM knowledge_file_reference
            WHERE {" AND ".join(conditions)}
            RETURNING kid, source_fs_entry_id, target_fs_entry_id,
                      relation_code, discovered_by, producer_run_id
            """,
            params,
        )
        rows = await self._fetchall(cursor)
        log = logger.info if rows else logger.debug
        log(
            "outgoing relation assertions deleted: kb_id=%s source_id=%s relations=%s producers=%s producer_run_id=%s task_id=%s count=%s",
            knowledge_base_id,
            source_fs_entry_id,
            relation_code,
            discovered_by,
            producer_run_id,
            source_task_id,
            len(rows),
        )
        return rows

    async def delete_for_source_fs_entry_id(
        self, cursor: Any, *, source_fs_entry_id: int
    ) -> None:
        """Compatibility adapter deleting Markdown-parser-owned outgoing assertions."""
        await self.delete_outgoing_for_source_fs_entry_id(
            cursor,
            source_fs_entry_id=source_fs_entry_id,
            discovered_by=self.MARKDOWN_PRODUCER,
        )

    async def list_assertions_by_source(
        self,
        cursor: Any,
        *,
        source_fs_entry_id: int,
        relation_code: str | Sequence[str] | None = None,
        discovered_by: str | Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List exact outgoing assertions with optional relation/producer filters."""
        relation_sql, relation_params = self._relation_filter(relation_code)
        producer_sql, producer_params = self._producer_filter(discovered_by)
        await cursor.execute(
            f"""
            {self._select_with_target()}
            WHERE kfr.source_fs_entry_id = %(source_fs_entry_id)s
              {relation_sql}
              {producer_sql}
            ORDER BY kfr.kid
            """,
            {
                "source_fs_entry_id": source_fs_entry_id,
                **relation_params,
                **producer_params,
            },
        )
        rows = await self._fetchall(cursor)
        logger.debug(
            "relation assertions listed by source: source_id=%s relations=%s producers=%s count=%s",
            source_fs_entry_id,
            relation_code,
            discovered_by,
            len(rows),
        )
        return rows

    async def list_by_source(
        self, cursor: Any, *, source_fs_entry_id: int
    ) -> list[dict[str, Any]]:
        """Compatibility view of physical Markdown references."""
        return await self.list_assertions_by_source(
            cursor,
            source_fs_entry_id=source_fs_entry_id,
            relation_code="MENTIONS",
            discovered_by=self.MARKDOWN_PRODUCER,
        )

    async def list_by_reference_ids(
        self, cursor: Any, *, reference_ids: list[int]
    ) -> list[dict[str, Any]]:
        """Resolve stable byqa-ref tokens directly by assertion id."""
        if not reference_ids:
            return []
        await cursor.execute(
            f"""
            {self._select_with_target()}
            WHERE kfr.kid = ANY(%(reference_ids)s)
            ORDER BY kfr.kid
            """,
            {"reference_ids": list(reference_ids)},
        )
        rows = await self._fetchall(cursor)
        logger.debug(
            "relation assertions loaded by token ids: requested_count=%s found_count=%s",
            len(reference_ids),
            len(rows),
        )
        return rows

    async def list_relations_by_source(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        source_fs_entry_id: int,
        relation_code: str | Sequence[str] | None = None,
        include_deleted_entries: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List deduplicated outgoing logical relations."""
        self._validate_pagination(limit=limit, offset=offset)
        relation_sql, relation_params = self._relation_filter(relation_code)
        deleted_filter = (
            ""
            if include_deleted_entries
            else "AND source.is_deleted = FALSE AND target.is_deleted = FALSE"
        )
        await cursor.execute(
            f"""
            {
                self._logical_relation_cte(
                    f'''kfr.knowledge_base_id = %(knowledge_base_id)s
                    AND kfr.source_fs_entry_id = %(source_fs_entry_id)s
                    {relation_sql} {deleted_filter}'''
                )
            }
            SELECT * FROM ranked_relations
            WHERE assertion_rank = 1
            ORDER BY kid
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "source_fs_entry_id": source_fs_entry_id,
                "limit": limit,
                "offset": offset,
                **relation_params,
            },
        )
        rows = await self._fetchall(cursor)
        logger.debug(
            "logical relations listed by source: kb_id=%s source_id=%s relations=%s include_deleted=%s count=%s offset=%s limit=%s",
            knowledge_base_id,
            source_fs_entry_id,
            relation_code,
            include_deleted_entries,
            len(rows),
            offset,
            limit,
        )
        return rows

    async def count_relations_by_source(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        source_fs_entry_id: int,
        relation_code: str | Sequence[str] | None = None,
        include_deleted_entries: bool = False,
    ) -> int:
        """Count deduplicated outgoing logical relations."""
        relation_sql, relation_params = self._relation_filter(relation_code)
        deleted_filter = (
            ""
            if include_deleted_entries
            else "AND source.is_deleted = FALSE AND target.is_deleted = FALSE"
        )
        await cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM (
                SELECT kfr.source_fs_entry_id, kfr.relation_code,
                       kfr.target_fs_entry_id
                FROM knowledge_file_reference kfr
                JOIN knowledge_fs_entry source ON source.kid = kfr.source_fs_entry_id
                JOIN knowledge_fs_entry target ON target.kid = kfr.target_fs_entry_id
                WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
                  AND kfr.source_fs_entry_id = %(source_fs_entry_id)s
                  {relation_sql}
                  {deleted_filter}
                GROUP BY kfr.source_fs_entry_id, kfr.relation_code,
                         kfr.target_fs_entry_id
            ) logical_relations
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "source_fs_entry_id": source_fs_entry_id,
                **relation_params,
            },
        )
        row = await cursor.fetchone()
        total = int(row["total"]) if row is not None else 0
        logger.debug(
            "logical relations counted by source: kb_id=%s source_id=%s relations=%s include_deleted=%s total=%s",
            knowledge_base_id,
            source_fs_entry_id,
            relation_code,
            include_deleted_entries,
            total,
        )
        return total

    async def list_relations_by_target(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_fs_entry_id: int,
        relation_code: str | Sequence[str] | None = None,
        include_deleted_entries: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List deduplicated incoming logical relations."""
        self._validate_pagination(limit=limit, offset=offset)
        relation_sql, relation_params = self._relation_filter(relation_code)
        deleted_filter = (
            ""
            if include_deleted_entries
            else "AND source.is_deleted = FALSE AND target.is_deleted = FALSE"
        )
        await cursor.execute(
            f"""
            {
                self._logical_relation_cte(
                    f'''kfr.knowledge_base_id = %(knowledge_base_id)s
                    AND kfr.target_fs_entry_id = %(target_fs_entry_id)s
                    {relation_sql} {deleted_filter}'''
                )
            }
            SELECT * FROM ranked_relations
            WHERE assertion_rank = 1
            ORDER BY kid
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "target_fs_entry_id": target_fs_entry_id,
                "limit": limit,
                "offset": offset,
                **relation_params,
            },
        )
        rows = await self._fetchall(cursor)
        logger.debug(
            "logical relations listed by target: kb_id=%s target_id=%s relations=%s include_deleted=%s count=%s offset=%s limit=%s",
            knowledge_base_id,
            target_fs_entry_id,
            relation_code,
            include_deleted_entries,
            len(rows),
            offset,
            limit,
        )
        return rows

    async def count_relations_by_target(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_fs_entry_id: int,
        relation_code: str | Sequence[str] | None = None,
        include_deleted_entries: bool = False,
    ) -> int:
        """Count deduplicated incoming logical relations."""
        relation_sql, relation_params = self._relation_filter(relation_code)
        deleted_filter = (
            ""
            if include_deleted_entries
            else "AND source.is_deleted = FALSE AND target.is_deleted = FALSE"
        )
        await cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM (
                SELECT kfr.source_fs_entry_id, kfr.relation_code,
                       kfr.target_fs_entry_id
                FROM knowledge_file_reference kfr
                JOIN knowledge_fs_entry source ON source.kid = kfr.source_fs_entry_id
                JOIN knowledge_fs_entry target ON target.kid = kfr.target_fs_entry_id
                WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
                  AND kfr.target_fs_entry_id = %(target_fs_entry_id)s
                  {relation_sql}
                  {deleted_filter}
                GROUP BY kfr.source_fs_entry_id, kfr.relation_code,
                         kfr.target_fs_entry_id
            ) logical_relations
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "target_fs_entry_id": target_fs_entry_id,
                **relation_params,
            },
        )
        row = await cursor.fetchone()
        total = int(row["total"]) if row is not None else 0
        logger.debug(
            "logical relations counted by target: kb_id=%s target_id=%s relations=%s include_deleted=%s total=%s",
            knowledge_base_id,
            target_fs_entry_id,
            relation_code,
            include_deleted_entries,
            total,
        )
        return total

    async def list_recent_assertions_by_target(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_fs_entry_id: int,
        relation_code: str | Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List physical incoming assertions by insertion time, newest first."""

        self._validate_pagination(limit=limit, offset=offset)
        relation_sql, relation_params = self._relation_filter(relation_code)
        await cursor.execute(
            f"""
            {self._select_with_source()}
            WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
              AND kfr.target_fs_entry_id = %(target_fs_entry_id)s
              AND kfr.status = 'resolved'
              AND source.is_deleted = FALSE
              {relation_sql}
            ORDER BY kfr.created_at DESC, kfr.kid DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "target_fs_entry_id": target_fs_entry_id,
                "limit": limit,
                "offset": offset,
                **relation_params,
            },
        )
        return await self._fetchall(cursor)

    async def resolve_pending_for_path(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_path: str,
        target_fs_entry_id: int,
    ) -> list[dict[str, Any]]:
        """Resolve assertions currently waiting on this knowledge-base path."""
        await cursor.execute(
            f"""
            UPDATE knowledge_file_reference
            SET target_fs_entry_id = %(target_fs_entry_id)s,
                target_path = NULL,
                status = 'resolved',
                last_resolved_at = NOW(),
                updated_at = NOW()
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND target_fs_entry_id IS NULL
              AND target_path = %(target_path)s
              AND status IN ('unresolved', 'broken')
            RETURNING {self._assertion_columns()}
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "target_path": target_path,
                "target_fs_entry_id": target_fs_entry_id,
            },
        )
        rows = await self._fetchall(cursor)
        log = logger.info if rows else logger.debug
        log(
            "pending relation assertions resolved: kb_id=%s target_id=%s count=%s",
            knowledge_base_id,
            target_fs_entry_id,
            len(rows),
        )
        return rows

    async def resolve_assertions_for_locator(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_locator_type: str,
        target_locator_value: str,
        target_fs_entry_id: int,
    ) -> list[dict[str, Any]]:
        """Rebind unresolved assertions through their stable recovery locator."""
        locator_type = target_locator_type.strip().upper()
        if locator_type not in self._TARGET_LOCATOR_TYPES:
            raise ValueError(
                "target_locator_type must be FS_ENTRY_ID, KB_PATH, or ENTITY_SURFACE"
            )
        if not target_locator_value.strip():
            raise ValueError("target_locator_value must not be empty")
        await cursor.execute(
            f"""
            UPDATE knowledge_file_reference assertion
            SET target_fs_entry_id = target.kid,
                target_path = NULL,
                status = 'resolved',
                last_resolved_at = NOW(),
                updated_at = NOW()
            FROM knowledge_fs_entry target
            WHERE assertion.knowledge_base_id = %(knowledge_base_id)s
              AND target.kid = %(target_fs_entry_id)s
              AND target.knowledge_base_id = %(knowledge_base_id)s
              AND target.is_deleted = FALSE
              AND assertion.target_locator_type = %(target_locator_type)s
              AND assertion.target_locator_value = %(target_locator_value)s
              AND assertion.status IN ('unresolved', 'broken')
            RETURNING {self._assertion_columns("assertion")}
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "target_locator_type": locator_type,
                "target_locator_value": target_locator_value,
                "target_fs_entry_id": target_fs_entry_id,
            },
        )
        rows = await self._fetchall(cursor)
        log = logger.info if rows else logger.debug
        log(
            "relation assertions rebound by locator: kb_id=%s target_id=%s locator_type=%s count=%s",
            knowledge_base_id,
            target_fs_entry_id,
            locator_type,
            len(rows),
        )
        return rows

    async def rebind_deleted_target_for_path(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_path: str,
        target_fs_entry_id: int,
    ) -> list[dict[str, Any]]:
        """Rebind assertions from a deleted target row to its live replacement."""
        await cursor.execute(
            f"""
            UPDATE knowledge_file_reference kfr
            SET target_fs_entry_id = %(target_fs_entry_id)s,
                target_path = NULL,
                status = 'resolved',
                last_resolved_at = NOW(),
                updated_at = NOW()
            FROM knowledge_fs_entry deleted_target
            WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
              AND deleted_target.kid = kfr.target_fs_entry_id
              AND kfr.target_fs_entry_id <> %(target_fs_entry_id)s
              AND kfr.status = 'resolved'
              AND deleted_target.is_deleted = TRUE
              AND deleted_target.virtual_path = %(target_path)s
            RETURNING {self._assertion_columns("kfr")}
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "target_path": target_path,
                "target_fs_entry_id": target_fs_entry_id,
            },
        )
        rows = await self._fetchall(cursor)
        log = logger.info if rows else logger.debug
        log(
            "relation assertions rebound from deleted target: kb_id=%s target_id=%s count=%s",
            knowledge_base_id,
            target_fs_entry_id,
            len(rows),
        )
        return rows

    async def mark_targets_deleted(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        targets: list[tuple[int, str]],
    ) -> list[dict[str, Any]]:
        """Break target bindings while retaining each stable recovery locator."""
        if not targets:
            return []
        values_sql: list[str] = []
        params: dict[str, Any] = {"knowledge_base_id": knowledge_base_id}
        for index, (target_fs_entry_id, target_path) in enumerate(targets):
            id_key = f"target_{index}_id"
            path_key = f"target_{index}_path"
            values_sql.append(f"(%({id_key})s::bigint, %({path_key})s::text)")
            params[id_key] = target_fs_entry_id
            params[path_key] = target_path
        await cursor.execute(
            f"""
            UPDATE knowledge_file_reference kfr
            SET target_fs_entry_id = NULL,
                target_path = deleted_targets.target_path,
                target_locator_value = CASE
                    WHEN kfr.target_locator_type = 'KB_PATH'
                        THEN deleted_targets.target_path
                    ELSE kfr.target_locator_value
                END,
                status = 'broken',
                updated_at = NOW()
            FROM (VALUES {", ".join(values_sql)})
                AS deleted_targets(target_fs_entry_id, target_path)
            WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
              AND kfr.target_fs_entry_id = deleted_targets.target_fs_entry_id
              AND kfr.status = 'resolved'
            RETURNING {self._assertion_columns("kfr")}
            """,
            params,
        )
        rows = await self._fetchall(cursor)
        log = logger.info if rows else logger.debug
        log(
            "relation assertion targets marked broken: kb_id=%s target_count=%s assertion_count=%s",
            knowledge_base_id,
            len(targets),
            len(rows),
        )
        return rows

    async def mark_target_restored(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_path: str,
        target_fs_entry_id: int,
    ) -> list[dict[str, Any]]:
        """Restore every broken assertion last bound to this path."""
        await cursor.execute(
            f"""
            UPDATE knowledge_file_reference
            SET target_fs_entry_id = %(target_fs_entry_id)s,
                target_path = NULL,
                status = 'resolved',
                last_resolved_at = NOW(),
                updated_at = NOW()
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND target_fs_entry_id IS NULL
              AND target_path = %(target_path)s
              AND status = 'broken'
            RETURNING {self._assertion_columns()}
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "target_path": target_path,
                "target_fs_entry_id": target_fs_entry_id,
            },
        )
        rows = await self._fetchall(cursor)
        log = logger.info if rows else logger.debug
        log(
            "broken relation assertions restored: kb_id=%s target_id=%s count=%s",
            knowledge_base_id,
            target_fs_entry_id,
            len(rows),
        )
        return rows

    async def list_assertions_by_target(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_fs_entry_id: int | None = None,
        target_path: str | None = None,
        include_deleted_sources: bool = False,
        relation_code: str | Sequence[str] | None = None,
        discovered_by: str | Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List exact incoming assertions by resolved id or pending path."""
        if (target_fs_entry_id is None) == (target_path is None):
            raise ValueError("provide exactly one of target_fs_entry_id or target_path")
        source_filter = (
            "" if include_deleted_sources else "AND source.is_deleted = FALSE"
        )
        relation_sql, relation_params = self._relation_filter(relation_code)
        producer_sql, producer_params = self._producer_filter(discovered_by)
        if target_fs_entry_id is not None:
            locator_filter = """
                kfr.target_fs_entry_id = %(target_fs_entry_id)s
                AND kfr.status = 'resolved'
            """
            params: dict[str, Any] = {
                "knowledge_base_id": knowledge_base_id,
                "target_fs_entry_id": target_fs_entry_id,
            }
        else:
            locator_filter = """
                kfr.target_fs_entry_id IS NULL
                AND kfr.target_path = %(target_path)s
                AND kfr.status IN ('unresolved', 'broken')
            """
            params = {
                "knowledge_base_id": knowledge_base_id,
                "target_path": target_path,
            }
        params.update(relation_params)
        params.update(producer_params)
        await cursor.execute(
            f"""
            {self._select_with_source()}
            WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
              AND {locator_filter}
              {source_filter}
              {relation_sql}
              {producer_sql}
            ORDER BY kfr.kid
            """,
            params,
        )
        return await self._fetchall(cursor)

    async def list_sources_by_target(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_fs_entry_id: int | None = None,
        target_path: str | None = None,
        include_deleted_sources: bool = False,
    ) -> list[dict[str, Any]]:
        """Compatibility view of physical inbound Markdown references."""
        return await self.list_assertions_by_target(
            cursor,
            knowledge_base_id=knowledge_base_id,
            target_fs_entry_id=target_fs_entry_id,
            target_path=target_path,
            include_deleted_sources=include_deleted_sources,
            relation_code="MENTIONS",
            discovered_by=self.MARKDOWN_PRODUCER,
        )

    def _logical_relation_cte(self, conditions: str) -> str:
        return f"""
            WITH ranked_relations AS (
                SELECT
                    {self._assertion_columns("kfr")},
                    source.virtual_path AS source_virtual_path,
                    source.is_deleted AS source_is_deleted,
                    target.virtual_path AS target_virtual_path,
                    target.is_deleted AS target_is_deleted,
                    COUNT(*) OVER (
                        PARTITION BY kfr.source_fs_entry_id,
                                     kfr.relation_code,
                                     kfr.target_fs_entry_id
                    ) AS assertion_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY kfr.source_fs_entry_id,
                                     kfr.relation_code,
                                     kfr.target_fs_entry_id
                        ORDER BY CASE
                                     WHEN kfr.start_line IS NOT NULL
                                       OR kfr.start_offset IS NOT NULL THEN 0
                                     ELSE 1
                                 END,
                                 kfr.confidence DESC NULLS LAST,
                                 kfr.updated_at DESC,
                                 kfr.kid DESC
                    ) AS assertion_rank
                FROM knowledge_file_reference kfr
                JOIN knowledge_fs_entry source ON source.kid = kfr.source_fs_entry_id
                JOIN knowledge_fs_entry target ON target.kid = kfr.target_fs_entry_id
                WHERE {conditions}
            )
        """

    def _select_with_target(self) -> str:
        return f"""
            SELECT
                {self._assertion_columns("kfr")},
                target.virtual_path AS target_virtual_path,
                target.is_deleted AS target_is_deleted
            FROM knowledge_file_reference kfr
            LEFT JOIN knowledge_fs_entry target ON target.kid = kfr.target_fs_entry_id
        """

    def _select_with_source(self) -> str:
        return f"""
            SELECT
                {self._assertion_columns("kfr")},
                source.virtual_path AS source_virtual_path,
                source.is_deleted AS source_is_deleted
            FROM knowledge_file_reference kfr
            JOIN knowledge_fs_entry source ON source.kid = kfr.source_fs_entry_id
        """

    def _assertion_columns(self, table_alias: str | None = None) -> str:
        prefix = f"{table_alias}." if table_alias else ""
        return ",\n                ".join(
            f"{prefix}{column}"
            for column in (
                "kid",
                "knowledge_base_id",
                "source_fs_entry_id",
                "target_fs_entry_id",
                "original_target",
                "target_path",
                "target_suffix",
                "target_kind",
                "status",
                "relation_code",
                "confidence",
                "discovered_by",
                "producer_run_id",
                "evidence_fingerprint",
                "source_heading_path",
                "start_line",
                "end_line",
                "start_offset",
                "end_offset",
                "target_locator_type",
                "target_locator_value",
                "source_task_id",
                "last_resolved_at",
                "created_at",
                "updated_at",
            )
        )

    def _relation_filter(
        self,
        relation_code: str | Sequence[str] | None,
        *,
        table_alias: str | None = "kfr",
    ) -> tuple[str, dict[str, Any]]:
        relation_codes = self._normalize_relation_codes(relation_code)
        if relation_codes is None:
            return "", {}
        prefix = f"{table_alias}." if table_alias else ""
        return f"AND {prefix}relation_code = ANY(%(relation_codes)s)", {
            "relation_codes": relation_codes
        }

    def _producer_filter(
        self,
        discovered_by: str | Sequence[str] | None,
        *,
        table_alias: str | None = "kfr",
    ) -> tuple[str, dict[str, Any]]:
        if discovered_by is None:
            return "", {}
        producers = (
            [discovered_by] if isinstance(discovered_by, str) else list(discovered_by)
        )
        producers = [self._normalize_producer(value) for value in producers]
        if not producers:
            raise ValueError("discovered_by must not be empty")
        prefix = f"{table_alias}." if table_alias else ""
        return f"AND {prefix}discovered_by = ANY(%(discovered_by_values)s)", {
            "discovered_by_values": producers
        }

    def _normalize_relation_codes(
        self, relation_code: str | Sequence[str] | None
    ) -> list[str] | None:
        if relation_code is None:
            return None
        values = (
            [relation_code] if isinstance(relation_code, str) else list(relation_code)
        )
        if not values:
            raise ValueError("relation_code must not be empty")
        return [self._normalize_relation_code(value) for value in values]

    def _normalize_relation_code(self, relation_code: str) -> str:
        normalized = relation_code.strip().upper()
        if normalized not in self._RELATION_CODES:
            allowed = ", ".join(sorted(self._RELATION_CODES))
            raise ValueError(f"relation_code must be one of: {allowed}")
        return normalized

    def _normalize_producer(self, discovered_by: str) -> str:
        normalized = discovered_by.strip().upper()
        if not normalized:
            raise ValueError("discovered_by must not be empty")
        return normalized

    def _normalize_optional_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _normalize_target_locator(
        self,
        *,
        target_fs_entry_id: int | None,
        target_path: str | None,
        target_locator_type: str | None,
        target_locator_value: str | None,
    ) -> tuple[str, str]:
        locator_type = (
            target_locator_type.strip().upper()
            if target_locator_type is not None
            else ("FS_ENTRY_ID" if target_fs_entry_id is not None else "KB_PATH")
        )
        if locator_type not in self._TARGET_LOCATOR_TYPES:
            raise ValueError(
                "target_locator_type must be FS_ENTRY_ID, KB_PATH, or ENTITY_SURFACE"
            )
        if target_locator_value is not None:
            locator_value = target_locator_value.strip()
        elif locator_type == "FS_ENTRY_ID" and target_fs_entry_id is not None:
            locator_value = str(target_fs_entry_id)
        elif locator_type == "KB_PATH" and target_path:
            locator_value = target_path
        else:
            locator_value = ""
        if not locator_value:
            raise ValueError("target_locator_value must not be empty")
        return locator_type, locator_value

    def _validate_target_state(
        self,
        *,
        target_fs_entry_id: int | None,
        target_path: str | None,
        status: str,
    ) -> None:
        if status == "resolved":
            valid = target_fs_entry_id is not None and target_path is None
        elif status in {"unresolved", "broken"}:
            valid = target_fs_entry_id is None and bool(target_path)
        else:
            raise ValueError("unsupported assertion status")
        if not valid:
            raise ValueError("target state does not match assertion status")

    def _validate_source_range(
        self,
        *,
        start_line: int | None,
        end_line: int | None,
        start_offset: int | None,
        end_offset: int | None,
    ) -> None:
        if (start_line is None) != (end_line is None):
            raise ValueError("start_line and end_line must be provided together")
        if start_line is not None and (start_line < 1 or end_line < start_line):
            raise ValueError("invalid source line range")
        if (start_offset is None) != (end_offset is None):
            raise ValueError("start_offset and end_offset must be provided together")
        if start_offset is not None and (start_offset < 0 or end_offset < start_offset):
            raise ValueError("invalid source offset range")

    def _build_evidence_fingerprint(self, **evidence: Any) -> str:
        payload = json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _validate_pagination(self, *, limit: int, offset: int) -> None:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must not be negative")

    async def _fetchall(self, cursor: Any) -> list[dict[str, Any]]:
        return list(await cursor.fetchall())
