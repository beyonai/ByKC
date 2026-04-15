"""Tests for knowledge-base services transactional behavior."""

from datetime import datetime, timezone

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import (
    CreateDirectoryRequest,
    CreateKnowledgeBaseRequest,
    DeleteDirectoryRequest,
    DeleteKnowledgeBaseRequest,
    DeleteKnowledgeItemRequest,
    FileToMarkdownIndexRequest,
    KnowledgeItemDownloadRequest,
    KnowledgeItemGlobRequest,
    KnowledgeItemListDirRequest,
    KnowledgeItemUploadRequest,
    UpdateDirectoryRequest,
    UpdateKnowledgeBaseRequest,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService
from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    KnowledgeItemIngestionService,
)
from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload


class FakeConnection:
    """Simple transaction double."""

    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.cursor_obj = FakeServiceCursor()

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        return None


class FakeServiceCursor:
    """Minimal cursor double for service-level raw SQL assertions."""

    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


class FakeKnowledgeBaseRepository:
    """Repository double for knowledge-base metadata."""

    def __init__(self, *, default_lookup_result=None):
        self.calls = []
        self.existing_by_code = {}
        self.existing_by_name = {}
        self.default_lookup_result = default_lookup_result

    def create_knowledge_base(self, cursor, **kwargs):
        self.calls.append(("create_knowledge_base", kwargs))
        row = {
            "kid": 7,
            "kb_name": kwargs["kb_name"],
            "kb_description": kwargs.get("kb_description"),
        }
        self.default_lookup_result = row
        return row

    def get_by_name(self, cursor, kb_name):
        self.calls.append(("get_by_name", {"kb_name": kb_name}))
        return self.existing_by_name.get(kb_name)

    def get_by_code(self, cursor, kb_code):
        self.calls.append(("get_by_code", {"kb_code": kb_code}))
        if kb_code in self.existing_by_code:
            row = self.existing_by_code[kb_code]
            if row.get("is_deleted") is True:
                return None
            return row
        return self.default_lookup_result

    def get_any_by_code(self, cursor, kb_code):
        self.calls.append(("get_any_by_code", {"kb_code": kb_code}))
        if kb_code in self.existing_by_code:
            return self.existing_by_code[kb_code]
        return self.default_lookup_result

    def soft_delete_by_code(self, cursor, *, kb_code):
        self.calls.append(("soft_delete_by_code", {"kb_code": kb_code}))

    def update_knowledge_base(self, cursor, *, kb_code, updates):
        self.calls.append(
            ("update_knowledge_base", {"kb_code": kb_code, "updates": updates})
        )
        existing = self.existing_by_code.get(kb_code) or self.default_lookup_result
        if existing is None:
            return
        for key, value in updates.items():
            existing[key] = value

    def update_root_entry(self, cursor, *, knowledge_base_id, root_entry_id):
        self.calls.append(
            (
                "update_root_entry",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "root_entry_id": root_entry_id,
                },
            )
        )


