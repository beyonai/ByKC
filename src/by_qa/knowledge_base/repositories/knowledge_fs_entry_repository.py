"""Persistence helpers for knowledge_fs_entry rows."""

from __future__ import annotations

import hashlib
from typing import Any

from by_qa.knowledge_base.infrastructure.storage import StorageLocation


class KnowledgeFsEntryRepository:
    """Repository for filesystem entries backing knowledge items."""

    async def create_directory_entry(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        full_path: str,
        directory_description: str | None = None,
    ) -> dict[str, Any] | None:
        """Create one directory node within one knowledge base path tree."""
        normalized_path = full_path.strip("/")
        if not normalized_path:
            raise ValueError("full_path must not be empty")

        current_parent_id: int | None = None
        current_path_ltree: str | None = None
        current_virtual_path: str = "/"
        path_segments = normalized_path.split("/")
        await self._lock_entry_tree_for_write(cursor)

        for index, segment in enumerate(path_segments[:-1], start=1):
            existing = await self._get_child_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=current_parent_id,
                name=segment,
            )
            if existing is not None:
                if existing.get("entry_type") != "DIRECTORY":
                    missing_directory_path = "/".join(path_segments[:index])
                    raise ValueError(
                        f"parent directory not found: {missing_directory_path}"
                    )
                current_parent_id = self._row_id(existing)
                current_path_ltree = self._row_value(existing, "path_ltree")
                current_virtual_path = self._row_value(existing, "virtual_path")
                continue

            parent_directory = await self._insert_directory_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=current_parent_id,
                parent_path_ltree=current_path_ltree,
                parent_virtual_path=current_virtual_path,
                name=segment,
                depth=index,
                description=None,
            )
            current_parent_id = self._row_id(parent_directory)
            current_path_ltree = self._row_value(parent_directory, "path_ltree")
            current_virtual_path = self._row_value(parent_directory, "virtual_path")

        existing_directory = await self._get_child_entry(
            cursor,
            knowledge_base_id=knowledge_base_id,
            parent_entry_id=current_parent_id,
            name=path_segments[-1],
        )
        if existing_directory is not None:
            if existing_directory.get("entry_type") == "DIRECTORY":
                return existing_directory
            raise ValueError(f"directory path already exists: {normalized_path}")

        return await self._insert_directory_entry(
            cursor,
            knowledge_base_id=knowledge_base_id,
            parent_entry_id=current_parent_id,
            parent_path_ltree=current_path_ltree,
            parent_virtual_path=current_virtual_path,
            name=path_segments[-1],
            depth=len(path_segments),
            description=directory_description,
        )

    async def create_file_entry(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        full_path: str,
        file_description: str | None = None,
    ) -> dict[str, Any] | None:
        """Create one file entry under an existing parent directory."""
        normalized_path = full_path.strip("/")
        if not normalized_path:
            raise ValueError("full_path must not be empty")

        current_parent_id: int | None = None
        current_path_ltree: str | None = None
        current_virtual_path: str = "/"
        path_segments = normalized_path.split("/")
        await self._lock_entry_tree_for_write(cursor)

        for index, segment in enumerate(path_segments[:-1], start=1):
            existing = await self._get_child_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=current_parent_id,
                name=segment,
            )
            if existing is not None:
                if existing.get("entry_type") != "DIRECTORY":
                    missing_directory_path = "/".join(path_segments[:index])
                    raise ValueError(
                        f"parent directory not found: {missing_directory_path}"
                    )
                current_parent_id = self._row_id(existing)
                current_path_ltree = self._row_value(existing, "path_ltree")
                current_virtual_path = self._row_value(existing, "virtual_path")
                continue

            parent_directory = await self._insert_directory_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=current_parent_id,
                parent_path_ltree=current_path_ltree,
                parent_virtual_path=current_virtual_path,
                name=segment,
                depth=index,
                description=None,
            )
            current_parent_id = self._row_id(parent_directory)
            current_path_ltree = self._row_value(parent_directory, "path_ltree")
            current_virtual_path = self._row_value(parent_directory, "virtual_path")

        existing_file = await self._get_child_entry(
            cursor,
            knowledge_base_id=knowledge_base_id,
            parent_entry_id=current_parent_id,
            name=path_segments[-1],
        )
        if existing_file is not None:
            raise ValueError(f"file path already exists: /{normalized_path}")

        label = self._path_label("f", len(path_segments), path_segments[-1])
        path_ltree = (
            f"{current_path_ltree}.{label}" if current_path_ltree is not None else label
        )
        virtual_path = (
            f"{current_virtual_path}/{path_segments[-1]}"
            if current_virtual_path != "/"
            else f"/{path_segments[-1]}"
        )
        await cursor.execute(
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
                virtual_path,
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
                %(parent_entry_id)s,
                'FILE',
                FALSE,
                %(name)s,
                %(path_ltree)s::ltree,
                %(depth)s,
                %(description)s,
                %(virtual_path)s,
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
            RETURNING kid, knowledge_base_id, parent_entry_id, path_ltree, name, entry_type, is_root, depth, virtual_path
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "parent_entry_id": current_parent_id,
                "name": path_segments[-1],
                "path_ltree": path_ltree,
                "depth": len(path_segments),
                "description": file_description,
                "virtual_path": virtual_path,
            },
        )
        return await cursor.fetchone()

    async def update_file_entry_storage(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        file_description: str | None,
        original_location: StorageLocation,
        file_size: int,
        mime_type: str,
        checksum: str,
    ) -> None:
        """Persist uploaded file object metadata on one file entry."""
        await cursor.execute(
            """
            UPDATE knowledge_fs_entry
            SET description = %(description)s,
                file_bucket_name = %(file_bucket_name)s,
                file_object_key = %(file_object_key)s,
                file_size = %(file_size)s,
                mime_type = %(mime_type)s,
                checksum = %(checksum)s,
                updated_at = NOW()
            WHERE kid = %(fs_entry_id)s
              AND entry_type = 'FILE'
              AND is_deleted = FALSE
            """,
            {
                "fs_entry_id": fs_entry_id,
                "description": file_description,
                "file_bucket_name": original_location.namespace,
                "file_object_key": original_location.key,
                "file_size": file_size,
                "mime_type": mime_type,
                "checksum": checksum,
            },
        )

    async def _insert_directory_entry(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        parent_entry_id: int | None,
        parent_path_ltree: str | None,
        parent_virtual_path: str,
        name: str,
        depth: int,
        description: str | None,
    ) -> dict[str, Any] | None:
        """Insert one directory node under an existing parent."""
        label = self._path_label("d", depth, name)
        path_ltree = (
            f"{parent_path_ltree}.{label}" if parent_path_ltree is not None else label
        )
        virtual_path = (
            f"{parent_virtual_path}/{name}"
            if parent_virtual_path != "/"
            else f"/{name}"
        )
        savepoint_name = "knowledge_fs_entry_directory_insert"
        params = {
            "knowledge_base_id": knowledge_base_id,
            "parent_entry_id": parent_entry_id,
            "name": name,
            "path_ltree": path_ltree,
            "depth": depth,
            "description": description,
            "virtual_path": virtual_path,
        }
        await cursor.execute(f"SAVEPOINT {savepoint_name}")
        try:
            await cursor.execute(
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
                    virtual_path,
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
                    %(description)s,
                    %(virtual_path)s,
                    NOW(),
                    NOW()
                )
                RETURNING kid, knowledge_base_id, parent_entry_id, path_ltree, name, entry_type, is_root, depth, virtual_path
                """,
                params,
            )
        except Exception as exc:
            await cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            await cursor.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            if not self._is_unique_violation(exc):
                raise
            return await self._get_existing_directory_after_conflict(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=parent_entry_id,
                name=name,
                virtual_path=virtual_path,
            )

        created = await cursor.fetchone()
        await cursor.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        if created is not None:
            return created

        return await self._get_existing_directory_after_conflict(
            cursor,
            knowledge_base_id=knowledge_base_id,
            parent_entry_id=parent_entry_id,
            name=name,
            virtual_path=virtual_path,
        )

    async def _get_existing_directory_after_conflict(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        parent_entry_id: int | None,
        name: str,
        virtual_path: str,
    ) -> dict[str, Any] | None:
        existing = await self._get_child_entry(
            cursor,
            knowledge_base_id=knowledge_base_id,
            parent_entry_id=parent_entry_id,
            name=name,
        )
        if existing is not None and existing.get("entry_type") == "DIRECTORY":
            return existing
        raise ValueError(f"directory path already exists: {virtual_path.lstrip('/')}")

    @staticmethod
    def _is_unique_violation(exc: Exception) -> bool:
        return (
            getattr(exc, "sqlstate", None) == "23505"
            or getattr(exc, "pgcode", None) == "23505"
        )

    async def _lock_entry_tree_for_write(self, cursor: Any) -> None:
        await cursor.execute(
            "LOCK TABLE knowledge_fs_entry IN SHARE ROW EXCLUSIVE MODE"
        )

    async def get_directory_by_path(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ) -> dict[str, Any] | None:
        """Look up one directory entry by its knowledge-base-relative path."""
        path_segments = [
            segment for segment in full_path.strip("/").split("/") if segment
        ]
        if not path_segments:
            return None
        current_parent_id: int | None = None
        current: dict[str, Any] | None = None
        for segment in path_segments:
            current = await self._get_child_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=current_parent_id,
                name=segment,
            )
            if current is None:
                return None
            current_parent_id = self._row_id(current)
        return current if current.get("entry_type") == "DIRECTORY" else None

    async def get_file_by_path(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ) -> dict[str, Any] | None:
        """Look up one file entry by its knowledge-base-relative path."""
        path_segments = [
            segment for segment in full_path.strip("/").split("/") if segment
        ]
        if not path_segments:
            return None
        current_parent_id: int | None = None
        current: dict[str, Any] | None = None
        for segment in path_segments:
            current = await self._get_child_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                parent_entry_id=current_parent_id,
                name=segment,
            )
            if current is None:
                return None
            current_parent_id = self._row_id(current)
        if current is None or current.get("entry_type") != "FILE":
            return None
        return await self._get_entry_by_id(cursor, entry_id=self._row_id(current))

    async def get_file_reference_target_by_path(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ) -> dict[str, Any] | None:
        """Look up one file entry eligible as a stable Markdown reference target."""
        return await self.get_file_by_path(
            cursor,
            knowledge_base_id=knowledge_base_id,
            full_path=full_path,
        )

    async def list_children_by_parent_entry_id(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        parent_entry_id: int | None,
    ) -> list[dict[str, Any]]:
        """List direct children under one knowledge-base-relative directory."""
        if parent_entry_id is None:
            await cursor.execute(
                """
                SELECT
                    fs.kid,
                    fs.knowledge_base_id,
                    fs.parent_entry_id,
                    fs.name,
                    CASE
                        WHEN fs.entry_type = 'DIRECTORY' THEN 'directory'
                        ELSE 'file'
                    END AS type,
                    CASE
                        WHEN fs.entry_type = 'DIRECTORY' THEN 0
                        ELSE COALESCE(fs.file_size, 0)
                    END AS size
                FROM knowledge_fs_entry fs
                WHERE fs.knowledge_base_id = %(knowledge_base_id)s
                  AND fs.parent_entry_id IS NULL
                  AND fs.is_deleted = FALSE
                ORDER BY
                    CASE WHEN fs.entry_type = 'DIRECTORY' THEN 0 ELSE 1 END,
                    lower(fs.name)
                """,
                {"knowledge_base_id": knowledge_base_id},
            )
        else:
            await cursor.execute(
                """
                SELECT
                    fs.kid,
                    fs.knowledge_base_id,
                    fs.parent_entry_id,
                    fs.name,
                    CASE
                        WHEN fs.entry_type = 'DIRECTORY' THEN 'directory'
                        ELSE 'file'
                    END AS type,
                    CASE
                        WHEN fs.entry_type = 'DIRECTORY' THEN 0
                        ELSE COALESCE(fs.file_size, 0)
                    END AS size
                FROM knowledge_fs_entry fs
                WHERE fs.knowledge_base_id = %(knowledge_base_id)s
                  AND fs.parent_entry_id = %(parent_entry_id)s
                  AND fs.is_deleted = FALSE
                ORDER BY
                    CASE WHEN fs.entry_type = 'DIRECTORY' THEN 0 ELSE 1 END,
                    lower(fs.name)
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "parent_entry_id": parent_entry_id,
                },
            )
        return await self._fetchall(cursor)

    async def _get_entry_by_id(
        self, cursor: Any, *, entry_id: int
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            SELECT
                kid,
                knowledge_base_id,
                parent_entry_id,
                path_ltree,
                name,
                entry_type,
                is_root,
                depth,
                description,
                file_bucket_name,
                file_object_key,
                markdown_bucket_name,
                markdown_object_key,
                file_size,
                mime_type,
                checksum,
                virtual_path,
                created_at,
                updated_at
            FROM knowledge_fs_entry
            WHERE kid = %(entry_id)s
              AND is_deleted = FALSE
            """,
            {"entry_id": entry_id},
        )
        return await cursor.fetchone()

    async def _get_child_entry(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        parent_entry_id: int | None,
        name: str,
    ) -> dict[str, Any] | None:
        if parent_entry_id is None:
            await cursor.execute(
                """
                SELECT
                    kid,
                    knowledge_base_id,
                    parent_entry_id,
                    path_ltree,
                    name,
                    entry_type,
                    is_root,
                    depth,
                    description,
                    virtual_path,
                    file_bucket_name,
                    file_object_key,
                    markdown_bucket_name,
                    markdown_object_key,
                    file_size,
                    mime_type,
                    checksum
                FROM knowledge_fs_entry
                WHERE knowledge_base_id = %(knowledge_base_id)s
                  AND parent_entry_id IS NULL
                  AND name = %(name)s
                  AND is_deleted = FALSE
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "name": name,
                },
            )
        else:
            await cursor.execute(
                """
                SELECT
                    kid,
                    knowledge_base_id,
                    parent_entry_id,
                    path_ltree,
                    name,
                    entry_type,
                    is_root,
                    depth,
                    description,
                    virtual_path,
                    file_bucket_name,
                    file_object_key,
                    markdown_bucket_name,
                    markdown_object_key,
                    file_size,
                    mime_type,
                    checksum
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
        return await cursor.fetchone()

    async def soft_delete_by_knowledge_base_id(
        self, cursor: Any, *, knowledge_base_id: int
    ) -> None:
        """Logically delete all filesystem entries under one knowledge base."""
        await cursor.execute(
            """
            UPDATE knowledge_fs_entry
            SET is_deleted = TRUE,
                updated_at = NOW()
            WHERE knowledge_base_id = %(knowledge_base_id)s
            """,
            {"knowledge_base_id": knowledge_base_id},
        )

    async def soft_delete_file_entry(
        self, cursor: Any, *, knowledge_base_id: int, fs_entry_id: int
    ) -> None:
        """Logically delete one file entry by id."""
        await cursor.execute(
            """
            UPDATE knowledge_fs_entry
            SET is_deleted = TRUE,
                updated_at = NOW()
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND kid = %(fs_entry_id)s
            """,
            {"knowledge_base_id": knowledge_base_id, "fs_entry_id": fs_entry_id},
        )

    async def list_subtree_entry_ids(
        self, cursor: Any, *, knowledge_base_id: int, root_fs_entry_id: int
    ) -> list[int]:
        """List filesystem entry ids within one directory subtree, including the root."""
        await cursor.execute(
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
        return [int(row["kid"]) for row in await self._fetchall(cursor)]

    async def soft_delete_subtree(
        self, cursor: Any, *, knowledge_base_id: int, root_fs_entry_id: int
    ) -> None:
        """Logically delete one directory subtree, including the root entry."""
        await cursor.execute(
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

    async def get_entry_by_id(
        self, cursor: Any, *, entry_id: int
    ) -> dict[str, Any] | None:
        """Fetch one filesystem entry by id."""
        return await self._get_entry_by_id(cursor, entry_id=entry_id)

    async def get_child_entry(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        parent_entry_id: int | None,
        name: str,
    ) -> dict[str, Any] | None:
        """Fetch one direct child entry by parent and name."""
        return await self._get_child_entry(
            cursor,
            knowledge_base_id=knowledge_base_id,
            parent_entry_id=parent_entry_id,
            name=name,
        )

    async def get_virtual_path_by_entry_id(
        self, cursor: Any, *, entry_id: int
    ) -> str | None:
        """Build the virtual path for one filesystem entry, excluding the KB root name."""
        await cursor.execute(
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
        row = await cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            return (
                row.get("coalesce")
                or row.get("full_path")
                or next(iter(row.values()), None)
            )
        return str(row[0]) if row else None

    async def rename_entry(self, cursor: Any, *, entry_id: int, new_name: str) -> None:
        """Rename one filesystem entry and rebuild its subtree path prefix."""
        await cursor.execute(
            """
            SELECT kid, parent_entry_id, path_ltree, depth, virtual_path
            FROM knowledge_fs_entry
            WHERE kid = %(entry_id)s
              AND is_deleted = FALSE
            """,
            {"entry_id": entry_id},
        )
        target = await cursor.fetchone()
        if target is None:
            return

        current_path_ltree = str(self._row_value(target, "path_ltree"))
        depth = int(self._row_value(target, "depth"))
        current_virtual_path = str(self._row_value(target, "virtual_path"))
        current_parent_path = ".".join(current_path_ltree.split(".")[:-1])
        new_label = self._path_label("d", depth, new_name)
        new_path_ltree = (
            f"{current_parent_path}.{new_label}" if current_parent_path else new_label
        )
        await cursor.execute(
            """
            UPDATE knowledge_fs_entry fs
            SET name = CASE
                    WHEN fs.kid = %(entry_id)s THEN %(new_name)s
                    ELSE fs.name
                END,
                path_ltree = text2ltree(
                    %(new_path_ltree)s
                    || COALESCE(
                        substring(
                            fs.path_ltree::text
                            FROM char_length(%(current_path_ltree)s) + 1
                        ),
                        ''
                    )
                ),
                updated_at = NOW()
            WHERE fs.path_ltree <@ %(current_path_ltree)s::ltree
              AND fs.is_deleted = FALSE
            """,
            {
                "entry_id": entry_id,
                "new_name": new_name,
                "new_path_ltree": new_path_ltree,
                "current_path_ltree": current_path_ltree,
            },
        )

        current_parent_virtual_path = (
            "/".join(current_virtual_path.split("/")[:-1]) or "/"
        )
        new_virtual_path = (
            f"{current_parent_virtual_path}/{new_name}"
            if current_parent_virtual_path != "/"
            else f"/{new_name}"
        )

        # Update the entry itself
        await cursor.execute(
            """
            UPDATE knowledge_fs_entry
            SET virtual_path = %(new_virtual_path)s,
                updated_at = NOW()
            WHERE kid = %(entry_id)s
              AND is_deleted = FALSE
            """,
            {
                "entry_id": entry_id,
                "new_virtual_path": new_virtual_path,
            },
        )

        # Update descendants: replace old prefix with new prefix
        await cursor.execute(
            """
            UPDATE knowledge_fs_entry
            SET virtual_path = %(new_virtual_path)s
                         || substring(virtual_path FROM char_length(%(current_virtual_path)s) + 1),
                updated_at = NOW()
            WHERE knowledge_base_id = (
                    SELECT knowledge_base_id FROM knowledge_fs_entry WHERE kid = %(entry_id)s
                  )
              AND virtual_path LIKE %(current_virtual_path_prefix)s
              AND is_deleted = FALSE
            """,
            {
                "entry_id": entry_id,
                "new_virtual_path": new_virtual_path,
                "current_virtual_path": current_virtual_path,
                "current_virtual_path_prefix": current_virtual_path + "/%",
            },
        )

    async def _fetchall(self, cursor: Any) -> list[dict[str, Any]]:
        return list(await cursor.fetchall())

    async def update_markdown_metadata(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        markdown_location: StorageLocation,
        line_count: int,
    ) -> None:
        """Update the markdown sidecar metadata on a file entry."""
        await cursor.execute(
            """
            UPDATE knowledge_fs_entry
            SET markdown_bucket_name = %(markdown_bucket_name)s,
                markdown_object_key = %(markdown_object_key)s,
                line_count = %(line_count)s,
                updated_at = NOW()
            WHERE kid = %(fs_entry_id)s
            """,
            {
                "fs_entry_id": fs_entry_id,
                "markdown_bucket_name": markdown_location.namespace,
                "markdown_object_key": markdown_location.key,
                "line_count": line_count,
            },
        )

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

    async def list_file_entries_in_subtree(
        self, cursor, *, knowledge_base_id: int, root_fs_entry_id: int
    ) -> list[dict[str, Any]]:
        """List all file entries (with storage locators) in one directory subtree."""
        await cursor.execute(
            """SELECT fs.kid, fs.virtual_path, fs.file_bucket_name, fs.file_object_key, fs.markdown_bucket_name, fs.markdown_object_key
            FROM knowledge_fs_entry fs JOIN knowledge_fs_entry root ON root.kid = %(root_fs_entry_id)s
            WHERE fs.knowledge_base_id = %(knowledge_base_id)s AND fs.is_deleted = FALSE
            AND fs.entry_type = 'FILE' AND fs.path_ltree <@ root.path_ltree ORDER BY fs.kid""",
            {
                "knowledge_base_id": knowledge_base_id,
                "root_fs_entry_id": root_fs_entry_id,
            },
        )
        return [dict(row) for row in await self._fetchall(cursor)]

    async def list_file_entries_by_knowledge_base_id(
        self, cursor, *, knowledge_base_id: int
    ) -> list[dict[str, Any]]:
        """List all non-deleted file entries (with storage locators) for one knowledge base."""
        await cursor.execute(
            """SELECT fs.kid, fs.virtual_path, fs.file_bucket_name, fs.file_object_key,
                      fs.markdown_bucket_name, fs.markdown_object_key, fs.mime_type
            FROM knowledge_fs_entry fs
            WHERE fs.knowledge_base_id = %(knowledge_base_id)s
              AND fs.is_deleted = FALSE
              AND fs.entry_type = 'FILE'
            ORDER BY fs.kid""",
            {"knowledge_base_id": knowledge_base_id},
        )
        return [dict(row) for row in await self._fetchall(cursor)]

    async def update_file_entry_locations(
        self,
        cursor,
        *,
        fs_entry_id: int,
        original_location: StorageLocation | None,
        markdown_location: StorageLocation | None,
    ) -> None:
        set_clauses = []
        params = {"fs_entry_id": fs_entry_id}
        if original_location is not None:
            set_clauses.extend(
                [
                    "file_bucket_name = %(file_bucket_name)s",
                    "file_object_key = %(file_object_key)s",
                ]
            )
            params.update(
                file_bucket_name=original_location.namespace,
                file_object_key=original_location.key,
            )
        if markdown_location is not None:
            set_clauses.extend(
                [
                    "markdown_bucket_name = %(markdown_bucket_name)s",
                    "markdown_object_key = %(markdown_object_key)s",
                ]
            )
            params.update(
                markdown_bucket_name=markdown_location.namespace,
                markdown_object_key=markdown_location.key,
            )
        if not set_clauses:
            return
        set_clauses.append("updated_at = NOW()")
        await cursor.execute(
            f"UPDATE knowledge_fs_entry SET {', '.join(set_clauses)} WHERE kid = %(fs_entry_id)s AND is_deleted = FALSE",
            params,
        )
