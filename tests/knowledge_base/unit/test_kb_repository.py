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
    cursor = FakeCursor(fetchone_results=[{"kid": 7}])

    repo.create_knowledge_base(
        cursor,
        kb_name="人力制度知识库",
        kb_description=None,
    )

    assert "insert into knowledge_base" in cursor.executed[0][0].lower()
    assert "merge into" not in cursor.executed[0][0].lower()
    assert "kb_code" not in cursor.executed[0][1]
    assert "returning kid" in cursor.executed[0][0].lower()


def test_get_knowledge_base_by_name_queries_active_row():
    """Knowledge base name lookup should only target active rows."""
    repo = KnowledgeBaseRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 7, "kb_name": "人力制度知识库"}])

    repo.get_by_name(cursor, "人力制度知识库")

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "from knowledge_base" in lowered
    assert "where kb_name = %(kb_name)s" in lowered
    assert "and is_deleted = false" in lowered
    assert params == {"kb_name": "人力制度知识库"}


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
        },
    )

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "update knowledge_base" in lowered
    assert "kb_name = %(kb_name)s" in sql
    assert "kb_description = %(kb_description)s" in sql
    assert "metadata = %(metadata)s::jsonb" not in sql
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


def test_create_directory_entry_executes_insert_sql():
    """Directory creation should emit a directory insert statement."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor(
        fetchone_results=[
            {
                "kid": 80,
                "knowledge_base_id": 7,
                "parent_entry_id": None,
                "path_ltree": "d1_5f95f5aa",
                "name": "考勤制度",
                "entry_type": "DIRECTORY",
                "is_root": False,
                "depth": 1,
            },
            None,
            {
                "kid": 81,
                "knowledge_base_id": 7,
                "parent_entry_id": 80,
                "path_ltree": "d1_5f95f5aa.d2_4100c4d4",
                "name": "归档",
                "entry_type": "DIRECTORY",
                "is_root": False,
                "depth": 2,
            },
        ]
    )

    repo.create_directory_entry(
        cursor,
        knowledge_base_id=7,
        full_path="考勤制度/归档",
        directory_description=None,
    )

    insert_sql, params = cursor.executed[-1]
    lowered = insert_sql.lower()
    assert "insert into knowledge_fs_entry" in lowered
    assert "'directory'" in lowered
    assert "description" in lowered
    assert "status" not in lowered
    assert "metadata" not in lowered
    assert params["knowledge_base_id"] == 7
    assert params["parent_entry_id"] == 80
    assert params["name"] == "归档"


def test_create_directory_entry_recursively_creates_missing_parents():
    """Directory creation should support recursive parent creation."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor(
        fetchone_results=[
            None,
            {
                "kid": 80,
                "knowledge_base_id": 7,
                "parent_entry_id": None,
                "path_ltree": "d1_5f95f5aa",
                "name": "考勤制度",
                "entry_type": "DIRECTORY",
                "is_root": False,
                "depth": 1,
            },
            None,
            {
                "kid": 81,
                "knowledge_base_id": 7,
                "parent_entry_id": 80,
                "path_ltree": "d1_5f95f5aa.d2_4100c4d4",
                "name": "归档",
                "entry_type": "DIRECTORY",
                "is_root": False,
                "depth": 2,
            },
        ]
    )

    repo.create_directory_entry(
        cursor,
        knowledge_base_id=7,
        full_path="考勤制度/归档",
        directory_description="递归创建目录",
    )

    insert_statements = [
        (sql, params)
        for sql, params in cursor.executed
        if "insert into knowledge_fs_entry" in sql.lower()
    ]
    assert len(insert_statements) == 2
    assert insert_statements[0][1]["name"] == "考勤制度"
    assert insert_statements[0][1]["parent_entry_id"] is None
    assert insert_statements[0][1]["path_ltree"].startswith("d1_")
    assert insert_statements[1][1]["name"] == "归档"
    assert insert_statements[1][1]["parent_entry_id"] == 80