class FakeKnowledgeFsEntryRepository:
    """Repository double for filesystem entries."""

    def __init__(self):
        self.calls = []
        self.raise_missing_parent_directory = False
        self.root_entry = {"kid": 70, "is_root": True, "full_path": "人力制度知识库"}
        self.file_entry = {"kid": 71, "entry_type": "FILE", "full_path": "item-1"}
        self.file_entry_by_path = {}
        self.root_entries_by_kb_code = {
            "hr-policy": [
                {
                    "kb_code": "hr-policy",
                    "name": "/人力制度知识库",
                    "type": "directory",
                    "size": 0,
                }
            ],
            "legal-policy": [
                {
                    "kb_code": "legal-policy",
                    "name": "/法务制度知识库",
                    "type": "directory",
                    "size": 0,
                }
            ],
            "demo-kb": [
                {
                    "kb_code": "demo-kb",
                    "name": "/DEMO知识库",
                    "type": "directory",
                    "size": 0,
                }
            ],
        }
        self.root_entries = [
            {
                "kb_code": "hr-policy",
                "name": "/人力制度知识库",
                "type": "directory",
                "size": 0,
            },
            {
                "kb_code": "legal-policy",
                "name": "/法务制度知识库",
                "type": "directory",
                "size": 0,
            },
        ]
        self.root_nodes_by_kb_code = {
            "hr-policy": [
                {
                    "kid": 7,
                    "kb_code": "hr-policy",
                    "name": "人力制度知识库",
                    "full_path": "人力制度知识库",
                    "type": "directory",
                    "size": 0,
                    "path_ltree": "kb_7",
                }
            ],
            "legal-policy": [
                {
                    "kid": 8,
                    "kb_code": "legal-policy",
                    "name": "法务制度知识库",
                    "full_path": "法务制度知识库",
                    "type": "directory",
                    "size": 0,
                    "path_ltree": "kb_8",
                }
            ],
            "demo-kb": [
                {
                    "kid": 9,
                    "kb_code": "demo-kb",
                    "name": "DEMO知识库",
                    "full_path": "DEMO知识库",
                    "type": "directory",
                    "size": 0,
                    "path_ltree": "kb_9",
                }
            ],
        }
        self.root_nodes = [
            {
                "kid": 7,
                "kb_code": "hr-policy",
                "name": "人力制度知识库",
                "full_path": "人力制度知识库",
                "type": "directory",
                "size": 0,
                "path_ltree": "kb_7",
            },
            {
                "kid": 8,
                "kb_code": "legal-policy",
                "name": "法务制度知识库",
                "full_path": "法务制度知识库",
                "type": "directory",
                "size": 0,
                "path_ltree": "kb_8",
            },
        ]
        self.directory_entry = {
            "kid": 80,
            "knowledge_base_id": 7,
            "parent_entry_id": 70,
            "name": "dir1",
            "entry_type": "DIRECTORY",
            "full_path": "人力制度知识库/dir1",
            "path_ltree": "kb_7.d1_a",
        }
        self.entry_by_id = {
            80: self.directory_entry,
            70: {
                "kid": 70,
                "knowledge_base_id": 7,
                "parent_entry_id": None,
                "name": "人力制度知识库",
                "entry_type": "DIRECTORY",
                "path_ltree": "kb_7",
                "is_root": True,
                "depth": 0,
            },
        }
        self.child_entry_by_parent_and_name = {}
        self.directory_children = [
            {
                "kb_code": "hr-policy",
                "name": "/人力制度知识库/dir1/doc.md",
                "type": "file",
                "size": 128,
            },
            {
                "kb_code": "hr-policy",
                "name": "/人力制度知识库/dir1/subdir",
                "type": "directory",
                "size": 0,
            },
        ]
        self.directory_child_rows_by_parent = {
            None: [{"kid": 80, "name": "dir1", "type": "directory", "size": 0}],
            80: [
                {"kid": 81, "name": "doc.md", "type": "file", "size": 128},
                {"kid": 82, "name": "subdir", "type": "directory", "size": 0},
            ],
        }
        self.pattern_matches = [
            {
                "kb_code": "hr-policy",
                "name": "/人力制度知识库/dir1/doc.md",
                "type": "file",
                "size": 128,
            }
        ]
        self.child_nodes_by_parent = {
            "kb_7": [
                {
                    "kid": 90,
                    "kb_code": "hr-policy",
                    "name": "doc.md",
                    "full_path": "人力制度知识库/doc.md",
                    "type": "file",
                    "size": 128,
                    "path_ltree": "kb_7.f1_doc",
                },
                {
                    "kid": 80,
                    "kb_code": "hr-policy",
                    "name": "dir1",
                    "full_path": "人力制度知识库/dir1",
                    "type": "directory",
                    "size": 0,
                    "path_ltree": "kb_7.d1_a",
                },
            ],
            "kb_8": [
                {
                    "kid": 81,
                    "kb_code": "legal-policy",
                    "name": "合同.md",
                    "full_path": "法务制度知识库/合同.md",
                    "type": "file",
                    "size": 64,
                    "path_ltree": "kb_8.f1_contract",
                }
            ],
            "kb_7.d1_a": [
                {
                    "kid": 71,
                    "kb_code": "hr-policy",
                    "name": "doc.md",
                    "full_path": "doc.md",
                    "type": "file",
                    "size": 128,
                    "path_ltree": "kb_7.d1_a.f2_doc",
                },
                {
                    "kid": 72,
                    "kb_code": "hr-policy",
                    "name": "subdir",
                    "full_path": "subdir",
                    "type": "directory",
                    "size": 0,
                    "path_ltree": "kb_7.d1_a.d2_subdir",
                },
            ],
        }

    def ensure_root_entry(self, cursor, *, knowledge_base_id, kb_name):
        self.calls.append(
            (
                "ensure_root_entry",
                {"knowledge_base_id": knowledge_base_id, "kb_name": kb_name},
            )
        )
        return self.root_entry

    def rename_entry(self, cursor, *, entry_id, new_name):
        self.calls.append(
            ("rename_entry", {"entry_id": entry_id, "new_name": new_name})
        )
        entry = self.entry_by_id.get(entry_id)
        if entry is not None:
            entry["name"] = new_name

    def create_directory_entry(
        self, cursor, *, knowledge_base_id, full_path, directory_description=None
    ):
        self.calls.append(
            (
                "create_directory_entry",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "full_path": full_path,
                    "directory_description": directory_description,
                },
            )
        )
        if self.raise_missing_parent_directory:
            parent_path = full_path.strip("/").rsplit("/", 1)[0]
            raise ValueError(f"parent directory not found: {parent_path}")
        return {
            "kid": 81,
            "knowledge_base_id": knowledge_base_id,
            "parent_entry_id": 70,
            "entry_type": "DIRECTORY",
            "name": full_path.strip("/").split("/")[-1],
            "path_ltree": "kb_7.d1_archive",
            "depth": len(
                [segment for segment in full_path.strip("/").split("/") if segment]
            ),
        }

    def create_file_entry(
        self, cursor, *, knowledge_base_id, full_path, file_description=None
    ):
        self.calls.append(
            (
                "create_file_entry",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "full_path": full_path,
                    "file_description": file_description,
                },
            )
        )
        if self.raise_missing_parent_directory:
            parent_path = full_path.strip("/").rsplit("/", 1)[0]
            raise ValueError(f"parent directory not found: {parent_path}")
        return {
            "kid": 71,
            "knowledge_base_id": knowledge_base_id,
            "parent_entry_id": None,
            "entry_type": "FILE",
            "name": full_path.strip("/").split("/")[-1],
            "path_ltree": "d1_file.f2_doc",
            "depth": len(
                [segment for segment in full_path.strip("/").split("/") if segment]
            ),
        }

    def update_file_entry_storage(self, cursor, **kwargs):
        self.calls.append(("update_file_entry_storage", kwargs))

    def list_subtree_entry_ids(self, cursor, *, knowledge_base_id, root_fs_entry_id):
        self.calls.append(
            (
                "list_subtree_entry_ids",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "root_fs_entry_id": root_fs_entry_id,
                },
            )
        )
        return [81, 82, 83]

    def soft_delete_subtree(self, cursor, *, knowledge_base_id, root_fs_entry_id):
        self.calls.append(
            (
                "soft_delete_subtree",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "root_fs_entry_id": root_fs_entry_id,
                },
            )
        )

    def ensure_file_entry(self, cursor, *, knowledge_base_id, root_entry_id, full_path):
        self.calls.append(
            (
                "ensure_file_entry",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "root_entry_id": root_entry_id,
                    "full_path": full_path,
                },
            )
        )
        if self.raise_missing_parent_directory:
            parent_path = full_path.strip("/").rsplit("/", 1)[0]
            raise ValueError(f"parent directory not found: {parent_path}")
        return {**self.file_entry, "full_path": full_path}

    def list_root_entries(self, cursor, *, kb_codes):
        self.calls.append(("list_root_entries", {"kb_codes": kb_codes}))
        entries = []
        for kb_code in kb_codes:
            entries.extend(self.root_entries_by_kb_code.get(kb_code, []))
        return entries

    def list_root_nodes(self, cursor, *, kb_codes):
        self.calls.append(("list_root_nodes", {"kb_codes": kb_codes}))
        nodes = []
        for kb_code in kb_codes:
            nodes.extend(self.root_nodes_by_kb_code.get(kb_code, []))
        return nodes

    def list_all_root_nodes(self, cursor):
        self.calls.append(("list_all_root_nodes", {}))
        nodes = []
        for node_list in self.root_nodes_by_kb_code.values():
            nodes.extend(node_list)
        return nodes

    def get_directory_by_path(self, cursor, *, knowledge_base_id, full_path):
        self.calls.append(
            (
                "get_directory_by_path",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "full_path": full_path,
                },
            )
        )
        if full_path in (self.directory_entry["full_path"], "dir1"):
            return self.directory_entry
        return None

    def list_children_by_parent_entry_id(
        self, cursor, *, knowledge_base_id, parent_entry_id
    ):
        self.calls.append(
            (
                "list_children_by_parent_entry_id",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "parent_entry_id": parent_entry_id,
                },
            )
        )
        return self.directory_child_rows_by_parent.get(parent_entry_id, [])

    def get_file_by_path(self, cursor, *, knowledge_base_id, full_path):
        self.calls.append(
            (
                "get_file_by_path",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "full_path": full_path,
                },
            )
        )
        return self.file_entry_by_path.get(full_path)

    def get_entry_by_id(self, cursor, *, entry_id):
        self.calls.append(("get_entry_by_id", {"entry_id": entry_id}))
        return self.entry_by_id.get(entry_id)

    def get_child_entry(self, cursor, *, knowledge_base_id, parent_entry_id, name):
        self.calls.append(
            (
                "get_child_entry",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "parent_entry_id": parent_entry_id,
                    "name": name,
                },
            )
        )
        return self.child_entry_by_parent_and_name.get(
            (knowledge_base_id, parent_entry_id, name)
        )

    def get_virtual_path_by_entry_id(self, cursor, *, entry_id):
        self.calls.append(("get_virtual_path_by_entry_id", {"entry_id": entry_id}))
        entry = self.entry_by_id.get(entry_id)
        if entry is None:
            return None
        if entry_id == 80:
            return f"考勤制度/{entry['name']}"
        if entry_id == 71:
            return f"考勤制度/{entry['name']}"
        return None

    def list_children(self, cursor, *, parent_path_ltree):
        self.calls.append(("list_children", {"parent_path_ltree": parent_path_ltree}))
        if parent_path_ltree == self.directory_entry["path_ltree"]:
            return list(self.directory_children)
        return []

    def list_child_nodes(self, cursor, *, parent_path_ltree):
        self.calls.append(
            ("list_child_nodes", {"parent_path_ltree": parent_path_ltree})
        )
        return list(self.child_nodes_by_parent.get(parent_path_ltree, []))

    def list_entries_by_path_pattern(
        self, cursor, *, path_regex, ancestor_path_ltree=None
    ):
        self.calls.append(
            (
                "list_entries_by_path_pattern",
                {"path_regex": path_regex, "ancestor_path_ltree": ancestor_path_ltree},
            )
        )
        return list(self.pattern_matches)

    def get_current_file_version_by_entry_id(self, cursor, *, fs_entry_id):
        self.calls.append(
            ("get_current_file_version_by_entry_id", {"fs_entry_id": fs_entry_id})
        )
        if fs_entry_id == 71:
            return {
                "knowledge_base_id": 7,
                "knowledge_item_id": 10,
                "knowledge_item_version_id": 22,
                "kb_code": "hr-policy",
                "full_path": "dir1/doc.md",
                "version": "v1",
                "bucket_name": "knowledge-base",
                "object_key": "kb/7/item/10/version/v1/original",
                "markdown_bucket_name": "knowledge-base-markdown",
                "markdown_object_key": "kb/7/item/10/version/v1/markdown",
                "markdown_file_size": 18,
                "markdown_checksum": "abc123",
                "checksum": "abc123",
                "file_size": 18,
            }
        return None

    def soft_delete_by_knowledge_base_id(self, cursor, *, knowledge_base_id):
        self.calls.append(
            (
                "soft_delete_by_knowledge_base_id",
                {"knowledge_base_id": knowledge_base_id},
            )
        )

    def soft_delete_file_entry(self, cursor, *, knowledge_base_id, fs_entry_id):
        self.calls.append(
            (
                "soft_delete_file_entry",
                {"knowledge_base_id": knowledge_base_id, "fs_entry_id": fs_entry_id},
            )
        )

    def update_markdown_metadata(
        self,
        cursor,
        *,
        fs_entry_id,
        markdown_bucket_name,
        markdown_object_key,
        line_count,
    ):
        self.calls.append(
            (
                "update_markdown_metadata",
                {
                    "fs_entry_id": fs_entry_id,
                    "markdown_bucket_name": markdown_bucket_name,
                    "markdown_object_key": markdown_object_key,
                    "line_count": line_count,
                },
            )
        )


