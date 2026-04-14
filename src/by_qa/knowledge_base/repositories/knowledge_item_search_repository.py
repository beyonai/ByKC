"""Persistence helpers for knowledge-base hybrid retrieval queries."""

from typing import Any


class KnowledgeItemSearchRepository:
    """Repository for text and vector candidate retrieval."""

    def __init__(self, embedding_table_name: str):
        self.embedding_table_name = embedding_table_name

    def search_text(
        self,
        cursor: Any,
        *,
        query: str,
        kb_codes: list[str],
        source_codes: list[str] | None,
        type_codes: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Search current-version chunks using full-text recall."""
        cursor.execute(
            """
            WITH filtered_chunks AS (
                SELECT
                    chunk_id,
                    kb_code,
                    knowledge_item_id,
                    item_code,
                    full_path,
                    version,
                    source_code,
                    type_code,
                    chunk_no,
                    chunk_text,
                    ts_rank_cd(
                        search_text,
                        plainto_tsquery('simple', %(query)s)
                    ) AS text_score
                FROM knowledge_item_chunk_retrieval_mv
                WHERE kb_code = ANY(%(kb_codes)s)
                  AND knowledge_base_status = 'ACTIVE'
                  AND knowledge_item_status = 'ACTIVE'
                  AND item_kind = 'FILE'
                  AND (
                        %(source_codes)s::text[] IS NULL
                        OR source_code = ANY(%(source_codes)s::text[])
                  )
                  AND (
                        %(type_codes)s::text[] IS NULL
                        OR type_code = ANY(%(type_codes)s::text[])
                  )
                  AND search_text @@ plainto_tsquery('simple', %(query)s)
            )
            SELECT
                chunk_id,
                kb_code,
                knowledge_item_id,
                item_code,
                full_path,
                version,
                source_code,
                type_code,
                chunk_no,
                chunk_text,
                text_score
            FROM filtered_chunks
            ORDER BY text_score DESC, chunk_id DESC
            LIMIT %(limit)s
            """,
            {
                "query": query,
                "kb_codes": kb_codes,
                "source_codes": source_codes,
                "type_codes": type_codes,
                "limit": limit,
            },
        )
        fetchall = getattr(cursor, "fetchall", None)
        return fetchall() if callable(fetchall) else []

    def search_vector(
        self,
        cursor: Any,
        *,
        query_embedding: list[float],
        kb_codes: list[str],
        source_codes: list[str] | None,
        type_codes: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Search current-version chunks using vector similarity recall."""
        vector_literal = "[" + ",".join(str(value) for value in query_embedding) + "]"
        cursor.execute(
            f"""
            SELECT
                r.chunk_id,
                r.kb_code,
                r.knowledge_item_id,
                r.item_code,
                r.full_path,
                r.version,
                r.source_code,
                r.type_code,
                r.chunk_no,
                r.chunk_text,
                1 - (e.embedding <=> %(query_embedding)s) AS vector_score
            FROM {self.embedding_table_name} e
            JOIN knowledge_item_chunk_retrieval_mv r
              ON r.chunk_id = e.chunk_id
            WHERE r.kb_code = ANY(%(kb_codes)s)
              AND r.knowledge_base_status = 'ACTIVE'
              AND r.knowledge_item_status = 'ACTIVE'
              AND r.item_kind = 'FILE'
              AND (
                    %(source_codes)s::text[] IS NULL
                    OR r.source_code = ANY(%(source_codes)s::text[])
              )
              AND (
                    %(type_codes)s::text[] IS NULL
                    OR r.type_code = ANY(%(type_codes)s::text[])
              )
            ORDER BY e.embedding <=> %(query_embedding)s ASC, r.chunk_id DESC
            LIMIT %(limit)s
            """,
            {
                "query_embedding": vector_literal,
                "kb_codes": kb_codes,
                "source_codes": source_codes,
                "type_codes": type_codes,
                "limit": limit,
            },
        )
        fetchall = getattr(cursor, "fetchall", None)
        return fetchall() if callable(fetchall) else []

    # ---- v2 methods: query knowledge_chunk_retrieval_mv (new table) ----

    def search_text_v2(
        self,
        cursor: Any,
        *,
        query: str,
        kb_codes: list[str],
        file_type_list: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Full-text recall against the new retrieval projection table."""
        cursor.execute(
            """
            SELECT
                r.chunk_id,
                kb.kid::text AS kb_code,
                r.full_path,
                r.chunk_no,
                r.start_line,
                r.end_line,
                r.chunk_text,
                ts_rank_cd(
                    r.search_text,
                    plainto_tsquery('simple', %(query)s)
                ) AS text_score
            FROM knowledge_chunk_retrieval_mv r
            JOIN knowledge_base kb
              ON kb.kid = r.knowledge_base_id
            WHERE kb.kid = ANY(%(kb_codes)s::bigint[])
              AND kb.is_deleted = FALSE
              AND (
                    %(file_type_list)s::text[] IS NULL
                    OR lower(
                        CASE
                            WHEN r.full_path LIKE '%%.%%'
                            THEN substring(r.full_path FROM '[^.]+$')
                            ELSE ''
                        END
                    ) = ANY(%(file_type_list)s::text[])
              )
              AND r.search_text @@ plainto_tsquery('simple', %(query)s)
            ORDER BY text_score DESC, r.chunk_id DESC
            LIMIT %(limit)s
            """,
            {
                "query": query,
                "kb_codes": kb_codes,
                "file_type_list": file_type_list,
                "limit": limit,
            },
        )
        fetchall = getattr(cursor, "fetchall", None)
        return fetchall() if callable(fetchall) else []

    def search_vector_v2(
        self,
        cursor: Any,
        *,
        query_embedding: list[float],
        kb_codes: list[str],
        file_type_list: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Vector similarity recall against the new retrieval projection table."""
        vector_literal = "[" + ",".join(str(value) for value in query_embedding) + "]"
        cursor.execute(
            f"""
            SELECT
                r.chunk_id,
                kb.kid::text AS kb_code,
                r.full_path,
                r.chunk_no,
                r.start_line,
                r.end_line,
                r.chunk_text,
                1 - (e.embedding <=> %(query_embedding)s) AS vector_score
            FROM {self.embedding_table_name} e
            JOIN knowledge_chunk_retrieval_mv r
              ON r.chunk_id = e.chunk_id
            JOIN knowledge_base kb
              ON kb.kid = r.knowledge_base_id
            WHERE kb.kid = ANY(%(kb_codes)s::bigint[])
              AND kb.is_deleted = FALSE
              AND (
                    %(file_type_list)s::text[] IS NULL
                    OR lower(
                        CASE
                            WHEN r.full_path LIKE '%%.%%'
                            THEN substring(r.full_path FROM '[^.]+$')
                            ELSE ''
                        END
                    ) = ANY(%(file_type_list)s::text[])
              )
            ORDER BY e.embedding <=> %(query_embedding)s ASC, r.chunk_id DESC
            LIMIT %(limit)s
            """,
            {
                "query_embedding": vector_literal,
                "kb_codes": kb_codes,
                "file_type_list": file_type_list,
                "limit": limit,
            },
        )
        fetchall = getattr(cursor, "fetchall", None)
        return fetchall() if callable(fetchall) else []
