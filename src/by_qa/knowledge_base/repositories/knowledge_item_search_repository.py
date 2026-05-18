"""Persistence helpers for knowledge-base hybrid retrieval queries."""

from typing import Any

# Path-derived file extension used by the legacy file_type_list filter.
# The retrieval projection only stores full_path, so the extension must
# be parsed at query time.
_PATH_EXTENSION_EXPR = """
    lower(
        CASE
            WHEN r.full_path LIKE '%%.%%'
            THEN substring(r.full_path FROM '[^.]+$')
            ELSE ''
        END
    )
"""

_FILE_TYPE_FILTER = f"""
    AND (
        %(file_type_list)s::text[] IS NULL
        OR {_PATH_EXTENSION_EXPR.strip()} = ANY(%(file_type_list)s::text[])
    )
"""

_CHUNK_COLUMNS = """
    r.chunk_id,
    kb.kid::text AS kb_code,
    r.full_path,
    r.chunk_no,
    r.start_line,
    r.end_line,
    r.chunk_text,
    r.fs_entry_id
"""


def _build_candidate_cte(where_sql: str) -> tuple[str, str]:
    """Compile the optional CTE that pre-filters fs_entry candidates.

    Returns (cte_clause, chunk_from). With a DSL filter, chunks are
    joined onto the candidate set so the heavy full-text or ANN scan
    only sees entries that already passed metadata filtering. Without
    one, chunks are scanned directly with kb-scoped predicates.

    The returned chunk_from never includes a WHERE clause — callers are
    responsible for appending ``WHERE`` (no-DSL path) or continuing with
    ``AND`` (DSL path already has no WHERE yet, so callers use ``WHERE``
    for both paths when the kb-scope predicate is the first condition).
    """
    if where_sql:
        cte = f"""
        WITH candidate_entries AS (
            SELECT fe.kid AS fs_entry_id
            FROM knowledge_fs_entry fe
            WHERE fe.knowledge_base_id = ANY(%(kb_codes)s::bigint[])
              AND fe.is_deleted = FALSE
              AND fe.entry_type = 'FILE'
              AND {where_sql}
        )
        """
        chunk_from = (
            "FROM candidate_entries c "
            "JOIN knowledge_chunk_retrieval_mv r "
            "ON r.fs_entry_id = c.fs_entry_id"
        )
        return cte, chunk_from
    return "", "FROM knowledge_chunk_retrieval_mv r"


async def _fetchall(cursor: Any) -> list[dict[str, Any]]:
    fetchall = getattr(cursor, "fetchall", None)
    return await fetchall() if callable(fetchall) else []


class KnowledgeItemSearchRepository:
    """Repository for text and vector candidate retrieval."""

    def __init__(self, embedding_table_name: str):
        self.embedding_table_name = embedding_table_name

    async def search_text(
        self,
        cursor: Any,
        *,
        query: str,
        kb_codes: list[str],
        limit: int,
        file_type_list: list[str] | None = None,
        where_sql: str = "",
        where_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text recall against the retrieval projection.

        file_type_list and DSL filtering can be combined; both are
        optional. With where_sql, fs_entry candidates are resolved via
        CTE before the full-text scan.
        """
        cte, chunk_from = _build_candidate_cte(where_sql)
        # Both paths use WHERE here: the DSL path has no WHERE yet (the
        # CTE already filtered by kb_codes), and the no-DSL path also
        # needs a fresh WHERE.  The kb-scope predicate is the first
        # condition in both cases.
        kb_scope = (
            "r.knowledge_base_id = ANY(%(kb_codes)s::bigint[])"
            if not where_sql
            else "TRUE"
        )
        sql = f"""
            {cte}
            SELECT
                {_CHUNK_COLUMNS},
                ts_rank_cd(
                    r.search_text,
                    plainto_tsquery('simple', %(query)s)
                ) AS text_score
            {chunk_from}
            JOIN knowledge_base kb ON kb.kid = r.knowledge_base_id
            WHERE {kb_scope}
              AND kb.is_deleted = FALSE
              {_FILE_TYPE_FILTER}
              AND r.search_text @@ plainto_tsquery('simple', %(query)s)
            ORDER BY text_score DESC, r.chunk_id DESC
            LIMIT %(limit)s
        """
        params = {
            **(where_params or {}),
            "query": query,
            "kb_codes": kb_codes,
            "file_type_list": file_type_list,
            "limit": limit,
        }
        await cursor.execute(sql, params)
        return await _fetchall(cursor)

    async def search_vector(
        self,
        cursor: Any,
        *,
        query_embedding: list[float],
        kb_codes: list[str],
        limit: int,
        file_type_list: list[str] | None = None,
        where_sql: str = "",
        where_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Vector similarity recall against the retrieval projection.

        Mirrors search_text: file_type_list and DSL filtering are
        independently optional, and a present DSL filter pushes
        fs_entry resolution into a CTE ahead of the ANN scan.
        """
        vector_literal = "[" + ",".join(str(value) for value in query_embedding) + "]"
        cte, chunk_from = _build_candidate_cte(where_sql)
        kb_scope = (
            "r.knowledge_base_id = ANY(%(kb_codes)s::bigint[])"
            if not where_sql
            else "TRUE"
        )
        sql = f"""
            {cte}
            SELECT
                {_CHUNK_COLUMNS},
                1 - (e.embedding <=> %(query_embedding)s) AS vector_score
            {chunk_from}
            JOIN {self.embedding_table_name} e ON e.chunk_id = r.chunk_id
            JOIN knowledge_base kb ON kb.kid = r.knowledge_base_id
            WHERE {kb_scope}
              AND kb.is_deleted = FALSE
              {_FILE_TYPE_FILTER}
            ORDER BY e.embedding <=> %(query_embedding)s ASC, r.chunk_id DESC
            LIMIT %(limit)s
        """
        params = {
            **(where_params or {}),
            "query_embedding": vector_literal,
            "kb_codes": kb_codes,
            "file_type_list": file_type_list,
            "limit": limit,
        }
        await cursor.execute(sql, params)
        return await _fetchall(cursor)