class FakeKnowledgeItemChunkRepository:
    """Repository double for chunks and embeddings."""

    def __init__(self):
        self.calls = []

    def replace_for_version(self, cursor, **kwargs):
        self.calls.append(("replace_for_version", kwargs))
        chunks = kwargs["chunks"]
        return [
            {"kid": 100 + item["chunk_no"], "chunk_no": item["chunk_no"]}
            for item in chunks
        ]

    def replace_embeddings(self, cursor, **kwargs):
        self.calls.append(("replace_embeddings", kwargs))

    def replace_for_fs_entry(self, cursor, *, fs_entry_id, chunks):
        self.calls.append(
            (
                "replace_for_fs_entry",
                {"fs_entry_id": fs_entry_id, "chunk_count": len(chunks)},
            )
        )
        return [
            {"kid": 9000 + i, "chunk_no": chunk["chunk_no"]}
            for i, chunk in enumerate(chunks)
        ]


class FakeRetrievalProjectionRepository:
    """Repository double for the retrieval projection."""

    def __init__(self):
        self.calls = []

    def refresh_for_item(self, cursor, **kwargs):
        self.calls.append(("refresh_for_item", kwargs))

    def delete_for_knowledge_base(self, cursor, **kwargs):
        self.calls.append(("delete_for_knowledge_base", kwargs))

    def delete_for_item(self, cursor, **kwargs):
        self.calls.append(("delete_for_item", kwargs))

    def delete_for_fs_entry_ids(self, cursor, **kwargs):
        self.calls.append(("delete_for_fs_entry_ids", kwargs))

    def refresh_for_fs_entry(
        self, cursor, *, knowledge_base_id, fs_entry_id, full_path
    ):
        self.calls.append(
            (
                "refresh_for_fs_entry",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "fs_entry_id": fs_entry_id,
                    "full_path": full_path,
                },
            )
        )


