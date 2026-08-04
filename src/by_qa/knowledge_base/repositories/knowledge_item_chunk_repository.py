"""Persistence helpers for knowledge_item_chunk and embedding rows."""

from typing import Any

from by_qa.knowledge_common.text_segmentation import segment_for_fts


class KnowledgeItemChunkRepository:
    """Repository for chunks and dynamic vector records."""

    def __init__(self, embedding_table_name: str):
        self.embedding_table_name = embedding_table_name

    async def get_build_result_summary(
        self, cursor: Any, *, fs_entry_id: int
    ) -> dict[str, Any]:
        """Return chunk, embedding, and retrieval-index counts for one file."""
        await cursor.execute(
            f"""
            SELECT
                COUNT(c.kid)::int AS chunk_count,
                COUNT(e.chunk_id)::int AS embedded_chunk_count,
                COUNT(r.chunk_id)::int AS indexed_chunk_count
            FROM knowledge_chunk c
            LEFT JOIN {self.embedding_table_name} e ON e.chunk_id = c.kid
            LEFT JOIN knowledge_chunk_retrieval_mv r ON r.chunk_id = c.kid
            WHERE c.fs_entry_id = %(fs_entry_id)s
            """,
            {"fs_entry_id": fs_entry_id},
        )
        row = await cursor.fetchone()
        return row or {
            "chunk_count": 0,
            "embedded_chunk_count": 0,
            "indexed_chunk_count": 0,
        }

    async def list_build_result_chunks(
        self,
        cursor: Any,
        *,
        fs_entry_id: int,
        offset: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return a page of chunks with embedding and retrieval-index flags."""
        await cursor.execute(
            f"""
            SELECT
                c.chunk_no,
                c.start_line,
                c.end_line,
                c.chunk_text,
                (e.chunk_id IS NOT NULL) AS has_embedding,
                (r.chunk_id IS NOT NULL) AS retrieval_indexed
            FROM knowledge_chunk c
            LEFT JOIN {self.embedding_table_name} e ON e.chunk_id = c.kid
            LEFT JOIN knowledge_chunk_retrieval_mv r ON r.chunk_id = c.kid
            WHERE c.fs_entry_id = %(fs_entry_id)s
            ORDER BY c.chunk_no ASC
            OFFSET %(offset)s
            LIMIT %(limit)s
            """,
            {
                "fs_entry_id": fs_entry_id,
                "offset": offset,
                "limit": limit,
            },
        )
        return list(await cursor.fetchall())

    async def delete_for_fs_entry(self, cursor: Any, *, fs_entry_id: int) -> None:
        """Delete a file's embeddings before deleting its chunk rows."""
        await cursor.execute(
            f"""
            DELETE FROM {self.embedding_table_name}
            WHERE chunk_id IN (
                SELECT kid FROM knowledge_chunk WHERE fs_entry_id = %(fs_entry_id)s
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
                    %(chunk_text)s,
                    to_tsvector('simple', %(segmented_text)s),
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
                    "chunk_text": chunk["chunk_text"],
                    "segmented_text": segment_for_fts(chunk["chunk_text"]),
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
                    to_tsvector('simple', %(segmented_text)s),
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
                    "segmented_text": segment_for_fts(chunk["chunk_text"]),
                },
            )
            row = await cursor.fetchone()
            if row is not None:
                created_rows.append(row)
        return created_rows
