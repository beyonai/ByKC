"""Tests for KB repository SQL execution."""

from by_qa.knowledge_base.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fetch_cache_repository import (
    KnowledgeFetchCacheRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fs_entry_repository import (
    KnowledgeFsEntryRepository,
)
from by_qa.knowledge_base.repositories.knowledge_item_chunk_repository import (
    KnowledgeItemChunkRepository,
)
from by_qa.knowledge_base.repositories.knowledge_item_repository import (
    KnowledgeItemRepository,
)
from by_qa.knowledge_base.repositories.knowledge_item_search_repository import (
    KnowledgeItemSearchRepository,
)
from by_qa.knowledge_base.repositories.knowledge_item_version_repository import (
    KnowledgeItemVersionRepository,
)
from by_qa.knowledge_base.repositories.retrieval_projection_repository import (
    RetrievalProjectionRepository,
)


class FakeCursor:
    """Cursor test double capturing executed SQL."""

    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.executed: list[tuple[str, tuple | dict | None]] = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        if self._fetchone_results:
            return self._fetchone_results.pop(0)
        return None

    def fetchall(self):
        if self._fetchall_results:
            return self._fetchall_results.pop(0)
        return []


def test_create_knowledge_base_executes_insert_sql():
    """Knowledge base creation should emit a plain insert statement."""
    repo = KnowledgeBaseRepository()
    cursor = FakeCursor()

    repo.create_knowledge_base(
        cursor,
        kb_code="hr-policy",
        kb_name="人力制度知识库",
        kb_description=None,
        status="ACTIVE",
        metadata={"owner": "HR"},
    )

    assert "insert into knowledge_base" in cursor.executed[0][0].lower()
    assert "merge into" not in cursor.executed[0][0].lower()
    assert cursor.executed[0][1]["kb_code"] == "hr-policy"


def test_soft_delete_knowledge_base_updates_is_deleted_flag():
    """Knowledge base deletion should update the logical-delete flag instead of removing rows."""
    repo = KnowledgeBaseRepository()
    cursor = FakeCursor()

    repo.soft_delete_by_code(cursor, kb_code="hr-policy")

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "update knowledge_base" in lowered
    assert "is_deleted = true" in lowered
    assert params == {"kb_code": "hr-policy"}


def test_update_knowledge_base_executes_update_sql():
    """Knowledge base updates should emit a plain update statement."""
    repo = KnowledgeBaseRepository()
    cursor = FakeCursor()

    repo.update_knowledge_base(
        cursor,
        kb_code="hr-policy",
        updates={
            "kb_name": "新知识库名称",
            "kb_description": "更新后的描述",
            "metadata": {"owner": "HR"},
        },
    )

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "update knowledge_base" in lowered
    assert "kb_name = %(kb_name)s" in sql
    assert "kb_description = %(kb_description)s" in sql
    assert "metadata = %(metadata)s::jsonb" in sql
    assert params["kb_code"] == "hr-policy"
    assert params["kb_name"] == "新知识库名称"


def test_update_knowledge_base_only_updates_provided_fields():
    """Partial KB updates should not emit assignments for omitted fields."""
    repo = KnowledgeBaseRepository()
    cursor = FakeCursor()

    repo.update_knowledge_base(
        cursor,
        kb_code="hr-policy",
        updates={"kb_name": "新知识库名称"},
    )

    sql, params = cursor.executed[0]
    assert "kb_name = %(kb_name)s" in sql
    assert "kb_description = %(kb_description)s" not in sql
    assert "metadata = %(metadata)s::jsonb" not in sql
    assert params == {"kb_code": "hr-policy", "kb_name": "新知识库名称"}


def test_get_knowledge_base_by_code_filters_deleted_rows():
    """Knowledge base lookup should ignore logically deleted rows in SQL."""
    repo = KnowledgeBaseRepository()
    cursor = FakeCursor()

    repo.get_by_code(cursor, "hr-policy")

    sql, params = cursor.executed[0]
    assert "is_deleted = FALSE" in sql
    assert params == {"kb_code": "hr-policy"}


def test_get_any_knowledge_base_by_code_includes_deleted_rows():
    """Conflict checks should be able to query a KB code including deleted rows."""
    repo = KnowledgeBaseRepository()
    cursor = FakeCursor()

    repo.get_any_by_code(cursor, "hr-policy")

    sql, params = cursor.executed[0]
    assert "is_deleted = FALSE" not in sql
    assert params == {"kb_code": "hr-policy"}


def test_get_by_fs_entry_id_queries_knowledge_item_by_kb_and_fs_entry():
    """Knowledge item lookup should query by knowledge base and fs entry id."""
    repo = KnowledgeItemRepository()
    cursor = FakeCursor()

    repo.get_by_fs_entry_id(cursor, knowledge_base_id=7, fs_entry_id=71)

    assert "from knowledge_item" in cursor.executed[0][0].lower()
    assert "item_code" in cursor.executed[0][0].lower()
    assert "is_deleted = FALSE" in cursor.executed[0][0]
    assert "knowledge_base_id = %(knowledge_base_id)s" in cursor.executed[0][0]
    assert "fs_entry_id = %(fs_entry_id)s" in cursor.executed[0][0]
    assert cursor.executed[0][1] == {"knowledge_base_id": 7, "fs_entry_id": 71}


def test_get_any_by_fs_entry_id_includes_deleted_rows():
    """Conflict checks should be able to query deleted items by fs entry id."""
    repo = KnowledgeItemRepository()
    cursor = FakeCursor()

    repo.get_any_by_fs_entry_id(cursor, knowledge_base_id=7, fs_entry_id=71)

    sql, params = cursor.executed[0]
    assert "is_deleted = FALSE" not in sql
    assert params == {"knowledge_base_id": 7, "fs_entry_id": 71}


def test_get_by_item_code_queries_knowledge_item_by_kb_and_item_code():
    """Knowledge item lookup should support item_code-based indexing workflows."""
    repo = KnowledgeItemRepository()
    cursor = FakeCursor()

    repo.get_by_item_code(cursor, knowledge_base_id=7, item_code="file-001")

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "from knowledge_item" in lowered
    assert "item_code = %(item_code)s" in sql
    assert "is_deleted = FALSE" in sql
    assert params == {"knowledge_base_id": 7, "item_code": "file-001"}


def test_get_any_by_item_code_includes_deleted_rows():
    """Conflict checks should be able to query deleted items by file_code."""
    repo = KnowledgeItemRepository()
    cursor = FakeCursor()

    repo.get_any_by_item_code(cursor, knowledge_base_id=7, item_code="file-001")

    sql, params = cursor.executed[0]
    assert "is_deleted = FALSE" not in sql
    assert params == {"knowledge_base_id": 7, "item_code": "file-001"}


def test_soft_delete_knowledge_item_by_kb_updates_is_deleted_flag():
    """Bulk item deletion should use logical-delete updates."""
    repo = KnowledgeItemRepository()
    cursor = FakeCursor()

    repo.soft_delete_by_knowledge_base_id(cursor, knowledge_base_id=7)

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "update knowledge_item" in lowered
    assert "is_deleted = true" in lowered
    assert params == {"knowledge_base_id": 7}


def test_soft_delete_knowledge_item_by_item_code_updates_is_deleted_flag():
    """Single-item deletion should target one business item_code."""
    repo = KnowledgeItemRepository()
    cursor = FakeCursor()

    repo.soft_delete_by_item_code(cursor, knowledge_base_id=7, item_code="file-001")

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "update knowledge_item" in lowered
    assert "item_code = %(item_code)s" in sql
    assert "is_deleted = true" in lowered
    assert params == {"knowledge_base_id": 7, "item_code": "file-001"}


def test_upsert_knowledge_item_persists_item_code():
    """Knowledge item upsert should persist the stable business item_code."""
    repo = KnowledgeItemRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 10}])

    repo.upsert(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=71,
        item_code="item-1",
        item_kind="FILE",
        description="Policy file",
        status="ACTIVE",
        source_code="oa",
        type_code="policy_markdown",
        metadata={"owner": "HR"},
    )

    merged_sql, merged_params = cursor.executed[0]
    lowered = merged_sql.lower()
    assert "item_code" in lowered
    assert merged_params["item_code"] == "item-1"