class FakeObjectStorage:
    """Object storage double tracking promotion and cleanup."""

    def __init__(self):
        self.bucket_name = "knowledge-base"
        self.markdown_bucket_name = "knowledge-base-markdown"
        self.uploaded = []
        self.promoted = []
        self.deleted = []
        self.downloaded = []
        self.object_payloads = {
            (
                "knowledge-base-markdown",
                "kb/7/item/10/version/v1/markdown",
            ): b"line1\nline2\nline3\n",
        }

    def upload_temp_object(
        self, import_request_id, content, *, content_type, bucket_name=None
    ):
        self.uploaded.append((import_request_id, content, content_type, bucket_name))
        return f"tmp/{import_request_id}/content.md"

    def build_original_object_key(
        self, *, knowledge_base_id, knowledge_item_id, version
    ):
        return f"kb/{knowledge_base_id}/item/{knowledge_item_id}/version/{version}/original"

    def build_markdown_object_key(
        self, *, knowledge_base_id, knowledge_item_id, version
    ):
        return f"kb/{knowledge_base_id}/item/{knowledge_item_id}/version/{version}/markdown"

    def promote_temp_object(
        self, temp_object_key, final_object_key, *, bucket_name=None
    ):
        self.promoted.append((temp_object_key, final_object_key, bucket_name))

    def delete_object_quietly(self, object_key, *, bucket_name=None):
        self.deleted.append((object_key, bucket_name))

    def download_object(self, object_key, *, bucket_name=None):
        self.downloaded.append((object_key, bucket_name))
        return self.object_payloads[(bucket_name or self.bucket_name, object_key)]

    def build_access_url(self, object_key, *, expires, bucket_name=None):
        target_bucket = bucket_name or self.bucket_name
        return f"https://minio.example/{target_bucket}/{object_key}?ttl={int(expires.total_seconds())}"


class FakeKnowledgeFetchCacheRepository:
    """Repository double for fetch cache index rows."""

    def __init__(self):
        self.calls = []
        self.entries_by_version_id = {}

    def upsert_cache_entry(self, cursor, **kwargs):
        self.calls.append(("upsert_cache_entry", kwargs))
        self.entries_by_version_id[kwargs["knowledge_item_version_id"]] = {
            "kid": 301,
            "knowledge_item_version_id": kwargs["knowledge_item_version_id"],
            "checksum": kwargs["checksum"],
            "cache_file_path": kwargs["cache_file_path"],
            "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "cache_status": "READY",
        }
        return {"kid": 301}

    def get_by_version_id(self, cursor, *, knowledge_item_version_id):
        self.calls.append(
            (
                "get_by_version_id",
                {"knowledge_item_version_id": knowledge_item_version_id},
            )
        )
        return self.entries_by_version_id.get(knowledge_item_version_id)

    def touch_cache_entry(self, cursor, *, cache_entry_id, cache_ttl_seconds):
        self.calls.append(
            (
                "touch_cache_entry",
                {
                    "cache_entry_id": cache_entry_id,
                    "cache_ttl_seconds": cache_ttl_seconds,
                },
            )
        )


def test_create_knowledge_base_commits_and_returns_business_fields():
    """Knowledge base creation should generate kb_code from the persisted row id."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(default_lookup_result=None)
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.create_knowledge_base(
        CreateKnowledgeBaseRequest(
            kb_name="人力制度知识库",
            kb_description="公司人事制度与流程文档",
        )
    )

    assert response.kb_code == "7"
    assert response.kb_description == "公司人事制度与流程文档"
    assert connection.committed is True
    assert knowledge_base_repository.calls[0] == (
        "get_by_name",
        {"kb_name": "人力制度知识库"},
    )
    assert knowledge_base_repository.calls[1][0] == "create_knowledge_base"
    assert knowledge_fs_entry_repository.calls == []


def test_create_knowledge_base_rejects_duplicate_name():
    """Knowledge base creation should reject duplicate kb names."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(default_lookup_result=None)
    knowledge_base_repository.existing_by_name["人力制度知识库"] = {
        "kid": 9,
        "kb_name": "人力制度知识库",
    }
    fs_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=fs_repository,
    )

    try:
        service.create_knowledge_base(
            CreateKnowledgeBaseRequest(
                kb_name="人力制度知识库",
                kb_description="公司人事制度与流程文档",
            )
        )
        raise AssertionError("expected KnowledgeBaseValidationError")
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "knowledge base name already exists: 人力制度知识库"

    assert connection.committed is False
    assert connection.rolled_back is True
    assert knowledge_base_repository.calls == [
        ("get_by_name", {"kb_name": "人力制度知识库"})
    ]
    assert fs_repository.calls == []


def test_delete_knowledge_base_marks_kb_and_descendants_deleted():
    """Deleting one knowledge base should logically delete the KB, fs entries, and retrieval rows."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "id": 7,
            "kb_code": "hr-policy",
            "kb_name": "人力制度知识库",
            "status": "ACTIVE",
            "is_deleted": False,
        }
    )
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.delete_knowledge_base(
        DeleteKnowledgeBaseRequest(kb_code="hr-policy")
    )

    assert response.kb_code == "hr-policy"
    assert response.is_deleted is True
    assert connection.committed is True
    assert (
        "soft_delete_by_code",
        {"kb_code": "hr-policy"},
    ) in knowledge_base_repository.calls
    assert (
        "soft_delete_by_knowledge_base_id",
        {"knowledge_base_id": 7},
    ) in knowledge_fs_entry_repository.calls
    assert (
        """
                DELETE FROM knowledge_chunk_retrieval_mv
                WHERE knowledge_base_id = %(knowledge_base_id)s
                """,
        {"knowledge_base_id": 7},
    ) in connection.cursor_obj.executed


def test_update_knowledge_base_commits_and_returns_success():
    """Updating a KB should persist documented fields and commit."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "kid": 7,
            "kb_code": "hr-policy",
            "kb_name": "人力制度知识库",
            "kb_description": "旧描述",
            "status": "ACTIVE",
            "is_deleted": False,
        }
    )
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.update_knowledge_base(
        UpdateKnowledgeBaseRequest(
            knCode="hr-policy",
            knName="新知识库名称",
            knDescription="新描述",
        )
    )

    assert response.kb_code == "hr-policy"
    assert connection.committed is True
    assert ("get_by_code", {"kb_code": "hr-policy"}) in knowledge_base_repository.calls
    assert (
        "update_knowledge_base",
        {
            "kb_code": "hr-policy",
            "updates": {
                "kb_name": "新知识库名称",
                "kb_description": "新描述",
            },
        },
    ) in knowledge_base_repository.calls
    assert not any(
        call[0] == "rename_entry" for call in knowledge_fs_entry_repository.calls
    )


