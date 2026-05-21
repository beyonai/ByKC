"""Unit tests for knowledge_item_search_repository SQL shape."""

from __future__ import annotations

from typing import Any

import pytest

from by_qa.knowledge_base.repositories.knowledge_item_search_repository import (
    KnowledgeItemSearchRepository,
)


class _RecordingCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows or []
        self.executed_sql: str = ""
        self.executed_params: dict[str, Any] = {}

    async def execute(self, sql: str, params: dict[str, Any]):
        self.executed_sql = sql
        self.executed_params = params

    async def fetchall(self):
        return list(self.rows)


@pytest.mark.asyncio
async def test_search_text_uses_cte_when_dsl_filter_present():
    """DSL filter must resolve fs_entry candidates first, before full-text scan."""
    repo = KnowledgeItemSearchRepository(embedding_table_name="emb")
    cursor = _RecordingCursor()

    await repo.search_text(
        cursor,
        query="hello",
        kb_codes=["1"],
        where_sql="fe.name = %(dsl_p1)s",
        where_params={"dsl_p1": "x.md"},
        limit=10,
    )

    sql = cursor.executed_sql
    normalized = " ".join(sql.split())
    assert "WITH candidate_entries" in sql
    cte_idx = sql.index("candidate_entries")
    fts_idx = sql.index("plainto_tsquery")
    assert cte_idx < fts_idx, "CTE must come before full-text predicate"
    assert (
        "JOIN knowledge_chunk_retrieval_mv r ON r.fs_entry_id = c.fs_entry_id"
        in normalized
    )
    assert cursor.executed_params["dsl_p1"] == "x.md"
    assert cursor.executed_params["query"] == "hello"
    assert "segmented_query" in cursor.executed_params
    assert cursor.executed_params["kb_codes"] == ["1"]
    assert cursor.executed_params["limit"] == 10
    assert cursor.executed_params["file_type_list"] is None


@pytest.mark.asyncio
async def test_search_text_omits_cte_when_no_dsl_filter():
    """No DSL filter means no CTE; chunks are scanned directly."""
    repo = KnowledgeItemSearchRepository(embedding_table_name="emb")
    cursor = _RecordingCursor()

    await repo.search_text(
        cursor,
        query="hello",
        kb_codes=["1"],
        limit=10,
    )

    sql = cursor.executed_sql
    assert "candidate_entries" not in sql
    assert "plainto_tsquery" in sql


@pytest.mark.asyncio
async def test_search_text_combines_dsl_and_file_type_filter():
    """file_type_list and DSL filtering should compose without conflict."""
    repo = KnowledgeItemSearchRepository(embedding_table_name="emb")
    cursor = _RecordingCursor()

    await repo.search_text(
        cursor,
        query="hello",
        kb_codes=["1"],
        file_type_list=["md"],
        where_sql="fe.name = %(dsl_p1)s",
        where_params={"dsl_p1": "x.md"},
        limit=5,
    )

    sql = cursor.executed_sql
    assert "candidate_entries" in sql
    assert "file_type_list" in sql
    assert cursor.executed_params["file_type_list"] == ["md"]


@pytest.mark.asyncio
async def test_search_vector_uses_cte_when_dsl_filter_present():
    """Vector path mirrors text path: candidate CTE before ANN scan."""
    repo = KnowledgeItemSearchRepository(embedding_table_name="emb_table")
    cursor = _RecordingCursor()

    await repo.search_vector(
        cursor,
        query_embedding=[0.1, 0.2, 0.3],
        kb_codes=["1"],
        where_sql="fe.name = %(dsl_p1)s",
        where_params={"dsl_p1": "x.md"},
        limit=10,
    )

    sql = cursor.executed_sql
    normalized = " ".join(sql.split())
    assert "WITH candidate_entries" in sql
    cte_idx = sql.index("candidate_entries")
    ann_idx = sql.index("<=>")
    assert cte_idx < ann_idx, "CTE must come before ANN scan"
    assert "JOIN emb_table e ON e.chunk_id = r.chunk_id" in normalized
    assert cursor.executed_params["dsl_p1"] == "x.md"
    assert cursor.executed_params["kb_codes"] == ["1"]
    assert cursor.executed_params["limit"] == 10
    assert cursor.executed_params["query_embedding"] == "[0.1,0.2,0.3]"


@pytest.mark.asyncio
async def test_search_vector_omits_cte_when_no_dsl_filter():
    repo = KnowledgeItemSearchRepository(embedding_table_name="emb_table")
    cursor = _RecordingCursor()

    await repo.search_vector(
        cursor,
        query_embedding=[0.1, 0.2, 0.3],
        kb_codes=["1"],
        limit=10,
    )

    sql = cursor.executed_sql
    assert "candidate_entries" not in sql
    assert "<=>" in sql


@pytest.mark.asyncio
async def test_search_text_segments_chinese_query():
    """Chinese query must be segmented before plainto_tsquery."""
    repo = KnowledgeItemSearchRepository(embedding_table_name="emb")
    cursor = _RecordingCursor()

    await repo.search_text(
        cursor,
        query="如何部署Kubernetes集群",
        kb_codes=["1"],
        limit=10,
    )

    params = cursor.executed_params
    assert params["query"] == "如何部署Kubernetes集群"
    assert "segmented_query" in params
    segmented = params["segmented_query"]
    assert "部署" in segmented
    assert " " in segmented  # jieba inserts spaces between words
    assert "plainto_tsquery" in cursor.executed_sql
    assert "%(segmented_query)s" in cursor.executed_sql
