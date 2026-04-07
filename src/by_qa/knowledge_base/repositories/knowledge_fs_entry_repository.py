"""Persistence helpers for knowledge_fs_entry rows."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class KnowledgeFsEntryRepository:
    """Repository for filesystem entries backing knowledge items."""

    def ensure_root_entry(
        self, cursor: Any, *, knowledge_base_id: int, kb_name: str
    ) -> dict[str, Any] | None:
        """Ensure one root directory entry exists for the knowledge base."""
        cursor.execute(
            """
            SELECT kid, knowledge_base_id, parent_entry_id, path_ltree, full_path, name
            FROM knowledge_fs_entry
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND is_root = TRUE
              AND is_deleted = FALSE
            """,
            {"knowledge_base_id": knowledge_base_id},
        )
        fetchone = getattr(cursor, "fetchone", None)
        existing = fetchone() if callable(fetchone) else None
        if existing is not None:
            self._update_knowledge_base_root(
                cursor,
                knowledge_base_id=knowledge_base_id,
                root_entry_id=self._row_id(existing),
            )
            return existing

        cursor.execute(
            """
            INSERT INTO knowledge_fs_entry (
                knowledge_base_id,
                parent_entry_id,
                entry_type,
                is_root,
                name,
                full_path,
                path_ltree,
                depth,
                status,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                NULL,
                'DIRECTORY',
                TRUE,
                %(name)s,
                %(full_path)s,
                %(path_ltree)s::ltree,
                0,
                'ACTIVE',
                %(metadata)s::jsonb,
                NOW(),
                NOW()
            )
            RETURNING kid, knowledge_base_id, parent_entry_id, path_ltree, full_path, name
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "name": kb_name,
                "full_path": kb_name,
                "path_ltree": f"kb_{knowledge_base_id}",
                "metadata": json.dumps({}),
            },
        )
        created = fetchone() if callable(fetchone) else None
        if created is not None:
            self._update_knowledge_base_root(
                cursor,
                knowledge_base_id=knowledge_base_id,
                root_entry_id=self._row_id(created),
            )
        return created

    def ensure_file_entry(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        root_entry_id: int,
        full_path: str,
    ) -> dict[str, Any] | None:
        """Ensure the directory chain and file node exist for the given path."""
        normalized_path = full_path.strip("/")
        if not normalized_path:
            raise ValueError("full_path must not be empty")

        fetchone = getattr(cursor, "fetchone", None)
        root_entry = self._get_entry_by_id(cursor, entry_id=root_entry_id)
        if root_entry is None:
            raise ValueError(f"root entry not found: {root_entry_id}")

        current_parent_id = self._row_id(root_entry)
        current_path_ltree = self._row_value(root_entry, "path_ltree")
        path_segments = normalized_path.split("/")

        for index, segment in enumerate(path_segments[:-1], start=1):
            directory_full_path = "/".join(path_segments[:index])
            existing = self._get_entry_by_full_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                full_path=directory_full_path,
            )
            if existing is None:
                path_ltree = (
                    f"{current_path_ltree}.{self._path_label('d', index, segment)}"
                )
                cursor.execute(
                    """
                    INSERT INTO knowledge_fs_entry (
                        knowledge_base_id,
                        parent_entry_id,
                        entry_type,
                        is_root,
                        name,
                        full_path,
                        path_ltree,
                        depth,
                        status,
                        metadata,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        %(knowledge_base_id)s,
                        %(parent_entry_id)s,
                        'DIRECTORY',
                        FALSE,
                        %(name)s,
                        %(full_path)s,
                        %(path_ltree)s::ltree,
                        %(depth)s,
                        'ACTIVE',
                        %(metadata)s::jsonb,
                        NOW(),
                        NOW()
                    )
                    RETURNING kid, knowledge_base_id, parent_entry_id, path_ltree, full_path, name
                    """,
                    {
                        "knowledge_base_id": knowledge_base_id,
                        "parent_entry_id": current_parent_id,
                        "name": segment,
                        "full_path": directory_full_path,
                        "path_ltree": path_ltree,
                        "depth": index,
                        "metadata": json.dumps({}),
                    },
                )
                existing = fetchone() if callable(fetchone) else None
            current_parent_id = self._row_id(existing)
            current_path_ltree = self._row_value(existing, "path_ltree")

        existing_file = self._get_entry_by_full_path(
            cursor,
            knowledge_base_id=knowledge_base_id,
            full_path=normalized_path,
        )
        if existing_file is not None:
            return existing_file

        file_name = path_segments[-1]
        path_ltree = f"{current_path_ltree}.{self._path_label('f', len(path_segments), file_name)}"
        cursor.execute(
            """
            INSERT INTO knowledge_fs_entry (
                knowledge_base_id,
                parent_entry_id,
                entry_type,
                is_root,
                name,
                full_path,
                path_ltree,
                depth,
                status,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                %(parent_entry_id)s,
                'FILE',
                FALSE,
                %(name)s,
                %(full_path)s,
                %(path_ltree)s::ltree,
                %(depth)s,
                'ACTIVE',
                %(metadata)s::jsonb,
                NOW(),
                NOW()
            )
            RETURNING kid, knowledge_base_id, parent_entry_id, path_ltree, full_path, name
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "parent_entry_id": current_parent_id,
                "name": file_name,
                "full_path": normalized_path,
                "path_ltree": path_ltree,
                "depth": len(path_segments),
                "metadata": json.dumps({}),
            },
        )
        return fetchone() if callable(fetchone) else None

    def list_root_entries(
        self, cursor: Any, *, kb_codes: list[str]
    ) -> list[dict[str, Any]]:
        """List top-level virtual knowledge-base directories."""
        cursor.execute(
            """
            SELECT
                kb.kb_code,
                fs.full_path AS name,
                'directory' AS type,
                0 AS size
            FROM knowledge_fs_entry fs
            JOIN knowledge_base kb ON kb.kid = fs.knowledge_base_id
            WHERE fs.is_root = TRUE
              AND fs.entry_type = 'DIRECTORY'
              AND fs.status = 'ACTIVE'
              AND fs.is_deleted = FALSE
              AND kb.is_deleted = FALSE
              AND kb.kb_code = ANY(%(kb_codes)s)
            ORDER BY lower(fs.full_path)
            """,
            {"kb_codes": kb_codes},
        )
        return self._fetchall(cursor)

    def list_root_nodes(
        self, cursor: Any, *, kb_codes: list[str]
    ) -> list[dict[str, Any]]:
        """List root nodes with tree metadata for iterative pattern traversal."""
        cursor.execute(
            """
            SELECT
                fs.kid,
                kb.kb_code,
                fs.name,
                fs.full_path,
                fs.path_ltree,
                'directory' AS type,
                0 AS size
            FROM knowledge_fs_entry fs
            JOIN knowledge_base kb ON kb.kid = fs.knowledge_base_id
            WHERE fs.is_root = TRUE
              AND fs.entry_type = 'DIRECTORY'
              AND fs.status = 'ACTIVE'
              AND fs.is_deleted = FALSE
              AND kb.is_deleted = FALSE
              AND kb.kb_code = ANY(%(kb_codes)s)
            ORDER BY lower(fs.full_path)
            """,
            {"kb_codes": kb_codes},
        )
        return self._fetchall(cursor)

    def list_all_root_nodes(self, cursor: Any) -> list[dict[str, Any]]:
        """List all root nodes without kb_code filtering."""
        cursor.execute("""
            SELECT
                fs.kid,
                fs.name,
                fs.full_path,
                fs.path_ltree,
                'directory' AS type,
                0 AS size
            FROM knowledge_fs_entry fs
            WHERE fs.is_root = TRUE
              AND fs.entry_type = 'DIRECTORY'
              AND fs.status = 'ACTIVE'
              AND fs.is_deleted = FALSE
            ORDER BY lower(fs.full_path)
            """)
        return self._fetchall(cursor)

    def get_directory_by_path(
        self, cursor: Any, *, full_path: str
    ) -> dict[str, Any] | None:
        """Look up one directory entry by its virtual path."""
        cursor.execute(
            """
            SELECT kid, knowledge_base_id, parent_entry_id, path_ltree, full_path, name
            FROM knowledge_fs_entry
            WHERE full_path = %(full_path)s
              AND entry_type = 'DIRECTORY'
              AND status = 'ACTIVE'
              AND is_deleted = FALSE
            ORDER BY is_root DESC, kid ASC
            LIMIT 1
            """,
            {"full_path": full_path},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def list_children(
        self, cursor: Any, *, parent_path_ltree: str
    ) -> list[dict[str, Any]]:
        """List direct children under one directory entry."""
        cursor.execute(
            """
            SELECT
                kb.kb_code,
                fs.full_path AS name,
                CASE
                    WHEN fs.entry_type = 'DIRECTORY' THEN 'directory'
                    ELSE 'file'
                END AS type,
                CASE
                    WHEN fs.entry_type = 'DIRECTORY' THEN 0
                    ELSE COALESCE(kv.file_size, 0)
                END AS size
            FROM knowledge_fs_entry fs
            JOIN knowledge_base kb ON kb.kid = fs.knowledge_base_id
            LEFT JOIN knowledge_item ki
                ON ki.fs_entry_id = fs.kid
            LEFT JOIN knowledge_item_version kv
                ON kv.kid = ki.current_version_id
            WHERE fs.status = 'ACTIVE'
              AND fs.is_deleted = FALSE
              AND kb.is_deleted = FALSE
              AND fs.path_ltree <@ %(parent_path_ltree)s::ltree
              AND nlevel(fs.path_ltree) = nlevel(%(parent_path_ltree)s::ltree) + 1
              AND fs.path_ltree <> %(parent_path_ltree)s::ltree
            ORDER BY
                CASE WHEN fs.entry_type = 'DIRECTORY' THEN 0 ELSE 1 END,
                lower(fs.name)
            """,
            {"parent_path_ltree": parent_path_ltree},
        )
        return self._fetchall(cursor)

    def list_child_nodes(
        self, cursor: Any, *, parent_path_ltree: str
    ) -> list[dict[str, Any]]:
        """List direct child nodes with tree metadata for iterative pattern traversal."""
        cursor.execute(
            """
            SELECT
                fs.kid,
                kb.kb_code,
                fs.name,
                fs.full_path,
                fs.path_ltree,
                CASE
                    WHEN fs.entry_type = 'DIRECTORY' THEN 'directory'
                    ELSE 'file'
                END AS type,
                CASE
                    WHEN fs.entry_type = 'DIRECTORY' THEN 0
                    ELSE COALESCE(kv.file_size, 0)
                END AS size
            FROM knowledge_fs_entry fs
            JOIN knowledge_base kb ON kb.kid = fs.knowledge_base_id
            LEFT JOIN knowledge_item ki
                ON ki.fs_entry_id = fs.kid
            LEFT JOIN knowledge_item_version kv
                ON kv.kid = ki.current_version_id
            WHERE fs.status = 'ACTIVE'
              AND fs.is_deleted = FALSE
              AND kb.is_deleted = FALSE
              AND fs.path_ltree <@ %(parent_path_ltree)s::ltree
              AND nlevel(fs.path_ltree) = nlevel(%(parent_path_ltree)s::ltree) + 1
              AND fs.path_ltree <> %(parent_path_ltree)s::ltree
            ORDER BY
                CASE WHEN fs.entry_type = 'DIRECTORY' THEN 0 ELSE 1 END,
                lower(fs.name)
            """,
            {"parent_path_ltree": parent_path_ltree},
        )
        return self._fetchall(cursor)

    def list_entries_by_path_pattern(
        self, cursor: Any, *, path_regex: str, ancestor_path_ltree: str | None = None
    ) -> list[dict[str, Any]]:
        """List entries whose full_path matches the supplied regex."""
        ancestor_clause = ""
        if ancestor_path_ltree:
            ancestor_clause = " AND fs.path_ltree <@ %(ancestor_path_ltree)s::ltree"
        cursor.execute(
            f"""
            SELECT
                kb.kb_code,
                fs.full_path AS name,
                CASE
                    WHEN fs.entry_type = 'DIRECTORY' THEN 'directory'
                    ELSE 'file'
                END AS type,
                CASE
                    WHEN fs.entry_type = 'DIRECTORY' THEN 0
                    ELSE COALESCE(kv.file_size, 0)
                END AS size
            FROM knowledge_fs_entry fs
            JOIN knowledge_base kb ON kb.kid = fs.knowledge_base_id
            LEFT JOIN knowledge_item ki
                ON ki.fs_entry_id = fs.kid
            LEFT JOIN knowledge_item_version kv
                ON kv.kid = ki.current_version_id
            WHERE fs.status = 'ACTIVE'
              AND fs.is_deleted = FALSE
              AND kb.is_deleted = FALSE
              AND fs.full_path ~ %(path_regex)s
              {ancestor_clause}
            ORDER BY lower(fs.full_path)
            """,
            {
                "path_regex": path_regex,
                "ancestor_path_ltree": ancestor_path_ltree,
            },
        )
        return self._fetchall(cursor)

    def get_current_file_version_by_entry_id(
        self, cursor: Any, *, fs_entry_id: int
    ) -> dict[str, Any] | None:
        """Resolve current-version object metadata for one file entry."""
        cursor.execute(
            """
            SELECT
                fs.kid,
                fs.knowledge_base_id,
                ki.kid AS knowledge_item_id,
                kv.kid AS knowledge_item_version_id,
                kb.kb_code,
                kb.kb_name,
                fs.full_path,
                fs.name,
                kv.version,
                kv.bucket_name,
                kv.object_key,
                kv.markdown_bucket_name,
                kv.markdown_object_key,
                kv.markdown_file_size,
                kv.markdown_checksum,
                kv.checksum,
                kv.file_size
            FROM knowledge_fs_entry fs
            JOIN knowledge_item ki
                ON ki.fs_entry_id = fs.kid
            JOIN knowledge_item_version kv
                ON ki.current_version_id = kv.kid
            JOIN knowledge_base kb
                ON kb.kid = fs.knowledge_base_id
            WHERE fs.kid = %(fs_entry_id)s
              AND fs.entry_type = 'FILE'
              AND fs.status = 'ACTIVE'
              AND fs.is_deleted = FALSE
              AND ki.status = 'ACTIVE'
              AND ki.is_deleted = FALSE
              AND kb.is_deleted = FALSE
            LIMIT 1
            """,
            {"fs_entry_id": fs_entry_id},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def _get_entry_by_id(self, cursor: Any, *, entry_id: int) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT kid, knowledge_base_id, parent_entry_id, path_ltree, full_path, name
            FROM knowledge_fs_entry
            WHERE kid = %(entry_id)s
              AND is_deleted = FALSE
            """,
            {"entry_id": entry_id},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def _get_entry_by_full_path(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT kid, knowledge_base_id, parent_entry_id, path_ltree, full_path, name
            FROM knowledge_fs_entry
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND full_path = %(full_path)s
              AND is_deleted = FALSE
            """,
            {"knowledge_base_id": knowledge_base_id, "full_path": full_path},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def soft_delete_by_knowledge_base_id(
        self, cursor: Any, *, knowledge_base_id: int
    ) -> None:
        """Logically delete all filesystem entries under one knowledge base."""
        cursor.execute(
            """
            UPDATE knowledge_fs_entry
            SET is_deleted = TRUE,
                updated_at = NOW()
            WHERE knowledge_base_id = %(knowledge_base_id)s
            """,
            {"knowledge_base_id": knowledge_base_id},
        )

    def soft_delete_file_entry(
        self, cursor: Any, *, knowledge_base_id: int, fs_entry_id: int
    ) -> None:
        """Logically delete one file entry by id."""
        cursor.execute(
            """
            UPDATE knowledge_fs_entry
            SET is_deleted = TRUE,
                updated_at = NOW()
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND kid = %(fs_entry_id)s
            """,
            {"knowledge_base_id": knowledge_base_id, "fs_entry_id": fs_entry_id},
        )

    def _update_knowledge_base_root(
        self, cursor: Any, *, knowledge_base_id: int, root_entry_id: int
    ) -> None:
        cursor.execute(
            """
            UPDATE knowledge_base
            SET root_entry_id = %(root_entry_id)s,
                updated_at = NOW()
            WHERE kid = %(knowledge_base_id)s
              AND (root_entry_id IS NULL OR root_entry_id <> %(root_entry_id)s)
            """,
            {"knowledge_base_id": knowledge_base_id, "root_entry_id": root_entry_id},
        )

    def _fetchall(self, cursor: Any) -> list[dict[str, Any]]:
        fetchall = getattr(cursor, "fetchall", None)
        if callable(fetchall):
            return list(fetchall())
        return []

    def _path_label(self, prefix: str, depth: int, segment: str) -> str:
        digest = hashlib.md5(segment.encode("utf-8")).hexdigest()[:8]
        return f"{prefix}{depth}_{digest}"

    def _row_id(self, row: dict[str, Any]) -> int:
        return int(self._row_value(row, "kid"))

    def _row_value(self, row: dict[str, Any], key: str) -> Any:
        if isinstance(row, dict):
            if key in row:
                return row[key]
            if key == "kid" and "id" in row:
                return row["id"]
        raise KeyError(key)