def test_update_knowledge_base_rejects_missing_kb():
    """Updating a KB should fail when kb_code does not exist."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(default_lookup_result=None)
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
    )

    try:
        service.update_knowledge_base(
            UpdateKnowledgeBaseRequest(
                knCode="missing-kb",
                knName="新知识库名称",
            )
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "knowledge base not found: missing-kb"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert connection.rolled_back is True


def test_update_knowledge_base_rejects_duplicate_name():
    """Updating a KB should reject duplicate kb names."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "kid": 7,
            "kb_code": "hr-policy",
            "kb_name": "人力制度知识库",
            "kb_description": "旧描述",
            "status": "ACTIVE",
            "is_deleted": False,
        }
    )
    knowledge_base_repository.existing_by_name["法务制度知识库"] = {
        "kid": 8,
        "kb_name": "法务制度知识库",
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
    )

    try:
        service.update_knowledge_base(
            UpdateKnowledgeBaseRequest(
                knCode="hr-policy",
                knName="法务制度知识库",
            )
        )
        raise AssertionError("expected KnowledgeBaseValidationError")
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "knowledge base name already exists: 法务制度知识库"

    assert connection.committed is False
    assert connection.rolled_back is True


def test_update_knowledge_base_keeps_omitted_fields_unchanged():
    """Omitted fields should not be overwritten when updating a KB."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "kid": 7,
            "kb_code": "hr-policy",
            "kb_name": "人力制度知识库",
            "kb_description": "旧描述",
            "status": "ACTIVE",
            "is_deleted": False,
            "root_entry_id": 70,
            "metadata": {"owner": "old", "lang": "zh-CN"},
        }
    )
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.update_knowledge_base(
        UpdateKnowledgeBaseRequest(
            kb_code="hr-policy",
            kb_name="新知识库名称",
        )
    )

    assert response.kb_description == "旧描述"
    assert (
        "update_knowledge_base",
        {
            "kb_code": "hr-policy",
            "updates": {"kb_name": "新知识库名称"},
        },
    ) in knowledge_base_repository.calls


def test_update_knowledge_base_clears_fields_only_when_null_is_explicit():
    """Explicit null should clear mutable nullable fields."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "kid": 7,
            "kb_code": "hr-policy",
            "kb_name": "人力制度知识库",
            "kb_description": "旧描述",
            "status": "ACTIVE",
            "is_deleted": False,
            "root_entry_id": 70,
            "metadata": {"owner": "old"},
        }
    )
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
    )

    response = service.update_knowledge_base(
        UpdateKnowledgeBaseRequest(
            kb_code="hr-policy",
            kb_description=None,
        )
    )

    assert response.kb_description is None
    assert (
        "update_knowledge_base",
        {
            "kb_code": "hr-policy",
            "updates": {"kb_description": None},
        },
    ) in knowledge_base_repository.calls


def test_create_directory_commits_and_returns_business_fields():
    """Directory creation should commit and return only business fields."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "kid": 7,
            "kb_code": "hr-policy",
            "kb_name": "人力制度知识库",
            "kb_description": None,
            "is_deleted": False,
        }
    )
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.create_directory(
        CreateDirectoryRequest(
            knCode="hr-policy",
            directoryPath="/考勤制度/归档",
            directoryDescription="考勤制度归档目录",
        )
    )

    assert response.kb_code == "hr-policy"
    assert response.directory_path == "/考勤制度/归档"
    assert response.directory_description == "考勤制度归档目录"
    assert connection.committed is True
    assert (
        "create_directory_entry",
        {
            "knowledge_base_id": 7,
            "full_path": "考勤制度/归档",
            "directory_description": "考勤制度归档目录",
        },
    ) in knowledge_fs_entry_repository.calls


def test_create_directory_supports_recursive_creation():
    """Directory creation should support recursive parent creation."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "kid": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "kb_description": None,
                "is_deleted": False,
            }
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.create_directory(
        CreateDirectoryRequest(
            knCode="hr-policy",
            directoryPath="/missing-dir/归档",
            directoryDescription=None,
        )
    )

    assert response.directory_path == "/missing-dir/归档"
    assert connection.committed is True


def test_delete_directory_marks_subtree_deleted_and_clears_projection():
    """Deleting a directory should logically delete its subtree and retrieval rows."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.directory_entry = {
        "kid": 81,
        "knowledge_base_id": 7,
        "parent_entry_id": None,
        "name": "归档",
        "entry_type": "DIRECTORY",
        "full_path": "考勤制度/归档",
        "path_ltree": "d1_a.d2_b",
    }
    retrieval_projection_repository = FakeRetrievalProjectionRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "kid": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
                "is_deleted": False,
                "root_entry_id": 70,
                "metadata": {},
            }
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        retrieval_projection_repository=retrieval_projection_repository,
    )

    response = service.delete_directory(
        DeleteDirectoryRequest(
            knCode="hr-policy",
            directoryPath="/考勤制度/归档",
        )
    )

    assert response.kb_code == "hr-policy"
    assert response.directory_path == "/考勤制度/归档"
    assert response.is_deleted is True
    assert connection.committed is True
    assert (
        "get_directory_by_path",
        {"knowledge_base_id": 7, "full_path": "考勤制度/归档"},
    ) in knowledge_fs_entry_repository.calls
    assert (
        "list_subtree_entry_ids",
        {"knowledge_base_id": 7, "root_fs_entry_id": 81},
    ) in knowledge_fs_entry_repository.calls
    assert (
        "soft_delete_subtree",
        {"knowledge_base_id": 7, "root_fs_entry_id": 81},
    ) in knowledge_fs_entry_repository.calls
    assert connection.cursor_obj.executed[-1] == (
        """
                DELETE FROM knowledge_chunk_retrieval_mv
                WHERE knowledge_base_id = %(knowledge_base_id)s
                  AND fs_entry_id = ANY(%(fs_entry_ids)s)
                """,
        {"knowledge_base_id": 7, "fs_entry_ids": [81, 82, 83]},
    )


def test_delete_directory_rejects_missing_directory():
    """Deleting a directory should fail when the path does not exist."""
    connection = FakeConnection()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "kid": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
                "is_deleted": False,
                "root_entry_id": 70,
                "metadata": {},
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
    )

    try:
        service.delete_directory(
            DeleteDirectoryRequest(
                knCode="hr-policy",
                directoryPath="/考勤制度/归档",
            )
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "directory not found: /考勤制度/归档"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert connection.rolled_back is True


def test_update_directory_renames_directory_by_path():
    """Updating a directory should rename the matched path entry."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.directory_entry = {
        "kid": 80,
        "knowledge_base_id": 7,
        "parent_entry_id": None,
        "name": "归档",
        "entry_type": "DIRECTORY",
        "full_path": "考勤制度/归档",
        "path_ltree": "d1_a.d2_b",
    }
    knowledge_fs_entry_repository.entry_by_id[80] = {
        "kid": 80,
        "knowledge_base_id": 7,
        "parent_entry_id": None,
        "name": "历史归档",
        "entry_type": "DIRECTORY",
        "path_ltree": "d1_a.d2_b",
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "kid": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
                "is_deleted": False,
                "root_entry_id": 70,
                "metadata": {},
            }
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.update_directory(
        UpdateDirectoryRequest(
            knCode="hr-policy",
            directoryPath="/考勤制度/归档",
            directoryName="历史归档",
        )
    )

    assert response.kb_code == "hr-policy"
    assert response.directory_path == "/考勤制度/历史归档"
    assert response.directory_name == "历史归档"
    assert connection.committed is True
    assert (
        "get_directory_by_path",
        {"knowledge_base_id": 7, "full_path": "考勤制度/归档"},
    ) in knowledge_fs_entry_repository.calls
    assert (
        "rename_entry",
        {"entry_id": 80, "new_name": "历史归档"},
    ) in knowledge_fs_entry_repository.calls