def test_get_by_item_and_version_queries_version_row():
    """Version lookup should query by knowledge item id and version."""
    repo = KnowledgeItemVersionRepository()
    cursor = FakeCursor()

    repo.get_by_item_and_version(cursor, knowledge_item_id=10, version="v1")

    assert "from knowledge_item_version" in cursor.executed[0][0].lower()
    assert cursor.executed[0][1] == {"knowledge_item_id": 10, "version": "v1"}


def test_upsert_knowledge_item_version_persists_original_and_markdown_keys():
    """Version upsert should persist original and markdown object metadata together."""
    repo = KnowledgeItemVersionRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 22}])

    repo.upsert(
        cursor,
        knowledge_item_id=10,
        fs_entry_id=71,
        version="v1",
        bucket_name="knowledge-base",
        object_key="7/dir1/item-1.pdf/v1/item-1.pdf",
        markdown_bucket_name="knowledge-base-markdown",
        markdown_object_key="7/dir1/item-1.pdf/v1/item-1.md",
        markdown_file_size=256,
        markdown_checksum="md-abc123",
        file_size=128,
        checksum="abc123",
    )

    merged_sql, merged_params = cursor.executed[0]
    lowered = merged_sql.lower()
    assert "markdown_object_key" in lowered
    assert merged_params["markdown_object_key"] == "7/dir1/item-1.pdf/v1/item-1.md"


