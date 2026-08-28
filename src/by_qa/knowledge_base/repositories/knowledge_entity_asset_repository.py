"""Persistence for durable KnowledgeEntity names and model-specific vectors."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

_SAFE_TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class KnowledgeEntityAssetRepository:
    """Store canonical entities, aliases, file anchors, and vector projections."""

    def __init__(self, entity_embedding_table_name: str):
        if not _SAFE_TABLE_NAME.fullmatch(entity_embedding_table_name):
            raise ValueError("entity embedding table name must be SQL-safe")
        self.entity_embedding_table_name = entity_embedding_table_name

    async def get_by_id(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        entity_id: int,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        await cursor.execute(
            f"""
            SELECT e.*,
                   COALESCE((
                       SELECT array_agg(a.entity_name ORDER BY a.kid)
                       FROM knowledge_entity a
                       WHERE a.canonical_entity_id = e.kid
                         AND a.name_role = 'alias'
                         AND a.object_kind = 'ENTITY'
                   ), ARRAY[]::text[]) AS aliases
            FROM knowledge_entity e
            WHERE e.knowledge_base_id = %(knowledge_base_id)s
              AND e.kid = %(entity_id)s
              AND e.object_kind = 'ENTITY'
            {"FOR UPDATE" if for_update else ""}
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "entity_id": entity_id,
            },
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def get_by_fs_entry_id(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
    ) -> dict[str, Any] | None:
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_entity
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND fs_entry_id = %(fs_entry_id)s
              AND name_role = 'canonical'
              AND object_kind = 'ENTITY'
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
            },
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def resolve_exact(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        normalized_surfaces: Sequence[str],
    ) -> list[dict[str, Any]]:
        if not normalized_surfaces:
            return []
        await cursor.execute(
            """
            SELECT
                canonical.kid AS resolved_entity_id,
                canonical.knowledge_base_id,
                canonical.fs_entry_id,
                canonical.entity_name AS canonical_entity_name,
                canonical.subject_entity_id,
                canonical.entity_type,
                canonical.description,
                matched.normalized_entity_name AS matched_normalized_surface,
                matched.name_role AS matched_name_role,
                matched.entity_name AS matched_surface,
                COALESCE((
                    SELECT array_agg(a.entity_name ORDER BY a.kid)
                    FROM knowledge_entity a
                    WHERE a.canonical_entity_id = canonical.kid
                      AND a.name_role = 'alias'
                      AND a.object_kind = 'ENTITY'
                ), ARRAY[]::text[]) AS aliases
            FROM knowledge_entity matched
            JOIN knowledge_entity canonical
              ON canonical.kid = COALESCE(matched.canonical_entity_id, matched.kid)
             AND canonical.knowledge_base_id = matched.knowledge_base_id
             AND canonical.name_role = 'canonical'
             AND canonical.object_kind = 'ENTITY'
            WHERE matched.knowledge_base_id = %(knowledge_base_id)s
              AND matched.object_kind = 'ENTITY'
              AND matched.normalized_entity_name = ANY(%(normalized_surfaces)s)
            ORDER BY matched.normalized_entity_name, canonical.kid, matched.kid
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "normalized_surfaces": list(dict.fromkeys(normalized_surfaces)),
            },
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def create_canonical(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        entity_name: str,
        normalized_entity_name: str,
        subject_entity_id: int | None,
        entity_type: str | None,
        description: str | None,
        fs_entry_id: int | None = None,
    ) -> dict[str, Any]:
        await cursor.execute(
            """
            INSERT INTO knowledge_entity (
                knowledge_base_id,
                fs_entry_id,
                canonical_entity_id,
                name_role,
                entity_name,
                normalized_entity_name,
                subject_entity_id,
                entity_type,
                description,
                object_kind
            )
            VALUES (
                %(knowledge_base_id)s,
                %(fs_entry_id)s,
                NULL,
                'canonical',
                %(entity_name)s,
                %(normalized_entity_name)s,
                %(subject_entity_id)s,
                %(entity_type)s,
                %(description)s,
                'ENTITY'
            )
            RETURNING *
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "entity_name": entity_name,
                "normalized_entity_name": normalized_entity_name,
                "subject_entity_id": subject_entity_id,
                "entity_type": entity_type,
                "description": description,
            },
        )
        return dict(await cursor.fetchone())

    async def add_alias(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        canonical_entity_id: int,
        alias: str,
        normalized_alias: str,
    ) -> dict[str, Any]:
        await cursor.execute(
            """
            UPDATE knowledge_entity
               SET entity_name = %(alias)s,
                   updated_at = NOW()
             WHERE canonical_entity_id = %(canonical_entity_id)s
               AND normalized_entity_name = %(normalized_alias)s
               AND name_role = 'alias'
               AND object_kind = 'ENTITY'
            RETURNING *
            """,
            {
                "canonical_entity_id": canonical_entity_id,
                "alias": alias,
                "normalized_alias": normalized_alias,
            },
        )
        existing = await cursor.fetchone()
        if existing is None:
            await cursor.execute(
                """
                INSERT INTO knowledge_entity (
                    knowledge_base_id,
                canonical_entity_id,
                name_role,
                entity_name,
                normalized_entity_name,
                object_kind
                )
                VALUES (
                    %(knowledge_base_id)s,
                    %(canonical_entity_id)s,
                    'alias',
                    %(alias)s,
                    %(normalized_alias)s,
                    'ENTITY'
                )
                RETURNING *
                """,
                {
                    "knowledge_base_id": knowledge_base_id,
                    "canonical_entity_id": canonical_entity_id,
                    "alias": alias,
                    "normalized_alias": normalized_alias,
                },
            )
            existing = await cursor.fetchone()
        inserted = dict(existing)
        await cursor.execute(
            """
            UPDATE knowledge_entity
               SET updated_at = NOW()
             WHERE kid = %(canonical_entity_id)s
               AND object_kind = 'ENTITY'
            """,
            {"canonical_entity_id": canonical_entity_id},
        )
        return inserted

    async def attach_fs_entry(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        entity_id: int,
        fs_entry_id: int,
    ) -> None:
        await cursor.execute(
            """
            UPDATE knowledge_entity
               SET fs_entry_id = %(fs_entry_id)s, updated_at = NOW()
             WHERE kid = %(entity_id)s
               AND knowledge_base_id = %(knowledge_base_id)s
               AND name_role = 'canonical'
               AND object_kind = 'ENTITY'
            """,
            {
                "entity_id": entity_id,
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
            },
        )

    async def clear_fs_entry_ids(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_ids: Sequence[int],
    ) -> int:
        if not fs_entry_ids:
            return 0
        await cursor.execute(
            """
            UPDATE knowledge_entity
               SET fs_entry_id = NULL, updated_at = NOW()
             WHERE knowledge_base_id = %(knowledge_base_id)s
               AND fs_entry_id = ANY(%(fs_entry_ids)s)
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_ids": list(fs_entry_ids),
            },
        )
        return int(cursor.rowcount or 0)

    async def delete_by_knowledge_base_id(
        self, cursor: Any, *, knowledge_base_id: int
    ) -> int:
        await cursor.execute(
            """
            DELETE FROM knowledge_entity
            WHERE knowledge_base_id = %(knowledge_base_id)s
            """,
            {"knowledge_base_id": knowledge_base_id},
        )
        return int(cursor.rowcount or 0)

    async def upsert_topic(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        owner_entity_id: int,
        name: str,
        normalized_name: str,
    ) -> dict[str, Any]:
        """Reuse an exactly equal local Topic name inside one Entity owner."""

        await cursor.execute(
            """
            SELECT kid
            FROM knowledge_entity
            WHERE kid = %(owner_entity_id)s
              AND knowledge_base_id = %(knowledge_base_id)s
              AND object_kind = 'ENTITY'
              AND name_role = 'canonical'
            FOR UPDATE
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "owner_entity_id": owner_entity_id,
            },
        )
        if await cursor.fetchone() is None:
            raise ValueError("Topic owner must be a canonical Entity in the same KB")
        await cursor.execute(
            """
            SELECT kid, entity_name, subject_entity_id
            FROM knowledge_entity
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND object_kind = 'TOPIC'
              AND subject_entity_id = %(owner_entity_id)s
              AND normalized_entity_name = %(normalized_name)s
            FOR UPDATE
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "owner_entity_id": owner_entity_id,
                "normalized_name": normalized_name,
            },
        )
        existing = await cursor.fetchone()
        if existing is not None:
            return dict(existing)
        await cursor.execute(
            """
            INSERT INTO knowledge_entity (
                knowledge_base_id,
                fs_entry_id,
                canonical_entity_id,
                name_role,
                entity_name,
                normalized_entity_name,
                subject_entity_id,
                entity_type,
                object_kind
            )
            SELECT
                %(knowledge_base_id)s,
                NULL,
                NULL,
                'canonical',
                %(name)s,
                %(normalized_name)s,
                owner.kid,
                NULL,
                'TOPIC'
            FROM knowledge_entity owner
            WHERE owner.kid = %(owner_entity_id)s
              AND owner.knowledge_base_id = %(knowledge_base_id)s
              AND owner.object_kind = 'ENTITY'
              AND owner.name_role = 'canonical'
            RETURNING kid, entity_name, subject_entity_id
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "owner_entity_id": owner_entity_id,
                "name": name,
                "normalized_name": normalized_name,
            },
        )
        created = await cursor.fetchone()
        if created is None:
            raise ValueError("Topic owner must be a canonical Entity in the same KB")
        return dict(created)

    async def list_topics_for_entity_file(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        updated_after: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """List an Entity's Topics in stable discovery order.

        ``updated_after`` limits incremental enrichment to Topics created or
        materially updated after the previous successful enrichment watermark.
        """

        topic_time_filter = ""
        params: dict[str, Any] = {
            "knowledge_base_id": knowledge_base_id,
            "fs_entry_id": fs_entry_id,
        }
        if updated_after is not None:
            topic_time_filter = "AND topic.updated_at > %(updated_after)s"
            params["updated_after"] = updated_after

        await cursor.execute(
            f"""
            SELECT topic.kid,
                   topic.entity_name,
                   topic.normalized_entity_name,
                   topic.created_at,
                   topic.updated_at
            FROM knowledge_entity owner
            JOIN knowledge_entity topic
              ON topic.subject_entity_id = owner.kid
             AND topic.knowledge_base_id = owner.knowledge_base_id
             AND topic.object_kind = 'TOPIC'
             AND topic.name_role = 'canonical'
            WHERE owner.knowledge_base_id = %(knowledge_base_id)s
              AND owner.fs_entry_id = %(fs_entry_id)s
              AND owner.object_kind = 'ENTITY'
              AND owner.name_role = 'canonical'
              {topic_time_filter}
            ORDER BY topic.kid
            """,
            params,
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def list_direct_children(
        self, cursor: Any, *, knowledge_base_id: int, subject_entity_id: int
    ) -> list[dict[str, Any]]:
        await cursor.execute(
            """
            SELECT kid, entity_name
            FROM knowledge_entity
            WHERE knowledge_base_id = %(knowledge_base_id)s
              AND subject_entity_id = %(subject_entity_id)s
              AND name_role = 'canonical'
              AND object_kind = 'ENTITY'
            ORDER BY kid
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "subject_entity_id": subject_entity_id,
            },
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def delete_entity(self, cursor: Any, *, entity_id: int) -> int:
        await cursor.execute(
            """
            DELETE FROM knowledge_entity
            WHERE kid = %(entity_id)s
              AND object_kind = 'ENTITY'
            """,
            {"entity_id": entity_id},
        )
        return int(cursor.rowcount or 0)

    async def delete_alias(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        canonical_entity_id: int,
        alias_id: int,
    ) -> int:
        await cursor.execute(
            """
            DELETE FROM knowledge_entity
            WHERE kid = %(alias_id)s
              AND knowledge_base_id = %(knowledge_base_id)s
              AND canonical_entity_id = %(canonical_entity_id)s
              AND name_role = 'alias'
              AND object_kind = 'ENTITY'
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "canonical_entity_id": canonical_entity_id,
                "alias_id": alias_id,
            },
        )
        deleted = int(cursor.rowcount or 0)
        if deleted:
            await cursor.execute(
                """
                UPDATE knowledge_entity
                   SET updated_at = NOW()
                 WHERE kid = %(canonical_entity_id)s
                   AND object_kind = 'ENTITY'
                """,
                {"canonical_entity_id": canonical_entity_id},
            )
        return deleted

    async def advisory_lock_surface(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        normalized_surface: str,
    ) -> None:
        await cursor.execute(
            """
            SELECT pg_advisory_xact_lock(
                hashtext(%(namespace)s),
                hashtext(%(surface)s)
            )
            """,
            {
                "namespace": f"knowledge-entity:{knowledge_base_id}",
                "surface": normalized_surface,
            },
        )

    async def get_embedding_hashes(
        self, cursor: Any, *, entity_id: int
    ) -> dict[str, str]:
        await cursor.execute(
            f"""
            SELECT representation, source_content_hash
            FROM {self.entity_embedding_table_name}
            WHERE entity_id = %(entity_id)s
            """,
            {"entity_id": entity_id},
        )
        return {
            str(row["representation"]): str(row["source_content_hash"])
            for row in await cursor.fetchall()
        }

    async def upsert_embedding(
        self,
        cursor: Any,
        *,
        entity_id: int,
        representation: str,
        source_content_hash: str,
        embedding: Sequence[float],
    ) -> None:
        await cursor.execute(
            """
            SELECT 1
            FROM knowledge_entity
            WHERE kid = %(entity_id)s
              AND name_role = 'canonical'
              AND object_kind = 'ENTITY'
              AND object_kind = 'ENTITY'
            """,
            {"entity_id": entity_id},
        )
        if await cursor.fetchone() is None:
            raise ValueError("entity embedding requires a canonical entity")
        vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"
        await cursor.execute(
            f"""
            UPDATE {self.entity_embedding_table_name}
               SET source_content_hash = %(source_content_hash)s,
                   embedding = %(embedding)s,
                   updated_at = NOW()
             WHERE entity_id = %(entity_id)s
               AND representation = %(representation)s
            """,
            {
                "entity_id": entity_id,
                "representation": representation,
                "source_content_hash": source_content_hash,
                "embedding": vector_literal,
            },
        )
        if int(cursor.rowcount or 0) > 0:
            return
        await cursor.execute(
            f"""
            INSERT INTO {self.entity_embedding_table_name} (
                entity_id, representation, source_content_hash, embedding
            )
            VALUES (
                %(entity_id)s,
                %(representation)s,
                %(source_content_hash)s,
                %(embedding)s
            )
            """,
            {
                "entity_id": entity_id,
                "representation": representation,
                "source_content_hash": source_content_hash,
                "embedding": vector_literal,
            },
        )

    async def delete_embeddings(
        self,
        cursor: Any,
        *,
        entity_id: int,
        representations: Sequence[str] | None = None,
    ) -> int:
        await cursor.execute(
            f"""
            DELETE FROM {self.entity_embedding_table_name}
            WHERE entity_id = %(entity_id)s
              AND (
                    %(representations)s::text[] IS NULL
                    OR representation = ANY(%(representations)s)
                  )
            """,
            {
                "entity_id": entity_id,
                "representations": (
                    list(representations) if representations is not None else None
                ),
            },
        )
        return int(cursor.rowcount or 0)

    async def search_similar(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        full_embedding: Sequence[float],
        subject_entity_id: int | None,
        entity_type: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        full_literal = "[" + ",".join(str(value) for value in full_embedding) + "]"
        await cursor.execute(
            f"""
            WITH scoped_entities AS MATERIALIZED (
                SELECT e.kid
                FROM knowledge_entity e
                WHERE e.knowledge_base_id = %(knowledge_base_id)s
                  AND e.name_role = 'canonical'
                  AND e.object_kind = 'ENTITY'
                  AND e.subject_entity_id IS NOT DISTINCT FROM %(subject_entity_id)s
                  AND (
                        %(entity_type)s::text IS NULL
                        OR e.entity_type IS NULL
                        OR e.entity_type = %(entity_type)s
                      )
            ), scored AS (
                SELECT v.entity_id,
                       1 - (v.embedding <=> %(full_embedding)s) AS score
                FROM scoped_entities scoped
                JOIN {self.entity_embedding_table_name} v
                  ON v.entity_id = scoped.kid
                 AND v.representation = 'full'
            )
            SELECT e.kid AS resolved_entity_id,
                   e.entity_name AS canonical_entity_name,
                   e.subject_entity_id,
                   e.entity_type,
                   e.description,
                   e.fs_entry_id,
                   scored.score,
                   COALESCE((
                       SELECT array_agg(a.entity_name ORDER BY a.kid)
                       FROM knowledge_entity a
                       WHERE a.canonical_entity_id = e.kid
                         AND a.name_role = 'alias'
                         AND a.object_kind = 'ENTITY'
                   ), ARRAY[]::text[]) AS aliases
            FROM scored
            JOIN knowledge_entity e ON e.kid = scored.entity_id
            ORDER BY scored.score DESC, e.kid
            LIMIT %(limit)s
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "subject_entity_id": subject_entity_id,
                "entity_type": entity_type,
                "full_embedding": full_literal,
                "limit": limit,
            },
        )
        return [dict(row) for row in await cursor.fetchall()]