def test_create_file_entry_inserts_file_row_without_old_status_columns():
    """File creation should insert a FILE entry without old status/metadata columns."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor(
        fetchone_results=[
            {
                "kid": 80,
                "knowledge_base_id": 7,
                "parent_entry_id": None,
                "path_ltree": "d1_5f95f5aa",
                "name": "考勤制度",
                "entry_type": "DIRECTORY",
                "is_root": False,
                "depth": 1,
            },
            None,
            {
                "kid": 81,
                "knowledge_base_id": 7,
                "parent_entry_id": 80,
                "path_ltree": "d1_5f95f5aa.f2_4100c4d4",
                "name": "考勤制度.pdf",
                "entry_type": "FILE",
                "is_root": False,
                "depth": 2,
            },
        ]
    )

    repo.create_file_entry(
        cursor,
        knowledge_base_id=7,
        full_path="考勤制度/考勤制度.pdf",
        file_description="原始文件",
    )

    insert_sql, params = cursor.executed[-1]
    lowered = insert_sql.lower()
    assert "insert into knowledge_fs_entry" in lowered
    assert "'file'" in lowered
    assert "description" in lowered
    assert "status" not in lowered
    assert "metadata" not in lowered
    assert params["knowledge_base_id"] == 7
    assert params["parent_entry_id"] == 80
    assert params["name"] == "考勤制度.pdf"
    assert params["description"] == "原始文件"


def test_create_file_entry_recursively_creates_missing_parents():
    """File creation should support recursive parent-directory creation."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor(
        fetchone_results=[
            None,
            {
                "kid": 80,
                "knowledge_base_id": 7,
                "parent_entry_id": None,
                "path_ltree": "d1_5f95f5aa",
                "name": "考勤制度",
                "entry_type": "DIRECTORY",
                "is_root": False,
                "depth": 1,
            },
            None,
            {
                "kid": 81,
                "knowledge_base_id": 7,
                "parent_entry_id": 80,
                "path_ltree": "d1_5f95f5aa.d2_4100c4d4",
                "name": "归档",
                "entry_type": "DIRECTORY",
                "is_root": False,
                "depth": 2,
            },
            None,
            {
                "kid": 82,
                "knowledge_base_id": 7,
                "parent_entry_id": 81,
                "path_ltree": "d1_5f95f5aa.d2_4100c4d4.f3_12345678",
                "name": "考勤制度.pdf",
                "entry_type": "FILE",
                "is_root": False,
                "depth": 3,
            },
        ]
    )

    repo.create_file_entry(
        cursor,
        knowledge_base_id=7,
        full_path="考勤制度/归档/考勤制度.pdf",
        file_description="原始文件",
    )

    insert_statements = [
        (sql, params)
        for sql, params in cursor.executed
        if "insert into knowledge_fs_entry" in sql.lower()
    ]
    assert len(insert_statements) == 3
    assert insert_statements[0][1]["name"] == "考勤制度"
    assert insert_statements[1][1]["name"] == "归档"
    assert insert_statements[2][1]["name"] == "考勤制度.pdf"
    assert insert_statements[2][1]["parent_entry_id"] == 81