def test_update_current_version_executes_targeted_update():
    """Latest-version pointer updates should touch knowledge_item.current_version_id."""
    repo = KnowledgeItemRepository()
    cursor = FakeCursor()

    repo.update_current_version(cursor, knowledge_item_id=10, version_id=22)

    assert "update knowledge_item" in cursor.executed[0][0].lower()
    assert "current_version_id" in cursor.executed[0][0]
    assert cursor.executed[0][1] == {"knowledge_item_id": 10, "version_id": 22}


def test_ensure_root_entry_targets_knowledge_fs_entry_tree():
    """Root entry bootstrap should go through knowledge_fs_entry and update the KB root pointer."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor(fetchone_results=[None, {"kid": 70}])

    repo.ensure_root_entry(cursor, knowledge_base_id=7, kb_name="人力制度知识库")

    combined_sql = "\n".join(sql for sql, _ in cursor.executed).lower()
    assert "knowledge_fs_entry" in combined_sql
    assert "root_entry_id" in combined_sql
    assert "knowledge_base" in combined_sql


def test_list_root_entries_queries_root_directories():
    """Root listing should target root directory entries only."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()

    repo.list_root_entries(cursor, kb_codes=["hr-policy"])

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "from knowledge_fs_entry" in lowered
    assert "join knowledge_base kb" in lowered
    assert "kb.kb_code" in sql
    assert "is_root = true" in lowered
    assert "entry_type = 'directory'" in lowered
    assert "fs.is_deleted = false" in lowered
    assert "kb.is_deleted = false" in lowered
    assert "kb.kb_code = ANY(%(kb_codes)s)" in sql
    assert params == {"kb_codes": ["hr-policy"]}


def test_list_root_nodes_returns_tree_fields_for_pattern_traversal():
    """Root node traversal should fetch path_ltree and node names for iterative matching."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()

    repo.list_root_nodes(cursor, kb_codes=["hr-policy"])

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "kb.kb_code" in sql
    assert "path_ltree" in lowered
    assert "fs.name" in lowered
    assert "is_root = true" in lowered
    assert "fs.is_deleted = false" in lowered
    assert "kb.is_deleted = false" in lowered
    assert "kb.kb_code = ANY(%(kb_codes)s)" in sql
    assert params == {"kb_codes": ["hr-policy"]}


def test_list_children_uses_path_ltree_for_direct_children_and_file_size():
    """Child listing should use path_ltree direct-child filtering and join current version for file size."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()

    repo.list_children(cursor, parent_path_ltree="kb_7.d1_a")

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "from knowledge_fs_entry fs" in lowered
    assert "join knowledge_base kb" in lowered
    assert "kb.kb_code" in sql
    assert "left join knowledge_item ki" in lowered
    assert "left join knowledge_item_version kv" in lowered
    assert "fs.is_deleted = false" in lowered
    assert "kb.is_deleted = false" in lowered
    assert "fs.path_ltree <@ %(parent_path_ltree)s::ltree" in sql
    assert "nlevel(fs.path_ltree) = nlevel(%(parent_path_ltree)s::ltree) + 1" in sql
    assert "coalesce(kv.file_size, 0)" in lowered
    assert params == {"parent_path_ltree": "kb_7.d1_a"}


