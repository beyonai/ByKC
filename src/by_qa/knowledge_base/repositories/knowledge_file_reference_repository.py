"""Persistence helpers for Markdown references and semantic document relations."""

from __future__ import annotations

from typing import Any


class KnowledgeFileReferenceRepository:
    """Repository for knowledge_file_reference rows."""

    _SEMANTIC_RELATION_CODES = frozenset({"MENTIONS", "PART_OF", "IS_A", "DEPENDS_ON"})

    async def delete_for_source_fs_entry_id(
        self, cursor: Any, *, source_fs_entry_id: int
    ) -> None:
        """Delete references emitted by a file without affecting inbound references."""
        await cursor.execute(
            """
            DELETE FROM knowledge_file_reference
            WHERE source_fs_entry_id = %(source_fs_entry_id)s
              AND reference_type = 'MARKDOWN'
            """,
            {"source_fs_entry_id": source_fs_entry_id},
        )

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
    ) -> dict[str, Any] | None:
        """Insert one parsed Markdown file reference."""
        await cursor.execute(
            """
            INSERT INTO knowledge_file_reference (
                knowledge_base_id,
                source_fs_entry_id,
                target_fs_entry_id,
                original_target,
                target_path,
                target_suffix,
                target_kind,
                status,
                reference_type,
                last_resolved_at,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                %(source_fs_entry_id)s,
                %(target_fs_entry_id)s,
                %(original_target)s,
                %(target_path)s,
                %(target_suffix)s,
                %(target_kind)s,
                %(status)s,
                'MARKDOWN',
                CASE WHEN %(status)s = 'resolved' THEN NOW() ELSE NULL END,
                NOW(),
                NOW()
            )
            RETURNING
                kid,
                knowledge_base_id,
                source_fs_entry_id,
                target_fs_entry_id,
                original_target,
                target_path,
                target_suffix,
                target_kind,
                status,
                reference_type,
                relation_code,
                confidence,
                discovered_by,
                definition_version,
                source_task_id,
                last_resolved_at,
                created_at,
                updated_at
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
            },
        )
        return await cursor.fetchone()

    async def list_by_source(
        self,
        cursor: Any,
        *,
        source_fs_entry_id: int,
    ) -> list[dict[str, Any]]:
        """List references emitted by one source file."""
        await cursor.execute(
            f"""
            {self._select_with_target()}
            WHERE kfr.source_fs_entry_id = %(source_fs_entry_id)s
              AND kfr.reference_type = 'MARKDOWN'
            ORDER BY kfr.kid
            """,
            {"source_fs_entry_id": source_fs_entry_id},
        )
        return await self._fetchall(cursor)

    async def upsert_semantic_relation(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        source_fs_entry_id: int,
        target_fs_entry_id: int,
        relation_code: str,
        original_target: str,
        confidence: float | None = None,
        discovered_by: str | None = None,
        definition_version: str | None = None,
        source_task_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Create or refresh one stable semantic edge.

        Source, relation and target form the identity. The ``INSERT .. SELECT``
        also prevents cross-knowledge-base relations when this repository is used
        without an upstream service check.
        """
        self._validate_relation_code(relation_code)
        if source_fs_entry_id == target_fs_entry_id:
            raise ValueError("semantic relation source and target must differ")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not original_target:
            raise ValueError("original_target must not be empty")

        await cursor.execute(
            """
            INSERT INTO knowledge_file_reference (
                knowledge_base_id,
                source_fs_entry_id,
                target_fs_entry_id,
                original_target,
                target_path,
                target_suffix,
                target_kind,
                status,
                reference_type,
                relation_code,
                confidence,
                discovered_by,
                definition_version,
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
                NULL,
                '',
                'FILE',
                'resolved',
                'SEMANTIC',
                %(relation_code)s,
                %(confidence)s,
                %(discovered_by)s,
                %(definition_version)s,
                %(source_task_id)s,
                NOW(),
                NOW(),
                NOW()
            FROM knowledge_fs_entry source
            JOIN knowledge_fs_entry target
              ON target.kid = %(target_fs_entry_id)s
             AND target.knowledge_base_id = %(knowledge_base_id)s
            WHERE source.kid = %(source_fs_entry_id)s
              AND source.knowledge_base_id = %(knowledge_base_id)s
            ON DUPLICATE KEY UPDATE
                knowledge_base_id = EXCLUDED.knowledge_base_id,
                original_target = EXCLUDED.original_target,
                confidence = EXCLUDED.confidence,
                discovered_by = EXCLUDED.discovered_by,
                definition_version = EXCLUDED.definition_version,
                source_task_id = EXCLUDED.source_task_id,
                status = 'resolved',
                target_path = NULL,
                target_suffix = '',
                target_kind = 'FILE',
                last_resolved_at = NOW(),
                updated_at = NOW()
            RETURNING
                kid,
                knowledge_base_id,
                source_fs_entry_id,
                target_fs_entry_id,
                original_target,
                target_path,
                target_suffix,
                target_kind,
                status,
                reference_type,
                relation_code,
                confidence,
                discovered_by,
                definition_version,
                source_task_id,
                last_resolved_at,
                created_at,
                updated_at
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "source_fs_entry_id": source_fs_entry_id,
                "target_fs_entry_id": target_fs_entry_id,
                "relation_code": relation_code,
                "original_target": original_target,
                "confidence": confidence,
                "discovered_by": discovered_by,
                "definition_version": definition_version,
                "source_task_id": source_task_id,
            },
        )
        return await cursor.fetchone()

    async def list_semantic_by_source(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        source_fs_entry_id: int,
        relation_code: str | list[str] | tuple[str, ...] | None = None,
        include_deleted_entries: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List paged semantic outgoing edges for one source document."""
        relation_sql, relation_params = self._semantic_relation_filter(relation_code)
        self._validate_pagination(limit=limit, offset=offset)
        deleted_filter = (
            ""
            if include_deleted_entries
            else "AND source.is_deleted = FALSE AND target.is_deleted = FALSE"
        )
        await cursor.execute(
            f"""
            {self._select_semantic()}
            WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
              AND kfr.reference_type = 'SEMANTIC'
              AND kfr.source_fs_entry_id = %(source_fs_entry_id)s
              {relation_sql}
              {deleted_filter}
            ORDER BY kfr.kid
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
        return await self._fetchall(cursor)

    async def count_semantic_by_source(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        source_fs_entry_id: int,
        relation_code: str | list[str] | tuple[str, ...] | None = None,
        include_deleted_entries: bool = False,
    ) -> int:
        """Count semantic outgoing edges using the same filters as the list query."""
        relation_sql, relation_params = self._semantic_relation_filter(relation_code)
        deleted_filter = (
            ""
            if include_deleted_entries
            else "AND source.is_deleted = FALSE AND target.is_deleted = FALSE"
        )
        await cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM knowledge_file_reference kfr
            JOIN knowledge_fs_entry source ON source.kid = kfr.source_fs_entry_id
            JOIN knowledge_fs_entry target ON target.kid = kfr.target_fs_entry_id
            WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
              AND kfr.reference_type = 'SEMANTIC'
              AND kfr.source_fs_entry_id = %(source_fs_entry_id)s
              {relation_sql}
              {deleted_filter}
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "source_fs_entry_id": source_fs_entry_id,
                **relation_params,
            },
        )
        row = await cursor.fetchone()
        return int(row["total"]) if row is not None else 0

    async def list_semantic_by_target(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_fs_entry_id: int,
        relation_code: str | list[str] | tuple[str, ...] | None = None,
        include_deleted_entries: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List paged semantic incoming edges for one target document."""
        relation_sql, relation_params = self._semantic_relation_filter(relation_code)
        self._validate_pagination(limit=limit, offset=offset)
        deleted_filter = (
            ""
            if include_deleted_entries
            else "AND source.is_deleted = FALSE AND target.is_deleted = FALSE"
        )
        await cursor.execute(
            f"""
            {self._select_semantic()}
            WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
              AND kfr.reference_type = 'SEMANTIC'
              AND kfr.target_fs_entry_id = %(target_fs_entry_id)s
              {relation_sql}
              {deleted_filter}
            ORDER BY kfr.kid
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

    async def count_semantic_by_target(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_fs_entry_id: int,
        relation_code: str | list[str] | tuple[str, ...] | None = None,
        include_deleted_entries: bool = False,
    ) -> int:
        """Count semantic incoming edges using the same filters as the list query."""
        relation_sql, relation_params = self._semantic_relation_filter(relation_code)
        deleted_filter = (
            ""
            if include_deleted_entries
            else "AND source.is_deleted = FALSE AND target.is_deleted = FALSE"
        )
        await cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM knowledge_file_reference kfr
            JOIN knowledge_fs_entry source ON source.kid = kfr.source_fs_entry_id
            JOIN knowledge_fs_entry target ON target.kid = kfr.target_fs_entry_id
            WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
              AND kfr.reference_type = 'SEMANTIC'
              AND kfr.target_fs_entry_id = %(target_fs_entry_id)s
              {relation_sql}
              {deleted_filter}
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "target_fs_entry_id": target_fs_entry_id,
                **relation_params,
            },
        )
        row = await cursor.fetchone()
        return int(row["total"]) if row is not None else 0

    async def delete_semantic_for_source_fs_entry_id(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        source_fs_entry_id: int,
        relation_code: str | list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Delete semantic edges emitted by one source, optionally by relation type."""
        relation_sql, relation_params = self._semantic_relation_filter(
            relation_code, table_alias=None
        )
        await cursor.execute(
            f"""
            DELETE FROM knowledge_file_reference
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND reference_type = 'SEMANTIC'
              AND source_fs_entry_id = %(source_fs_entry_id)s
              {relation_sql}
            RETURNING kid, source_fs_entry_id, target_fs_entry_id, relation_code
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "source_fs_entry_id": source_fs_entry_id,
                **relation_params,
            },
        )
        return await self._fetchall(cursor)

    async def delete_semantic_for_source_task_id(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        source_task_id: int,
    ) -> list[dict[str, Any]]:
        """Delete only semantic edges attributed to one processing task."""
        await cursor.execute(
            """
            DELETE FROM knowledge_file_reference
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND reference_type = 'SEMANTIC'
              AND source_task_id = %(source_task_id)s
            RETURNING kid, source_fs_entry_id, target_fs_entry_id, relation_code
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "source_task_id": source_task_id,
            },
        )
        return await self._fetchall(cursor)

    async def list_by_reference_ids(
        self,
        cursor: Any,
        *,
        reference_ids: list[int],
    ) -> list[dict[str, Any]]:
        """List references by stable reference ids."""
        if not reference_ids:
            return []
        await cursor.execute(
            f"""
            {self._select_with_target()}
            WHERE kfr.kid = ANY(%(reference_ids)s)
              AND kfr.reference_type = 'MARKDOWN'
            ORDER BY kfr.kid
            """,
            {"reference_ids": list(reference_ids)},
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
        """Resolve unresolved or broken references that point at one exact path."""
        await cursor.execute(
            """
            UPDATE knowledge_file_reference
            SET target_fs_entry_id = %(target_fs_entry_id)s,
                target_path = NULL,
                status = 'resolved',
                last_resolved_at = NOW(),
                updated_at = NOW()
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND reference_type = 'MARKDOWN'
              AND target_fs_entry_id IS NULL
              AND target_path = %(target_path)s
              AND status IN ('unresolved', 'broken')
            RETURNING
                kid,
                knowledge_base_id,
                source_fs_entry_id,
                target_fs_entry_id,
                original_target,
                target_path,
                target_suffix,
                target_kind,
                status,
                reference_type,
                relation_code,
                confidence,
                discovered_by,
                definition_version,
                source_task_id,
                last_resolved_at,
                created_at,
                updated_at
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "target_path": target_path,
                "target_fs_entry_id": target_fs_entry_id,
            },
        )
        return await self._fetchall(cursor)

    async def rebind_deleted_target_for_path(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_path: str,
        target_fs_entry_id: int,
    ) -> list[dict[str, Any]]:
        """Rebind resolved refs from a soft-deleted row at this path to a live row."""
        await cursor.execute(
            """
            UPDATE knowledge_file_reference kfr
            SET target_fs_entry_id = %(target_fs_entry_id)s,
                target_path = NULL,
                status = 'resolved',
                last_resolved_at = NOW(),
                updated_at = NOW()
            FROM knowledge_fs_entry deleted_target
            WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
              AND kfr.reference_type = 'MARKDOWN'
              AND deleted_target.kid = kfr.target_fs_entry_id
              AND kfr.target_fs_entry_id <> %(target_fs_entry_id)s
              AND kfr.status = 'resolved'
              AND deleted_target.is_deleted = TRUE
              AND deleted_target.virtual_path = %(target_path)s
            RETURNING
                kfr.kid,
                kfr.knowledge_base_id,
                kfr.source_fs_entry_id,
                kfr.target_fs_entry_id,
                kfr.original_target,
                kfr.target_path,
                kfr.target_suffix,
                kfr.target_kind,
                kfr.status,
                kfr.reference_type,
                kfr.relation_code,
                kfr.confidence,
                kfr.discovered_by,
                kfr.definition_version,
                kfr.source_task_id,
                kfr.last_resolved_at,
                kfr.created_at,
                kfr.updated_at
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "target_path": target_path,
                "target_fs_entry_id": target_fs_entry_id,
            },
        )
        return await self._fetchall(cursor)

    async def mark_targets_deleted(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        targets: list[tuple[int, str]],
    ) -> list[dict[str, Any]]:
        """Mark references to deleted target rows as broken."""
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
                status = 'broken',
                updated_at = NOW()
            FROM (VALUES {", ".join(values_sql)})
                AS deleted_targets(target_fs_entry_id, target_path)
            WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
              AND kfr.reference_type = 'MARKDOWN'
              AND kfr.target_fs_entry_id = deleted_targets.target_fs_entry_id
              AND kfr.status = 'resolved'
            RETURNING
                kfr.kid,
                kfr.knowledge_base_id,
                kfr.source_fs_entry_id,
                kfr.target_fs_entry_id,
                kfr.original_target,
                kfr.target_path,
                kfr.target_suffix,
                kfr.target_kind,
                kfr.status,
                kfr.reference_type,
                kfr.relation_code,
                kfr.confidence,
                kfr.discovered_by,
                kfr.definition_version,
                kfr.source_task_id,
                kfr.last_resolved_at,
                kfr.created_at,
                kfr.updated_at
            """,
            params,
        )
        return await self._fetchall(cursor)

    async def mark_target_restored(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        target_path: str,
        target_fs_entry_id: int,
    ) -> list[dict[str, Any]]:
        """Restore broken references for one path to a live target row."""
        await cursor.execute(
            """
            UPDATE knowledge_file_reference
            SET target_fs_entry_id = %(target_fs_entry_id)s,
                target_path = NULL,
                status = 'resolved',
                last_resolved_at = NOW(),
                updated_at = NOW()
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND reference_type = 'MARKDOWN'
              AND target_fs_entry_id IS NULL
              AND target_path = %(target_path)s
              AND status = 'broken'
            RETURNING
                kid,
                knowledge_base_id,
                source_fs_entry_id,
                target_fs_entry_id,
                original_target,
                target_path,
                target_suffix,
                target_kind,
                status,
                reference_type,
                relation_code,
                confidence,
                discovered_by,
                definition_version,
                source_task_id,
                last_resolved_at,
                created_at,
                updated_at
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "target_path": target_path,
                "target_fs_entry_id": target_fs_entry_id,
            },
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
        """List source references for a live target id or pending target path."""
        if (target_fs_entry_id is None) == (target_path is None):
            raise ValueError("provide exactly one of target_fs_entry_id or target_path")

        source_filter = (
            "" if include_deleted_sources else "AND source.is_deleted = FALSE"
        )
        if target_fs_entry_id is not None:
            await cursor.execute(
                f"""
                {self._select_with_source()}
                WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
                  AND kfr.reference_type = 'MARKDOWN'
                  AND kfr.target_fs_entry_id = %(target_fs_entry_id)s
                  AND kfr.status = 'resolved'
                  {source_filter}
                ORDER BY kfr.kid
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "target_fs_entry_id": target_fs_entry_id,
                },
            )
        else:
            await cursor.execute(
                f"""
                {self._select_with_source()}
                WHERE kfr.knowledge_base_id = %(knowledge_base_id)s
                  AND kfr.reference_type = 'MARKDOWN'
                  AND kfr.target_fs_entry_id IS NULL
                  AND kfr.target_path = %(target_path)s
                  AND kfr.status IN ('unresolved', 'broken')
                  {source_filter}
                ORDER BY kfr.kid
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "target_path": target_path,
                },
            )
        return await self._fetchall(cursor)

    def _select_with_target(self) -> str:
        return """
            SELECT
                kfr.kid,
                kfr.knowledge_base_id,
                kfr.source_fs_entry_id,
                kfr.target_fs_entry_id,
                kfr.original_target,
                kfr.target_path,
                kfr.target_suffix,
                kfr.target_kind,
                kfr.status,
                kfr.reference_type,
                kfr.relation_code,
                kfr.confidence,
                kfr.discovered_by,
                kfr.definition_version,
                kfr.source_task_id,
                kfr.last_resolved_at,
                kfr.created_at,
                kfr.updated_at,
                target.virtual_path AS target_virtual_path,
                target.is_deleted AS target_is_deleted
            FROM knowledge_file_reference kfr
            LEFT JOIN knowledge_fs_entry target
              ON target.kid = kfr.target_fs_entry_id
            """

    def _select_with_source(self) -> str:
        return """
            SELECT
                kfr.kid,
                kfr.knowledge_base_id,
                kfr.source_fs_entry_id,
                kfr.target_fs_entry_id,
                kfr.original_target,
                kfr.target_path,
                kfr.target_suffix,
                kfr.target_kind,
                kfr.status,
                kfr.reference_type,
                kfr.relation_code,
                kfr.confidence,
                kfr.discovered_by,
                kfr.definition_version,
                kfr.source_task_id,
                kfr.last_resolved_at,
                kfr.created_at,
                kfr.updated_at,
                source.virtual_path AS source_virtual_path,
                source.is_deleted AS source_is_deleted
            FROM knowledge_file_reference kfr
            JOIN knowledge_fs_entry source
              ON source.kid = kfr.source_fs_entry_id
            """

    def _select_semantic(self) -> str:
        return """
            SELECT
                kfr.kid,
                kfr.knowledge_base_id,
                kfr.source_fs_entry_id,
                kfr.target_fs_entry_id,
                kfr.original_target,
                kfr.status,
                kfr.reference_type,
                kfr.relation_code,
                kfr.confidence,
                kfr.discovered_by,
                kfr.definition_version,
                kfr.source_task_id,
                kfr.last_resolved_at,
                kfr.created_at,
                kfr.updated_at,
                source.virtual_path AS source_virtual_path,
                source.is_deleted AS source_is_deleted,
                target.virtual_path AS target_virtual_path,
                target.is_deleted AS target_is_deleted
            FROM knowledge_file_reference kfr
            JOIN knowledge_fs_entry source
              ON source.kid = kfr.source_fs_entry_id
            JOIN knowledge_fs_entry target
              ON target.kid = kfr.target_fs_entry_id
            """

    def _semantic_relation_filter(
        self,
        relation_code: str | list[str] | tuple[str, ...] | None,
        *,
        table_alias: str | None = "kfr",
    ) -> tuple[str, dict[str, Any]]:
        if relation_code is None:
            return "", {}
        relation_codes = (
            [relation_code] if isinstance(relation_code, str) else list(relation_code)
        )
        if not relation_codes:
            return "", {}
        for code in relation_codes:
            self._validate_relation_code(code)
        prefix = f"{table_alias}." if table_alias else ""
        return f"AND {prefix}relation_code = ANY(%(relation_codes)s)", {
            "relation_codes": relation_codes
        }

    def _validate_relation_code(self, relation_code: str) -> None:
        if relation_code not in self._SEMANTIC_RELATION_CODES:
            allowed = ", ".join(sorted(self._SEMANTIC_RELATION_CODES))
            raise ValueError(f"relation_code must be one of: {allowed}")

    def _validate_pagination(self, *, limit: int, offset: int) -> None:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must not be negative")

    async def _fetchall(self, cursor: Any) -> list[dict[str, Any]]:
        return list(await cursor.fetchall())