def test_get_file_by_path_resolves_file_under_current_path_model():
    """File lookup should traverse knowledge-base-relative paths and return storage metadata."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor(
        fetchone_results=[
            {
                "kid": 80,
                "knowledge_base_id": 7,
                "parent_entry_id": None,
                "path_ltree": "d1_5f95f5aa",
                "name": "考勤制度",
                "entry_type": "DIRECTORY",
                "is_root": False,
                "depth": 1,
            },
            {
                "kid": 81,
                "knowledge_base_id": 7,
                "parent_entry_id": 80,
                "path_ltree": "d1_5f95f5aa.f2_4100c4d4",
                "name": "考勤制度.pdf",
                "entry_type": "FILE",
                "is_root": False,
                "depth": 2,
            },
            {
                "kid": 81,
                "knowledge_base_id": 7,
                "parent_entry_id": 80,
                "path_ltree": "d1_5f95f5aa.f2_4100c4d4",
                "name": "考勤制度.pdf",
                "entry_type": "FILE",
                "is_root": False,
                "depth": 2,
                "file_bucket_name": "knowledge-base",
                "file_object_key": "kb/7/fs-entry/81/original.pdf",
                "markdown_bucket_name": "knowledge-base-markdown",
                "markdown_object_key": "kb/7/fs-entry/81/markdown.md",
                "file_size": 245760,
                "mime_type": "application/pdf",
                "checksum": "abc123",
            },
        ]
    )

    row = repo.get_file_by_path(
        cursor,
        knowledge_base_id=7,
        full_path="考勤制度/考勤制度.pdf",
    )

    assert row is not None
    assert row["kid"] == 81
    assert row["file_object_key"] == "kb/7/fs-entry/81/original.pdf"
    assert len(cursor.executed) == 3


def test_list_children_by_parent_entry_id_uses_current_fs_entry_columns():
    """Directory listing should read direct children from knowledge_fs_entry only."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor(
        fetchall_results=[
            [
                {"name": "归档", "type": "directory", "size": 0},
                {"name": "考勤制度.pdf", "type": "file", "size": 245760},
            ]
        ]
    )

    rows = repo.list_children_by_parent_entry_id(
        cursor,
        knowledge_base_id=7,
        parent_entry_id=80,
    )

    assert rows == [
        {"name": "归档", "type": "directory", "size": 0},
        {"name": "考勤制度.pdf", "type": "file", "size": 245760},
    ]
    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "from knowledge_fs_entry fs" in lowered
    assert "knowledge_item" not in lowered
    assert "knowledge_item_version" not in lowered
    assert "fs.file_size" in lowered
    assert params == {"knowledge_base_id": 7, "parent_entry_id": 80}


def test_update_file_entry_storage_updates_new_storage_columns():
    """File upload should persist object-storage metadata on the fs entry row."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()

    repo.update_file_entry_storage(
        cursor,
        fs_entry_id=81,
        file_description="原始文件",
        file_bucket_name="knowledge-base",
        file_object_key="kb/7/fs-entry/81/original.pdf",
        file_size=128,
        mime_type="application/pdf",
        checksum="abc123",
    )

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "update knowledge_fs_entry" in lowered
    assert "description = %(description)s" in sql
    assert "file_bucket_name = %(file_bucket_name)s" in sql
    assert "file_object_key = %(file_object_key)s" in sql
    assert "file_size = %(file_size)s" in sql
    assert "mime_type = %(mime_type)s" in sql
    assert "checksum = %(checksum)s" in sql
    assert params["fs_entry_id"] == 81
    assert params["file_bucket_name"] == "knowledge-base"


def test_soft_delete_directory_subtree_updates_descendants():
    """Directory subtree deletion should update all descendant filesystem entries."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor()

    repo.soft_delete_subtree(cursor, knowledge_base_id=7, root_fs_entry_id=81)

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "update knowledge_fs_entry" in lowered
    assert "path_ltree <@" in lowered
    assert params == {"knowledge_base_id": 7, "root_fs_entry_id": 81}


def test_get_knowledge_base_by_code_filters_deleted_rows():
    """Knowledge base lookup should ignore logically deleted rows in SQL."""
    repo = KnowledgeBaseRepository()
    cursor = FakeCursor()

    repo.get_by_code(cursor, "hr-policy")

    sql, params = cursor.executed[0]
    assert "kid = %(kb_code)s::bigint" in sql
    assert "is_deleted = FALSE" in sql
    assert params == {"kb_code": "hr-policy"}


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


