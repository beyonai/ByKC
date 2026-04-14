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
            SELECT kid, knowledge_base_id, parent_entry_id, path_ltree, name, entry_type, is_root, depth
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
            return existing

        cursor.execute(
            """
            INSERT INTO knowledge_fs_entry (
                knowledge_base_id,
                parent_entry_id,
                entry_type,
                is_root,
                name,
                path_ltree,
                depth,
                description,
                file_bucket_name,
                file_object_key,
                markdown_bucket_name,
                markdown_object_key,
                file_size,
                mime_type,
                checksum,
                line_count,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                NULL,
                'DIRECTORY',
                TRUE,
                %(name)s,
                %(path_ltree)s::ltree,
                0,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NOW(),
                NOW()
            )
            RETURNING kid, knowledge_base_id, parent_entry_id, path_ltree, name, entry_type, is_root, depth
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "name": kb_name,
                "path_ltree": f"kb_{knowledge_base_id}",
            },
        )
        created = fetchone() if callable(fetchone) else None
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
            existing = self._get_child_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=current_parent_id,
                name=segment,
            )
            if existing is None or existing.get("entry_type") != "DIRECTORY":
                missing_directory_path = "/".join(path_segments[:index])
                raise ValueError(
                    f"parent directory not found: {missing_directory_path}"
                )
            current_parent_id = self._row_id(existing)
            current_path_ltree = self._row_value(existing, "path_ltree")

        existing_file = self._get_child_entry(
            cursor,
            knowledge_base_id=knowledge_base_id,
            parent_entry_id=current_parent_id,
            name=path_segments[-1],
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
                %(path_ltree)s::ltree,
                %(depth)s,
                'ACTIVE',
                %(metadata)s::jsonb,
                NOW(),
                NOW()
            )
            RETURNING kid, knowledge_base_id, parent_entry_id, path_ltree, name, entry_type, is_root, depth
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "parent_entry_id": current_parent_id,
                "name": file_name,
                "path_ltree": path_ltree,
                "depth": len(path_segments),
                "metadata": json.dumps({}),
            },
        )
        return fetchone() if callable(fetchone) else None

    def create_directory_entry(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        root_entry_id: int,
        full_path: str,
    ) -> dict[str, Any] | None:
        """Create one directory node when its parent directory already exists."""
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
            existing = self._get_child_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=current_parent_id,
                name=segment,
            )
            if existing is None or existing.get("entry_type") != "DIRECTORY":
                missing_directory_path = "/".join(path_segments[:index])
                raise ValueError(
                    f"parent directory not found: {missing_directory_path}"
                )
            current_parent_id = self._row_id(existing)
            current_path_ltree = self._row_value(existing, "path_ltree")

        existing_directory = self._get_child_entry(
            cursor,
            knowledge_base_id=knowledge_base_id,
            parent_entry_id=current_parent_id,
            name=path_segments[-1],
        )
        if existing_directory is not None:
            raise ValueError(f"directory path already exists: {normalized_path}")

        directory_name = path_segments[-1]
        path_ltree = f"{current_path_ltree}.{self._path_label('d', len(path_segments), directory_name)}"
        cursor.execute(
            """
            INSERT INTO knowledge_fs_entry (
                knowledge_base_id,
                parent_entry_id,
                entry_type,
                is_root,
                name,
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
                %(path_ltree)s::ltree,
                %(depth)s,
                'ACTIVE',
                %(metadata)s::jsonb,
                NOW(),
                NOW()
            )
            RETURNING kid, knowledge_base_id, parent_entry_id, path_ltree, name, entry_type, is_root, depth
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "parent_entry_id": current_parent_id,
                "name": directory_name,
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
                fs.name AS name,
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
            ORDER BY lower(fs.name)
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
            ORDER BY lower(fs.name)
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
                fs.path_ltree,
                'directory' AS type,
                0 AS size
            FROM knowledge_fs_entry fs
            WHERE fs.is_root = TRUE
              AND fs.entry_type = 'DIRECTORY'
              AND fs.status = 'ACTIVE'
              AND fs.is_deleted = FALSE
            ORDER BY lower(fs.name)
            """)
        return self._fetchall(cursor)

    def get_directory_by_path(
        self, cursor: Any, *, full_path: str
    ) -> dict[str, Any] | None:
        """Look up one directory entry by its virtual path."""
        path_segments = [
            segment for segment in full_path.strip("/").split("/") if segment
        ]
        if not path_segments:
            return None
        current = self._get_root_by_name(cursor, name=path_segments[0])
        if current is None:
            return None
        for segment in path_segments[1:]:
            current = self._get_child_entry(
                cursor,
                knowledge_base_id=int(current["knowledge_base_id"]),
                parent_entry_id=self._row_id(current),
                name=segment,
            )
            if current is None:
                return None
        return current if current.get("entry_type") == "DIRECTORY" else None

    def list_children(
        self, cursor: Any, *, parent_path_ltree: str
    ) -> list[dict[str, Any]]:
        """List direct children under one directory entry."""
        cursor.execute(
            """
            SELECT
                kb.kb_code,
                fs.name AS name,
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

    def get_current_file_version_by_entry_id(
        self, cursor: Any, *, fs_entry_id: int
    ) -> dict[str, Any] | None:
        """Resolve current-version object metadata for one file entry."""
        cursor.execute(
            """
            WITH RECURSIVE item_path AS (
                SELECT kid, parent_entry_id, name, depth, is_root
                FROM knowledge_fs_entry
                WHERE kid = %(fs_entry_id)s
              UNION ALL
                SELECT parent.kid, parent.parent_entry_id, parent.name, parent.depth, parent.is_root
                FROM knowledge_fs_entry parent
                JOIN item_path child ON child.parent_entry_id = parent.kid
                WHERE parent.is_deleted = FALSE
            )
            SELECT
                fs.kid,
                fs.knowledge_base_id,
                ki.kid AS knowledge_item_id,
                kv.kid AS knowledge_item_version_id,
                kb.kb_code,
                kb.kb_name,
                fs.name,
                (
                    SELECT COALESCE(string_agg(name, '/' ORDER BY depth), '')
                    FROM item_path
                    WHERE is_root = FALSE
                ) AS full_path,
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
            SELECT kid, knowledge_base_id, parent_entry_id, path_ltree, name, entry_type, is_root, depth
            FROM knowledge_fs_entry
            WHERE kid = %(entry_id)s
              AND is_deleted = FALSE
            """,
            {"entry_id": entry_id},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def _get_child_entry(
        self, cursor: Any, *, knowledge_base_id: int, parent_entry_id: int, name: str
    ) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT kid, knowledge_base_id, parent_entry_id, path_ltree, name, entry_type, is_root, depth
            FROM knowledge_fs_entry
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND parent_entry_id = %(parent_entry_id)s
              AND name = %(name)s
              AND is_deleted = FALSE
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "parent_entry_id": parent_entry_id,
                "name": name,
            },
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def _get_root_by_name(self, cursor: Any, *, name: str) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT kid, knowledge_base_id, parent_entry_id, path_ltree, name, entry_type, is_root, depth
            FROM knowledge_fs_entry
            WHERE is_root = TRUE
              AND name = %(name)s
              AND is_deleted = FALSE
            ORDER BY kid ASC
            LIMIT 1
            """,
            {"name": name},
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

    def list_subtree_entry_ids(
        self, cursor: Any, *, knowledge_base_id: int, root_fs_entry_id: int
    ) -> list[int]:
        """List filesystem entry ids within one directory subtree, including the root."""
        cursor.execute(
            """
            SELECT fs.kid
            FROM knowledge_fs_entry fs
            JOIN knowledge_fs_entry root
              ON root.kid = %(root_fs_entry_id)s
            WHERE fs.knowledge_base_id = %(knowledge_base_id)s
              AND fs.is_deleted = FALSE
              AND fs.path_ltree <@ root.path_ltree
            ORDER BY fs.kid
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "root_fs_entry_id": root_fs_entry_id,
            },
        )
        return [int(row["kid"]) for row in self._fetchall(cursor)]

    def soft_delete_subtree(
        self, cursor: Any, *, knowledge_base_id: int, root_fs_entry_id: int
    ) -> None:
        """Logically delete one directory subtree, including the root entry."""
        cursor.execute(
            """
            UPDATE knowledge_fs_entry fs
            SET is_deleted = TRUE,
                updated_at = NOW()
            WHERE fs.knowledge_base_id = %(knowledge_base_id)s
              AND fs.path_ltree <@ (
                  SELECT path_ltree
                  FROM knowledge_fs_entry
                  WHERE kid = %(root_fs_entry_id)s
              )::ltree
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "root_fs_entry_id": root_fs_entry_id,
            },
        )

    def get_entry_by_id(self, cursor: Any, *, entry_id: int) -> dict[str, Any] | None:
        """Fetch one filesystem entry by id."""
        return self._get_entry_by_id(cursor, entry_id=entry_id)

    def get_child_entry(
        self, cursor: Any, *, knowledge_base_id: int, parent_entry_id: int, name: str
    ) -> dict[str, Any] | None:
        """Fetch one direct child entry by parent and name."""
        return self._get_child_entry(
            cursor,
            knowledge_base_id=knowledge_base_id,
            parent_entry_id=parent_entry_id,
            name=name,
        )

    def get_virtual_path_by_entry_id(self, cursor: Any, *, entry_id: int) -> str | None:
        """Build the virtual path for one filesystem entry, excluding the KB root name."""
        cursor.execute(
            """
            WITH RECURSIVE item_path AS (
                SELECT kid, parent_entry_id, name, depth, is_root
                FROM knowledge_fs_entry
                WHERE kid = %(entry_id)s
                  AND is_deleted = FALSE
              UNION ALL
                SELECT parent.kid, parent.parent_entry_id, parent.name, parent.depth, parent.is_root
                FROM knowledge_fs_entry parent
                JOIN item_path child ON child.parent_entry_id = parent.kid
                WHERE parent.is_deleted = FALSE
            )
            SELECT COALESCE(string_agg(name, '/' ORDER BY depth), '')
            FROM item_path
            WHERE is_root = FALSE
            """,
            {"entry_id": entry_id},
        )
        fetchone = getattr(cursor, "fetchone", None)
        row = fetchone() if callable(fetchone) else None
        if row is None:
            return None
        if isinstance(row, dict):
            return (
                row.get("coalesce")
                or row.get("full_path")
                or next(iter(row.values()), None)
            )
        return str(row[0]) if row else None

    def rename_entry(self, cursor: Any, *, entry_id: int, new_name: str) -> None:
        """Rename one filesystem entry without moving it."""
        cursor.execute(
            """
            UPDATE knowledge_fs_entry
            SET name = %(new_name)s,
                updated_at = NOW()
            WHERE kid = %(entry_id)s
              AND is_deleted = FALSE
            """,
            {"entry_id": entry_id, "new_name": new_name},
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