def test_list_child_nodes_uses_path_ltree_for_direct_children_traversal():
    """Traversal child listing should fetch direct children plus path_ltree metadata."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()

    repo.list_child_nodes(cursor, parent_path_ltree="kb_7")

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "from knowledge_fs_entry fs" in lowered
    assert "join knowledge_base kb" in lowered
    assert "kb.kb_code" in sql
    assert "fs.path_ltree <@ %(parent_path_ltree)s::ltree" in sql
    assert "nlevel(fs.path_ltree) = nlevel(%(parent_path_ltree)s::ltree) + 1" in sql
    assert "path_ltree" in lowered
    assert params == {"parent_path_ltree": "kb_7"}


def test_get_current_file_version_by_entry_id_joins_current_version_metadata():
    """Fetch should resolve current-version object metadata through fs_entry_id."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()

    repo.get_current_file_version_by_entry_id(cursor, fs_entry_id=71)

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "from knowledge_fs_entry fs" in lowered
    assert "join knowledge_item ki" in lowered
    assert "join knowledge_item_version kv" in lowered
    assert "join knowledge_base kb" in lowered
    assert "ki.current_version_id = kv.kid" in lowered
    assert "fs.is_deleted = false" in lowered
    assert "ki.is_deleted = false" in lowered
    assert "kb.is_deleted = false" in lowered
    assert "fs.kid = %(fs_entry_id)s" in sql
    assert "bucket_name" in lowered
    assert "object_key" in lowered
    assert "markdown_bucket_name" in lowered
    assert "markdown_object_key" in lowered
    assert "checksum" in lowered
    assert params == {"fs_entry_id": 71}


def test_soft_delete_fs_entries_by_kb_updates_is_deleted_flag():
    """Bulk filesystem deletion should use logical-delete updates."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()

    repo.soft_delete_by_knowledge_base_id(cursor, knowledge_base_id=7)

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "update knowledge_fs_entry" in lowered
    assert "is_deleted = true" in lowered
    assert params == {"knowledge_base_id": 7}


def test_soft_delete_fs_file_entry_updates_is_deleted_flag():
    """Single-file filesystem deletion should use logical-delete updates."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()

    repo.soft_delete_file_entry(cursor, knowledge_base_id=7, fs_entry_id=71)

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "update knowledge_fs_entry" in lowered
    assert "kid = %(fs_entry_id)s" in sql
    assert "is_deleted = true" in lowered
    assert params == {"knowledge_base_id": 7, "fs_entry_id": 71}


def test_upsert_fetch_cache_entry_persists_cache_identity_and_expiry():
    """Fetch cache upsert should store the current version identity and expiry timestamps."""
    repo = KnowledgeFetchCacheRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 300}])

    repo.upsert_cache_entry(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=71,
        knowledge_item_id=10,
        knowledge_item_version_id=22,
        kb_code="hr-policy",
        full_path="dir1/doc.md",
        virtual_path="人力制度知识库/dir1/doc.md",
        bucket_name="knowledge-base",
        object_key="7/dir1/doc.md/v1/content.md",
        checksum="abc123",
        cache_file_path="/tmp/kb_cache/人力制度知识库/dir1/doc.md",
        file_size=128,
        cache_ttl_seconds=86400,
    )

    delete_sql, delete_params = cursor.executed[0]
    insert_sql, insert_params = cursor.executed[1]
    delete_lowered = delete_sql.lower()
    insert_lowered = insert_sql.lower()
    assert "delete from knowledge_fetch_cache_index" in delete_lowered
    assert "knowledge_item_version_id = %(knowledge_item_version_id)s" in delete_sql
    assert "cache_file_path = %(cache_file_path)s" in delete_sql
    assert "insert into knowledge_fetch_cache_index" in insert_lowered
    assert "expires_at" in insert_lowered
    assert "cache_status" in insert_lowered
    assert insert_params["virtual_path"] == "人力制度知识库/dir1/doc.md"
    assert insert_params["cache_ttl_seconds"] == 86400
    assert (
        delete_params["cache_file_path"] == "/tmp/kb_cache/人力制度知识库/dir1/doc.md"
    )