def test_update_directory_allows_same_name_without_conflict():
    """Renaming to the current name should not trigger a sibling conflict."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.directory_entry = {
        "kid": 80,
        "knowledge_base_id": 7,
        "parent_entry_id": None,
        "name": "归档",
        "entry_type": "DIRECTORY",
        "full_path": "考勤制度/归档",
        "path_ltree": "d1_a.d2_b",
    }
    knowledge_fs_entry_repository.entry_by_id[80] = {
        "kid": 80,
        "knowledge_base_id": 7,
        "parent_entry_id": None,
        "name": "归档",
        "entry_type": "DIRECTORY",
        "path_ltree": "d1_a.d2_b",
    }
    knowledge_fs_entry_repository.child_entry_by_parent_and_name[(7, None, "归档")] = {
        "kid": 80,
        "knowledge_base_id": 7,
        "parent_entry_id": None,
        "name": "归档",
        "entry_type": "DIRECTORY",
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "kid": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
                "is_deleted": False,
                "root_entry_id": 70,
                "metadata": {},
            }
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.update_directory(
        UpdateDirectoryRequest(
            knCode="hr-policy",
            directoryPath="/考勤制度/归档",
            directoryName="归档",
        )
    )

    assert response.directory_name == "归档"
    assert connection.committed is True


def test_update_directory_rejects_sibling_name_conflict():
    """Updating a directory should reject sibling name conflicts."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.directory_entry = {
        "kid": 80,
        "knowledge_base_id": 7,
        "parent_entry_id": None,
        "name": "归档",
        "entry_type": "DIRECTORY",
        "full_path": "考勤制度/归档",
        "path_ltree": "d1_a.d2_b",
    }
    knowledge_fs_entry_repository.child_entry_by_parent_and_name[
        (7, None, "历史归档")
    ] = {
        "kid": 82,
        "knowledge_base_id": 7,
        "parent_entry_id": None,
        "name": "历史归档",
        "entry_type": "DIRECTORY",
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "kid": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
                "is_deleted": False,
                "root_entry_id": 70,
                "metadata": {},
            }
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    try:
        service.update_directory(
            UpdateDirectoryRequest(
                knCode="hr-policy",
                directoryPath="/考勤制度/归档",
                directoryName="历史归档",
            )
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "directory name already exists under parent: 历史归档"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert connection.rolled_back is True


def test_delete_knowledge_item_marks_file_entry_deleted_and_clears_artifacts():
    """Deleting one file should logically delete the file entry and clear derived artifacts."""
    connection = FakeConnection()
    object_storage = FakeObjectStorage()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.file_entry_by_path["Policies/delete.md"] = {
        "kid": 71,
        "knowledge_base_id": 7,
        "parent_entry_id": None,
        "entry_type": "FILE",
        "name": "delete.md",
        "path_ltree": "d1_a.f2_b",
        "depth": 2,
        "file_bucket_name": "knowledge-base",
        "file_object_key": "kb/7/fs-entry/71/original.md",
        "markdown_bucket_name": "knowledge-base-markdown",
        "markdown_object_key": "kb/7/fs-entry/71/markdown.md",
    }
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
                "is_deleted": False,
            }
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=object_storage,
        embedding_dimension=2,
    )

    response = service.delete_knowledge_item(
        DeleteKnowledgeItemRequest(kb_code="hr-policy", file_path="/Policies/delete.md")
    )

    assert response.kb_code == "hr-policy"
    assert response.file_path == "/Policies/delete.md"
    assert response.is_deleted is True
    assert connection.committed is True
    assert (
        "get_file_by_path",
        {"knowledge_base_id": 7, "full_path": "Policies/delete.md"},
    ) in knowledge_fs_entry_repository.calls
    assert (
        "soft_delete_file_entry",
        {"knowledge_base_id": 7, "fs_entry_id": 71},
    ) in knowledge_fs_entry_repository.calls
    assert any(
        "delete from knowledge_chunk_retrieval_mv" in sql.lower()
        and params == {"knowledge_base_id": 7, "fs_entry_id": 71}
        for sql, params in connection.cursor_obj.executed
    )
    assert any(
        "delete from knowledge_fetch_cache_index" in sql.lower()
        and params == {"knowledge_base_id": 7, "fs_entry_id": 71}
        for sql, params in connection.cursor_obj.executed
    )
    assert ("kb/7/fs-entry/71/original.md", "knowledge-base") in object_storage.deleted
    assert (
        "kb/7/fs-entry/71/markdown.md",
        "knowledge-base-markdown",
    ) in object_storage.deleted