def test_rename_entry_updates_name_and_path_ltree_prefix():
    """Directory rename should rebuild the entry path prefix without the openGauss subpath edge-case."""
    repo = KnowledgeFsEntryRepository()
    cursor = FakeCursor(
        fetchone_results=[
            {
                "kid": 81,
                "parent_entry_id": 80,
                "path_ltree": "d1_parent.d2_old",
                "depth": 2,
            }
        ]
    )

    repo.rename_entry(cursor, entry_id=81, new_name="新目录")

    assert (
        "select kid, parent_entry_id, path_ltree, depth"
        in cursor.executed[0][0].lower()
    )
    update_sql, params = cursor.executed[1]
    lowered = update_sql.lower()
    assert "update knowledge_fs_entry" in lowered
    assert "set name = case" in lowered
    assert "when fs.kid = %(entry_id)s then %(new_name)s" in lowered
    assert "else fs.name" in lowered
    assert "path_ltree = text2ltree(" in lowered
    assert "fs.path_ltree::text" in lowered
    assert "substring(" in lowered
    assert "from char_length(%(current_path_ltree)s) + 1" in lowered
    assert "coalesce(" in lowered
    assert (
        "subpath(fs.path_ltree, nlevel(%(current_path_ltree)s::ltree))" not in lowered
    )
    assert "where fs.path_ltree <@ %(current_path_ltree)s::ltree" in lowered
    assert params["entry_id"] == 81
    assert params["new_name"] == "新目录"
    assert params["current_path_ltree"] == "d1_parent.d2_old"
    assert params["new_path_ltree"].startswith("d1_parent.d2_")
    assert params["new_path_ltree"] != "d1_parent.d2_old"


def test_upsert_fetch_cache_entry_persists_cache_identity_and_expiry():
    """Fetch cache upsert should store the current file-node cache identity."""
    repo = KnowledgeFetchCacheRepository()
    cursor = FakeCursor(fetchone_results=[{"kid": 300}])

    repo.upsert_cache_entry(
        cursor,
        knowledge_base_id=7,
        fs_entry_id=71,
        full_path="dir1/doc.md",
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
    assert "fs_entry_id = %(fs_entry_id)s" in delete_sql
    assert "cache_file_path = %(cache_file_path)s" in delete_sql
    assert "insert into knowledge_fetch_cache_index" in insert_lowered
    assert "knowledge_item_id" not in insert_lowered
    assert "knowledge_item_version_id" not in insert_lowered
    assert "kb_code" not in insert_lowered
    assert "virtual_path" not in insert_lowered
    assert "expires_at" in insert_lowered
    assert "cache_status" in insert_lowered
    assert insert_params["fs_entry_id"] == 71
    assert insert_params["cache_ttl_seconds"] == 86400
    assert (
        delete_params["cache_file_path"] == "/tmp/kb_cache/人力制度知识库/dir1/doc.md"
    )


def test_get_cache_entry_by_fs_entry_id_queries_live_cache_row():
    """Fetch should look up a cache record by file-node id."""
    repo = KnowledgeFetchCacheRepository()
    cursor = FakeCursor()

    repo.get_by_fs_entry_id(cursor, fs_entry_id=71)

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "from knowledge_fetch_cache_index" in lowered
    assert "fs_entry_id = %(fs_entry_id)s" in sql
    assert params == {"fs_entry_id": 71}


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


def test_delete_retrieval_projection_for_fs_entry_ids_executes_targeted_delete():
    """Directory deletion should clear projection rows for the subtree fs_entry ids."""
    repo = RetrievalProjectionRepository()
    cursor = FakeCursor()

    repo.delete_for_fs_entry_ids(
        cursor,
        knowledge_base_id=7,
        fs_entry_ids=[81, 82, 83],
    )

    sql, params = cursor.executed[0]
    lowered = sql.lower()
    assert "delete from knowledge_item_chunk_retrieval_mv" in lowered
    assert "knowledge_base_id = %(knowledge_base_id)s" in sql
    assert "fs_entry_id = any(%(fs_entry_ids)s)" in lowered
    assert params == {"knowledge_base_id": 7, "fs_entry_ids": [81, 82, 83]}


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