def test_get_cache_entry_by_version_queries_live_cache_row():
    """Fetch should look up a cache record by current version id."""
    repo = KnowledgeFetchCacheRepository()
    cursor = FakeCursor()

    repo.get_by_version_id(cursor, knowledge_item_version_id=22)

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "from knowledge_fetch_cache_index" in lowered
    assert "knowledge_item_version_id = %(knowledge_item_version_id)s" in sql
    assert params == {"knowledge_item_version_id": 22}


def test_mark_expired_ready_entries_as_evicting_uses_skip_locked_batching():
    """Cleanup should promote expired READY rows to EVICTING in bounded batches."""
    repo = KnowledgeFetchCacheRepository()
    cursor = FakeCursor()

    repo.mark_expired_ready_entries_as_evicting(cursor, batch_size=100)

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "update knowledge_fetch_cache_index" in lowered
    assert "cache_status = 'evicting'" in lowered
    assert "for update skip locked" in lowered
    assert params == {"batch_size": 100}


def test_list_cleanup_candidates_targets_evicting_and_error_rows():
    """Cleanup workers should reload in-flight and failed rows for retry."""
    repo = KnowledgeFetchCacheRepository()
    cursor = FakeCursor()

    repo.list_cleanup_candidates(cursor, batch_size=50)

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "from knowledge_fetch_cache_index" in lowered
    assert "cache_status in ('evicting', 'error')" in lowered
    assert "for update skip locked" in lowered
    assert params == {"batch_size": 50}


def test_refresh_retrieval_projection_rebuilds_current_version_rows():
    """Projection refresh should target the retrieval projection object."""
    repo = RetrievalProjectionRepository()
    cursor = FakeCursor()

    repo.refresh_for_item(cursor, knowledge_item_id=10)

    combined_sql = "\n".join(sql for sql, _ in cursor.executed).lower()
    assert "knowledge_item_chunk_retrieval_mv" in combined_sql
    assert "delete from" in combined_sql
    assert "insert into" in combined_sql
    assert "knowledge_base_status" in combined_sql
    assert "knowledge_item_status" in combined_sql
    assert "metadata" in combined_sql
    assert "item_code" in combined_sql
    assert "item_kind" in combined_sql
    assert "full_path" in combined_sql
    assert "start_line" in combined_sql
    assert "end_line" in combined_sql
    assert "kb.is_deleted = false" in combined_sql
    assert "fs.is_deleted = false" in combined_sql
    assert "ki.is_deleted = false" in combined_sql


def test_delete_retrieval_projection_for_knowledge_base_executes_targeted_delete():
    """Knowledge-base deletion should clear projection rows for that KB immediately."""
    repo = RetrievalProjectionRepository()
    cursor = FakeCursor()

    repo.delete_for_knowledge_base(cursor, knowledge_base_id=7)

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "delete from knowledge_item_chunk_retrieval_mv" in lowered
    assert "knowledge_base_id = %(knowledge_base_id)s" in sql
    assert params == {"knowledge_base_id": 7}


def test_delete_retrieval_projection_for_item_executes_targeted_delete():
    """Document deletion should clear projection rows for only that item."""
    repo = RetrievalProjectionRepository()
    cursor = FakeCursor()

    repo.delete_for_item(cursor, knowledge_item_id=10)

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "delete from knowledge_item_chunk_retrieval_mv" in lowered
    assert "knowledge_item_id = %(knowledge_item_id)s" in sql
    assert params == {"knowledge_item_id": 10}


def test_replace_chunks_clears_existing_embeddings_before_deleting_old_chunks():
    """Replacing version chunks should delete dynamic embeddings before old chunk rows."""
    repo = KnowledgeItemChunkRepository("chunk_embedding_bge_m3")
    cursor = FakeCursor()

    repo.replace_for_version(
        cursor,
        knowledge_item_id=10,
        knowledge_item_version_id=22,
        chunks=[
            {"chunk_no": 1, "start_line": 1, "end_line": 10, "chunk_text": "hello"}
        ],
    )

    first_sql = cursor.executed[0][0].lower()
    second_sql = cursor.executed[1][0].lower()
    assert "delete from chunk_embedding_bge_m3" in first_sql
    assert "delete from knowledge_item_chunk" in second_sql
    assert "select kid" in first_sql