def test_create_knowledge_base_emits_internal_key_node_logs(monkeypatch):
    """Knowledge base creation should log persistence and commit steps."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(default_lookup_result=None)
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
    )
    info_messages: list[str] = []

    monkeypatch.setattr(
        logger,
        "info",
        lambda message, *args, **kwargs: info_messages.append(
            message % args if args else message
        ),
    )

    response = service.create_knowledge_base(
        CreateKnowledgeBaseRequest(
            kb_name="人力制度知识库",
            kb_description=None,
        )
    )

    assert response.kb_code == "7"
    assert service.knowledge_fs_entry_repository.calls == []
    assert info_messages == [
        "knowledge_base_service.create_knowledge_base started: kb_name=人力制度知识库, has_description=False",
        "knowledge_base_service persistence finished: knowledge_base_id=7",
        "knowledge_base_service transaction committed: knowledge_base_id=7",
    ]


def test_upload_file_commits_object_and_updates_fs_entry_storage():
    """Multipart upload should only persist original file metadata on the fs entry."""
    connection = FakeConnection()
    storage = FakeObjectStorage()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
            }
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=storage,
        embedding_dimension=2,
    )

    response = service.upload_file(
        KnowledgeItemUploadRequest(
            knCode="hr-policy",
            filePath="/dir1/item-1.pdf",
            fileDescription="操作手册",
            fileContent=b"pdf-bytes",
            fileName="item-1.pdf",
            contentType="application/pdf",
        )
    )

    assert response.model_dump() == {
        "kb_code": "hr-policy",
        "file_path": "/dir1/item-1.pdf",
        "file_description": "操作手册",
    }
    assert connection.committed is True
    assert (
        "create_file_entry",
        {
            "knowledge_base_id": 7,
            "full_path": "dir1/item-1.pdf",
            "file_description": "操作手册",
        },
    ) in knowledge_fs_entry_repository.calls
    storage_call = [
        call
        for call in knowledge_fs_entry_repository.calls
        if call[0] == "update_file_entry_storage"
    ][0]
    assert storage_call[1]["fs_entry_id"] == 71
    assert storage_call[1]["file_bucket_name"] == "knowledge-base"
    assert storage_call[1]["file_size"] == len(b"pdf-bytes")
    assert storage_call[1]["mime_type"] == "application/pdf"
    assert storage.promoted == [
        (
            "tmp/upload-7-71/content.md",
            "kb/7/fs-entry/71/original.pdf",
            "knowledge-base",
        )
    ]


def test_upload_file_recursively_creates_missing_parent_directories():
    """Multipart upload should recursively create missing parent directories."""
    connection = FakeConnection()
    storage = FakeObjectStorage()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
            }
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=storage,
        embedding_dimension=2,
    )

    response = service.upload_file(
        KnowledgeItemUploadRequest(
            knCode="hr-policy",
            filePath="/missing-dir/item-1.pdf",
            fileContent=b"pdf-bytes",
            fileName="item-1.pdf",
            contentType="application/pdf",
        )
    )

    assert response.file_path == "/missing-dir/item-1.pdf"
    assert connection.committed is True
    assert (
        "create_file_entry",
        {
            "knowledge_base_id": 7,
            "full_path": "missing-dir/item-1.pdf",
            "file_description": None,
        },
    ) in knowledge_fs_entry_repository.calls
    assert storage.promoted == [
        (
            "tmp/upload-7-71/content.md",
            "kb/7/fs-entry/71/original.pdf",
            "knowledge-base",
        )
    ]


def test_list_dir_root_returns_top_level_entries():
    """Root listing should return top-level entries inside the requested knowledge base."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.list_dir(
        KnowledgeItemListDirRequest(kb_code="hr-policy", directory_path="/")
    )

    assert response.model_dump()["items"] == [
        {"kb_code": "hr-policy", "name": "/dir1", "type": "directory", "size": 0}
    ]
    assert knowledge_fs_entry_repository.calls == [
        (
            "list_children_by_parent_entry_id",
            {"knowledge_base_id": 7, "parent_entry_id": None},
        )
    ]


def test_list_dir_directory_path_returns_direct_children_only():
    """Directory path should resolve the directory and list its direct children."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.list_dir(
        KnowledgeItemListDirRequest(kb_code="hr-policy", directory_path="/dir1")
    )

    assert response.model_dump()["items"] == [
        {"kb_code": "hr-policy", "name": "/dir1/doc.md", "type": "file", "size": 128},
        {
            "kb_code": "hr-policy",
            "name": "/dir1/subdir",
            "type": "directory",
            "size": 0,
        },
    ]
    assert knowledge_fs_entry_repository.calls == [
        (
            "get_directory_by_path",
            {"knowledge_base_id": 7, "full_path": "dir1"},
        ),
        (
            "list_children_by_parent_entry_id",
            {"knowledge_base_id": 7, "parent_entry_id": 80},
        ),
    ]


def test_list_dir_literal_missing_path_raises_not_found():
    """List-dir should raise not-found when the requested literal path does not exist."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    try:
        service.list_dir(
            KnowledgeItemListDirRequest(kb_code="hr-policy", directory_path="/missing")
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "directory not found: /missing"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert knowledge_fs_entry_repository.calls == [
        (
            "get_directory_by_path",
            {"knowledge_base_id": 7, "full_path": "missing"},
        )
    ]


def test_glob_pattern_matches_one_segment_at_a_time():
    """Glob should match pathRule with * limited to one path segment."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.glob(
        KnowledgeItemGlobRequest(kb_code="hr-policy", path_rule="/dir1/*.md")
    )

    assert response.model_dump()["items"] == [
        {
            "kb_code": "hr-policy",
            "name": "/dir1/doc.md",
            "type": "file",
            "size": 128,
        }
    ]
    assert knowledge_fs_entry_repository.calls == [
        (
            "list_children_by_parent_entry_id",
            {"knowledge_base_id": 7, "parent_entry_id": None},
        ),
        (
            "list_children_by_parent_entry_id",
            {"knowledge_base_id": 7, "parent_entry_id": 80},
        ),
    ]


def test_glob_rejects_double_star_multi_level_matching():
    """Glob should reject ** because only single-level * matching is supported."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    try:
        service.glob(
            KnowledgeItemGlobRequest(kb_code="hr-policy", path_rule="/dir1/**/*.md")
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "pathRule does not support ** multi-level matching"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert knowledge_fs_entry_repository.calls == []


def test_list_dir_raises_when_knowledge_base_is_missing():
    """List-dir should fail when knCode does not resolve to a knowledge base."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    try:
        service.list_dir(
            KnowledgeItemListDirRequest(kb_code="missing-kb", directory_path="/")
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "knowledge base not found: missing-kb"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert knowledge_fs_entry_repository.calls == []


def test_download_file_returns_original_bytes(tmp_path):
    """Download-file should fetch the current original object bytes."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    storage.object_payloads[("knowledge-base", "kb/7/fs-entry/71/original.pdf")] = (
        b"%PDF-1.4 binary payload"
    )
    cache_repository = FakeKnowledgeFetchCacheRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        object_storage=storage,
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )
    knowledge_fs_entry_repository.file_entry_by_path["dir1/doc.pdf"] = {
        "kid": 71,
        "knowledge_base_id": 7,
        "parent_entry_id": 80,
        "entry_type": "FILE",
        "name": "doc.pdf",
        "path_ltree": "d1_a.f2_doc",
        "depth": 2,
        "file_bucket_name": "knowledge-base",
        "file_object_key": "kb/7/fs-entry/71/original.pdf",
        "mime_type": "application/pdf",
        "file_size": 128,
    }

    response = service.download_file(
        KnowledgeItemDownloadRequest(
            kb_code="hr-policy",
            file_path="/dir1/doc.pdf",
        )
    )

    assert response == {
        "filename": "doc.pdf",
        "media_type": "application/pdf",
        "content": b"%PDF-1.4 binary payload",
    }
    assert knowledge_fs_entry_repository.calls == [
        (
            "get_file_by_path",
            {"knowledge_base_id": 7, "full_path": "dir1/doc.pdf"},
        )
    ]
    assert storage.downloaded == [("kb/7/fs-entry/71/original.pdf", "knowledge-base")]
    assert cache_repository.calls == []


