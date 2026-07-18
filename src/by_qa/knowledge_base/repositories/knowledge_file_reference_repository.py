"""Persistence helpers for stable Markdown file references."""

from __future__ import annotations

from typing import Any


class KnowledgeFileReferenceRepository:
    """Repository for knowledge_file_reference rows."""

    async def delete_for_source_fs_entry_id(
        self, cursor: Any, *, source_fs_entry_id: int
    ) -> None:
        """Delete references emitted by a file without affecting inbound references."""
        await cursor.execute(
            """
            DELETE FROM knowledge_file_reference
            WHERE source_fs_entry_id = %(source_fs_entry_id)s
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
            ORDER BY kfr.kid
            """,
            {"source_fs_entry_id": source_fs_entry_id},
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
                kfr.last_resolved_at,
                kfr.created_at,
                kfr.updated_at,
                source.virtual_path AS source_virtual_path,
                source.is_deleted AS source_is_deleted
            FROM knowledge_file_reference kfr
            JOIN knowledge_fs_entry source
              ON source.kid = kfr.source_fs_entry_id
            """

    async def _fetchall(self, cursor: Any) -> list[dict[str, Any]]:
        return list(await cursor.fetchall())
