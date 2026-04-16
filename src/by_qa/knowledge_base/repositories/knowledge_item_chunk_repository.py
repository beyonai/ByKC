"""Persistence helpers for knowledge_item_chunk and embedding rows."""

from typing import Any


class KnowledgeItemChunkRepository:
    """Repository for chunks and dynamic vector records."""

    def __init__(self, embedding_table_name: str):
        self.embedding_table_name = embedding_table_name

    async def replace_for_version(
        self,
        cursor: Any,
        *,
        knowledge_item_id: int,
        knowledge_item_version_id: int,
        chunks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Replace chunk rows for one version and return their ids."""
        await cursor.execute(
            f"""
            DELETE FROM {self.embedding_table_name}
            WHERE chunk_id IN (
                SELECT kid
                FROM knowledge_item_chunk
                WHERE knowledge_item_version_id = %(knowledge_item_version_id)s
            )
            """,
            {"knowledge_item_version_id": knowledge_item_version_id},
        )
        await cursor.execute(
            """
            DELETE FROM knowledge_item_chunk
            WHERE knowledge_item_version_id = %(knowledge_item_version_id)s
            """,
            {"knowledge_item_version_id": knowledge_item_version_id},
        )
        created_rows: list[dict[str, Any]] = []
        for chunk in chunks:
            await cursor.execute(
                """
                INSERT INTO knowledge_item_chunk (
                    knowledge_item_id,
                    knowledge_item_version_id,
                    chunk_no,
                    start_line,
                    end_line,
                    char_start,
                    char_end,
                    chunk_text,
                    search_text,
                    created_at,
                    updated_at
                )
                VALUES (
                    %(knowledge_item_id)s,
                    %(knowledge_item_version_id)s,
                    %(chunk_no)s,
                    %(start_line)s,
                    %(end_line)s,
                    %(char_start)s,
                    %(char_end)s,
                    %(chunk_text)s,
                    to_tsvector('simple', %(chunk_text)s),
                    NOW(),
                    NOW()
                )
                RETURNING kid, chunk_no
                """,
                {
                    "knowledge_item_id": knowledge_item_id,
                    "knowledge_item_version_id": knowledge_item_version_id,
                    "chunk_no": chunk["chunk_no"],
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                    "char_start": chunk.get("char_start"),
                    "char_end": chunk.get("char_end"),
                    "chunk_text": chunk["chunk_text"],
                },
            )
            row = await cursor.fetchone()
            if row is not None:
                created_rows.append(row)
        return created_rows

    async def replace_embeddings(
        self, cursor: Any, *, embeddings: list[dict[str, Any]]
    ) -> None:
        """Replace chunk embeddings in the dynamic embedding table."""
        if not embeddings:
            return
        chunk_ids = [item["chunk_id"] for item in embeddings]
        await cursor.execute(
            f"""
            DELETE FROM {self.embedding_table_name}
            WHERE chunk_id = ANY(%(chunk_ids)s)
            """,
            {"chunk_ids": chunk_ids},
        )
        for item in embeddings:
            vector_literal = (
                "[" + ",".join(str(value) for value in item["embedding"]) + "]"
            )
            await cursor.execute(
                f"""
                INSERT INTO {self.embedding_table_name} (
                    chunk_id,
                    embedding,
                    created_at,
                    updated_at
                )
                VALUES (
                    %(chunk_id)s,
                    %(embedding)s,
                    NOW(),
                    NOW()
                )
                """,
                {"chunk_id": item["chunk_id"], "embedding": vector_literal},
            )

    async def replace_for_fs_entry(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        chunks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Replace chunk rows for one fs_entry and return their ids."""
        await cursor.execute(
            f"""
            DELETE FROM {self.embedding_table_name}
            WHERE chunk_id IN (
                SELECT kid
                FROM knowledge_chunk
                WHERE fs_entry_id = %(fs_entry_id)s
            )
            """,
            {"fs_entry_id": fs_entry_id},
        )
        await cursor.execute(
            """
            DELETE FROM knowledge_chunk
            WHERE fs_entry_id = %(fs_entry_id)s
            """,
            {"fs_entry_id": fs_entry_id},
        )
        created_rows: list[dict[str, Any]] = []
        for chunk in chunks:
            await cursor.execute(
                """
                INSERT INTO knowledge_chunk (
                    fs_entry_id,
                    chunk_no,
                    start_line,
                    end_line,
                    chunk_text,
                    search_text,
                    created_at,
                    updated_at
                )
                VALUES (
                    %(fs_entry_id)s,
                    %(chunk_no)s,
                    %(start_line)s,
                    %(end_line)s,
                    %(chunk_text)s,
                    to_tsvector('simple', %(chunk_text)s),
                    NOW(),
                    NOW()
                )
                RETURNING kid, chunk_no
                """,
                {
                    "fs_entry_id": fs_entry_id,
                    "chunk_no": chunk["chunk_no"],
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                    "chunk_text": chunk["chunk_text"],
                },
            )
            row = await cursor.fetchone()
            if row is not None:
                created_rows.append(row)
        return created_rows