def test_replace_embeddings_serializes_vectors_as_literal_strings():
    """Dynamic vector inserts should serialize python lists into vector literals."""
    repo = KnowledgeItemChunkRepository("chunk_embedding_bge_m3")
    cursor = FakeCursor()

    repo.replace_embeddings(
        cursor,
        embeddings=[{"chunk_id": 101, "embedding": [0.1, 0.2, 0.3]}],
    )

    insert_sql, params = cursor.executed[1]
    assert "insert into chunk_embedding_bge_m3" in insert_sql.lower()
    assert params["embedding"] == "[0.1,0.2,0.3]"


def test_search_repository_executes_text_retrieval_against_projection_table():
    """Text recall should query the retrieval projection with status filters."""
    repo = KnowledgeItemSearchRepository("chunk_embedding_bge_m3")
    cursor = FakeCursor()

    repo.search_text(
        cursor,
        query="员工请假制度怎么规定",
        kb_codes=["hr-policy"],
        source_codes=["oa"],
        type_codes=["policy_markdown"],
        limit=30,
    )

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "knowledge_item_chunk_retrieval_mv" in lowered
    assert "knowledge_base_status" in lowered
    assert "knowledge_item_status" in lowered
    assert "item_kind = 'file'" in lowered
    assert "plainto_tsquery" in lowered
    assert "item_code" in lowered
    assert params["kb_codes"] == ["hr-policy"]
    assert params["source_codes"] == ["oa"]
    assert params["type_codes"] == ["policy_markdown"]


def test_search_repository_executes_vector_retrieval_via_embedding_table_and_projection():
    """Vector recall should join the dynamic embedding table to the retrieval projection."""
    repo = KnowledgeItemSearchRepository("chunk_embedding_bge_m3")
    cursor = FakeCursor()

    repo.search_vector(
        cursor,
        query_embedding=[0.1, 0.2, 0.3],
        kb_codes=["hr-policy"],
        source_codes=None,
        type_codes=None,
        limit=40,
    )

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "chunk_embedding_bge_m3" in lowered
    assert "join knowledge_item_chunk_retrieval_mv" in lowered
    assert "knowledge_base_status" in lowered
    assert "knowledge_item_status" in lowered
    assert "r.item_kind = 'file'" in lowered
    assert "item_code" in lowered
    assert params["query_embedding"] == "[0.1,0.2,0.3]"


def test_search_repository_casts_nullable_text_filters_to_text_arrays():
    """Nullable source/type filters should be typed to avoid PostgreSQL parameter ambiguity."""
    repo = KnowledgeItemSearchRepository("chunk_embedding_bge_m3")
    cursor = FakeCursor()

    repo.search_text(
        cursor,
        query="claude code的skill是怎么配置的",
        kb_codes=["demo-kb"],
        source_codes=None,
        type_codes=None,
        limit=30,
    )
    repo.search_vector(
        cursor,
        query_embedding=[0.1, 0.2, 0.3],
        kb_codes=["demo-kb"],
        source_codes=None,
        type_codes=None,
        limit=40,
    )

    text_sql, text_params = cursor.executed[0]
    vector_sql, vector_params = cursor.executed[1]

    assert "%(source_codes)s::text[] IS NULL" in text_sql
    assert "source_code = ANY(%(source_codes)s::text[])" in text_sql
    assert "%(type_codes)s::text[] IS NULL" in text_sql
    assert "type_code = ANY(%(type_codes)s::text[])" in text_sql
    assert "%(source_codes)s::text[] IS NULL" in vector_sql
    assert "r.source_code = ANY(%(source_codes)s::text[])" in vector_sql
    assert "%(type_codes)s::text[] IS NULL" in vector_sql
    assert "r.type_code = ANY(%(type_codes)s::text[])" in vector_sql
    assert text_params["source_codes"] is None
    assert vector_params["type_codes"] is None