# ---------------------------------------------------------------------------
# FakeDocumentChunkingService & fileToMarkdownIndex tests
# ---------------------------------------------------------------------------


class FakeDocumentChunkingService:
    """Fake document chunking service for testing."""

    def __init__(self):
        self.extract_calls = []
        self.chunk_calls = []
        self.markdown_result = "# Test Document\n\nThis is test content."
        self.chunks_result = [
            KnowledgeItemChunkPayload(
                chunk_no=1,
                start_line=1,
                end_line=3,
                chunk_text="# Test Document\n\nThis is test content.",
                embedding=[0.1, 0.2, 0.3],
            )
        ]

    def extract_text_from_file(self, file_bytes, file_type):
        self.extract_calls.append({"file_type": file_type, "size": len(file_bytes)})
        return self.markdown_result

    def chunk_and_embed(self, file_bytes, *, filename):
        self.chunk_calls.append({"filename": filename, "size": len(file_bytes)})
        return self.chunks_result


def test_file_to_markdown_index_kb_not_found():
    """fileToMarkdownIndex returns error when knowledge base does not exist."""
    import pytest

    kb_repo = FakeKnowledgeBaseRepository(default_lookup_result=None)
    fs_repo = FakeKnowledgeFsEntryRepository()
    service = KnowledgeItemIngestionService(
        connection_factory=FakeConnection,
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=3,
    )
    request = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "999", "filePath": "/doc.pdf"}
    )
    with pytest.raises(KnowledgeBaseValidationError, match="knowledge base not found"):
        service.file_to_markdown_index(
            request, document_chunking_service=FakeDocumentChunkingService()
        )


def test_file_to_markdown_index_file_not_found():
    """fileToMarkdownIndex returns error when file does not exist."""
    import pytest

    kb_repo = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "kid": 7,
            "kb_code": "1",
            "kb_name": "TestKB",
            "status": "ACTIVE",
        }
    )
    fs_repo = FakeKnowledgeFsEntryRepository()
    fs_repo.file_entry_by_path = {}
    service = KnowledgeItemIngestionService(
        connection_factory=FakeConnection,
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=3,
    )
    request = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "1", "filePath": "/nonexistent.pdf"}
    )
    with pytest.raises(KnowledgeBaseValidationError, match="file not found"):
        service.file_to_markdown_index(
            request, document_chunking_service=FakeDocumentChunkingService()
        )


def test_file_to_markdown_index_file_not_uploaded():
    """fileToMarkdownIndex returns error when file has no uploaded content."""
    import pytest

    kb_repo = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "kid": 7,
            "kb_code": "1",
            "kb_name": "TestKB",
            "status": "ACTIVE",
        }
    )
    fs_repo = FakeKnowledgeFsEntryRepository()
    fs_repo.file_entry_by_path = {
        "制度/人事/请假制度.pdf": {
            "kid": 71,
            "entry_type": "FILE",
            "name": "请假制度.pdf",
            "file_bucket_name": None,
            "file_object_key": None,
            "mime_type": None,
        }
    }
    service = KnowledgeItemIngestionService(
        connection_factory=FakeConnection,
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=3,
    )
    request = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"}
    )
    with pytest.raises(
        KnowledgeBaseValidationError, match="file has not been uploaded"
    ):
        service.file_to_markdown_index(
            request, document_chunking_service=FakeDocumentChunkingService()
        )


def test_file_to_markdown_index_success():
    """fileToMarkdownIndex completes full pipeline successfully."""
    kb_repo = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "kid": 7,
            "kb_code": "1",
            "kb_name": "TestKB",
            "status": "ACTIVE",
        }
    )
    fs_repo = FakeKnowledgeFsEntryRepository()
    fs_repo.file_entry_by_path = {
        "制度/人事/请假制度.pdf": {
            "kid": 71,
            "entry_type": "FILE",
            "name": "请假制度.pdf",
            "file_bucket_name": "test-bucket",
            "file_object_key": "kb/7/fs-entry/71/original.pdf",
            "mime_type": "application/pdf",
        }
    }
    chunk_repo = FakeKnowledgeItemChunkRepository()
    retrieval_repo = FakeRetrievalProjectionRepository()
    obj_storage = FakeObjectStorage()
    obj_storage.object_payloads[("test-bucket", "kb/7/fs-entry/71/original.pdf")] = (
        b"fake-pdf-bytes"
    )
    chunking_service = FakeDocumentChunkingService()
    service = KnowledgeItemIngestionService(
        connection_factory=FakeConnection,
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_item_chunk_repository=chunk_repo,
        retrieval_projection_repository=retrieval_repo,
        object_storage=obj_storage,
        embedding_dimension=3,
    )
    request = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"}
    )
    service.file_to_markdown_index(request, document_chunking_service=chunking_service)

    # Verify document chunking was called
    assert len(chunking_service.extract_calls) == 1
    assert chunking_service.extract_calls[0]["file_type"] == "pdf"
    assert len(chunking_service.chunk_calls) == 1

    # Verify chunks were persisted
    chunk_calls = [c for c in chunk_repo.calls if c[0] == "replace_for_fs_entry"]
    assert len(chunk_calls) == 1
    assert chunk_calls[0][1]["fs_entry_id"] == 71

    # Verify retrieval projection was refreshed
    refresh_calls = [c for c in retrieval_repo.calls if c[0] == "refresh_for_fs_entry"]
    assert len(refresh_calls) == 1
    assert refresh_calls[0][1]["fs_entry_id"] == 71
    assert refresh_calls[0][1]["full_path"] == "制度/人事/请假制度.pdf"

    # Verify markdown metadata was updated on fs_entry
    md_calls = [c for c in fs_repo.calls if c[0] == "update_markdown_metadata"]
    assert len(md_calls) == 1
    assert md_calls[0][1]["fs_entry_id"] == 71
