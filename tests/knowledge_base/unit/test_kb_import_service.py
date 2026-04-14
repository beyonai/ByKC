"""Tests for knowledge-base services transactional behavior."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import (
    CreateDirectoryRequest,
    CreateKnowledgeBaseRequest,
    DeleteDirectoryRequest,
    DeleteKnowledgeBaseRequest,
    DeleteKnowledgeItemRequest,
    KnowledgeItemDownloadRequest,
    KnowledgeItemFetchRequest,
    KnowledgeItemGlobRequest,
    KnowledgeItemImportManifest,
    KnowledgeItemImportRequest,
    KnowledgeItemListDirRequest,
    KnowledgeItemUploadRequest,
    UpdateDirectoryRequest,
    UpdateFileRequest,
    UpdateKnowledgeBaseRequest,
    WriteFileRequest,
    WriteIndexRequest,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService
from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    KnowledgeItemIngestionService,
)


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
        if full_path == self.directory_entry["full_path"]:
            return self.directory_entry
        return None

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


class FakeKnowledgeItemRepository:
    """Repository double for knowledge items."""

    def __init__(self):
        self.calls = []
        self.existing = None

    def upsert(self, cursor, **kwargs):
        self.calls.append(("upsert", kwargs))
        return {"kid": 10}

    def get_by_fs_entry_id(self, cursor, *, knowledge_base_id, fs_entry_id):
        self.calls.append(
            (
                "get_by_fs_entry_id",
                {"knowledge_base_id": knowledge_base_id, "fs_entry_id": fs_entry_id},
            )
        )
        return self.existing

    def get_by_item_code(self, cursor, *, knowledge_base_id, item_code):
        self.calls.append(
            (
                "get_by_item_code",
                {"knowledge_base_id": knowledge_base_id, "item_code": item_code},
            )
        )
        return self.existing

    def get_any_by_fs_entry_id(self, cursor, *, knowledge_base_id, fs_entry_id):
        self.calls.append(
            (
                "get_any_by_fs_entry_id",
                {"knowledge_base_id": knowledge_base_id, "fs_entry_id": fs_entry_id},
            )
        )
        return self.existing

    def get_any_by_item_code(self, cursor, *, knowledge_base_id, item_code):
        self.calls.append(
            (
                "get_any_by_item_code",
                {"knowledge_base_id": knowledge_base_id, "item_code": item_code},
            )
        )
        return self.existing

    def update_current_version(self, cursor, **kwargs):
        self.calls.append(("update_current_version", kwargs))

    def soft_delete_by_knowledge_base_id(self, cursor, *, knowledge_base_id):
        self.calls.append(
            (
                "soft_delete_by_knowledge_base_id",
                {"knowledge_base_id": knowledge_base_id},
            )
        )

    def soft_delete_by_item_code(self, cursor, *, knowledge_base_id, item_code):
        self.calls.append(
            (
                "soft_delete_by_item_code",
                {"knowledge_base_id": knowledge_base_id, "item_code": item_code},
            )
        )

    def soft_delete_by_fs_entry_ids(self, cursor, *, knowledge_base_id, fs_entry_ids):
        self.calls.append(
            (
                "soft_delete_by_fs_entry_ids",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "fs_entry_ids": fs_entry_ids,
                },
            )
        )

    def update_knowledge_item(self, cursor, *, knowledge_base_id, item_code, updates):
        self.calls.append(
            (
                "update_knowledge_item",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "item_code": item_code,
                    "updates": updates,
                },
            )
        )
        if self.existing is not None:
            self.existing.update(updates)


class FakeKnowledgeItemVersionRepository:
    """Repository double for knowledge item versions."""

    def __init__(self):
        self.calls = []
        self.existing = None
        self.version_row = None

    def upsert(self, cursor, **kwargs):
        self.calls.append(("upsert", kwargs))
        return {"kid": 22}

    def get_by_item_and_version(self, cursor, *, knowledge_item_id, version):
        self.calls.append(
            (
                "get_by_item_and_version",
                {"knowledge_item_id": knowledge_item_id, "version": version},
            )
        )
        return self.version_row or self.existing


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


def build_manifest() -> KnowledgeItemImportManifest:
    """Construct a valid import manifest for reuse."""
    return KnowledgeItemImportManifest(
        kb_code="hr-policy",
        document={
            "item_code": "item-1",
            "full_path": "dir1/item-1.md",
            "title": "操作手册.pdf",
            "status": "ACTIVE",
            "source_code": "oa",
            "type_code": "policy_markdown",
            "version": "v1",
        },
        chunks=[
            {
                "chunk_no": 1,
                "start_line": 1,
                "end_line": 10,
                "chunk_text": "hello",
                "embedding": [0.1, 0.2],
            },
            {
                "chunk_no": 2,
                "start_line": 11,
                "end_line": 20,
                "chunk_text": "world",
                "embedding": [0.3, 0.4],
            },
        ],
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


def test_update_file_renames_file_and_updates_metadata():
    """Updating a file should rename the fs entry and persist item metadata fields."""
    connection = FakeConnection()
    knowledge_item_repository = FakeKnowledgeItemRepository()
    knowledge_item_repository.existing = {
        "kid": 11,
        "knowledge_base_id": 7,
        "fs_entry_id": 71,
        "item_code": "attendance-policy-pdf",
        "item_kind": "FILE",
        "description": "旧文件说明",
        "metadata": {"owner": "old"},
        "status": "ACTIVE",
        "type_code": "pdf",
        "is_deleted": False,
    }
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.entry_by_id[71] = {
        "kid": 71,
        "knowledge_base_id": 7,
        "parent_entry_id": 70,
        "name": "异常考勤处理办法.pdf",
        "entry_type": "FILE",
        "path_ltree": "kb_7.f1_doc",
        "is_root": False,
        "depth": 1,
    }
    service = KnowledgeBaseService(
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
        knowledge_item_repository=knowledge_item_repository,
    )

    response = service.update_file(
        UpdateFileRequest(
            kb_code="hr-policy",
            file_code="attendance-policy-pdf",
            file_name="异常考勤处理办法（正式版）.pdf",
            file_description="新文件说明",
            metadata={"owner": "HR"},
        )
    )

    assert response.kb_code == "hr-policy"
    assert response.file_code == "attendance-policy-pdf"
    assert response.file_path == "/考勤制度/异常考勤处理办法（正式版）.pdf"
    assert response.file_description == "新文件说明"
    assert response.metadata == {"owner": "HR"}
    assert connection.committed is True
    assert (
        knowledge_fs_entry_repository.entry_by_id[71]["name"]
        == "异常考勤处理办法（正式版）.pdf"
    )
    assert (
        "rename_entry",
        {"entry_id": 71, "new_name": "异常考勤处理办法（正式版）.pdf"},
    ) in knowledge_fs_entry_repository.calls
    assert (
        "update_knowledge_item",
        {
            "knowledge_base_id": 7,
            "item_code": "attendance-policy-pdf",
            "updates": {"description": "新文件说明", "metadata": {"owner": "HR"}},
        },
    ) in knowledge_item_repository.calls


def test_update_file_keeps_omitted_fields_unchanged():
    """Omitted file fields should remain unchanged during partial updates."""
    connection = FakeConnection()
    knowledge_item_repository = FakeKnowledgeItemRepository()
    knowledge_item_repository.existing = {
        "kid": 11,
        "knowledge_base_id": 7,
        "fs_entry_id": 71,
        "item_code": "attendance-policy-pdf",
        "item_kind": "FILE",
        "description": "旧文件说明",
        "metadata": {"owner": "old"},
        "status": "ACTIVE",
        "type_code": "pdf",
        "is_deleted": False,
    }
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.entry_by_id[71] = {
        "kid": 71,
        "knowledge_base_id": 7,
        "parent_entry_id": 70,
        "name": "异常考勤处理办法.pdf",
        "entry_type": "FILE",
        "path_ltree": "kb_7.f1_doc",
        "is_root": False,
        "depth": 1,
    }
    service = KnowledgeBaseService(
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
        knowledge_item_repository=knowledge_item_repository,
    )

    response = service.update_file(
        UpdateFileRequest(
            kb_code="hr-policy",
            file_code="attendance-policy-pdf",
            metadata={"owner": "HR"},
        )
    )

    assert response.file_path == "/考勤制度/异常考勤处理办法.pdf"
    assert response.file_description == "旧文件说明"
    assert response.metadata == {"owner": "HR"}
    assert (
        knowledge_fs_entry_repository.calls.count(
            (
                "rename_entry",
                {"entry_id": 71, "new_name": "异常考勤处理办法（正式版）.pdf"},
            )
        )
        == 0
    )


def test_update_file_rejects_sibling_name_conflict():
    """Updating a file should reject sibling name conflicts."""
    connection = FakeConnection()
    knowledge_item_repository = FakeKnowledgeItemRepository()
    knowledge_item_repository.existing = {
        "kid": 11,
        "knowledge_base_id": 7,
        "fs_entry_id": 71,
        "item_code": "attendance-policy-pdf",
        "item_kind": "FILE",
        "description": "旧文件说明",
        "metadata": {"owner": "old"},
        "status": "ACTIVE",
        "type_code": "pdf",
        "is_deleted": False,
    }
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.entry_by_id[71] = {
        "kid": 71,
        "knowledge_base_id": 7,
        "parent_entry_id": 70,
        "name": "异常考勤处理办法.pdf",
        "entry_type": "FILE",
        "path_ltree": "kb_7.f1_doc",
        "is_root": False,
        "depth": 1,
    }
    knowledge_fs_entry_repository.child_entry_by_parent_and_name[
        (7, 70, "历史归档.pdf")
    ] = {
        "kid": 72,
        "knowledge_base_id": 7,
        "parent_entry_id": 70,
        "name": "历史归档.pdf",
        "entry_type": "FILE",
    }
    service = KnowledgeBaseService(
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
        knowledge_item_repository=knowledge_item_repository,
    )

    try:
        service.update_file(
            UpdateFileRequest(
                kb_code="hr-policy",
                file_code="attendance-policy-pdf",
                file_name="历史归档.pdf",
            )
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "file name already exists under parent: 历史归档.pdf"
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
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
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


def test_import_document_commits_promotes_and_updates_current_version():
    """A successful import should commit, promote the object, and update current version."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "id": 7,
            "kb_code": "hr-policy",
            "kb_name": "人力制度知识库",
            "status": "ACTIVE",
        }
    )
    knowledge_item_repository = FakeKnowledgeItemRepository()
    knowledge_item_version_repository = FakeKnowledgeItemVersionRepository()
    knowledge_item_chunk_repository = FakeKnowledgeItemChunkRepository()
    retrieval_projection_repository = FakeRetrievalProjectionRepository()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_item_repository=knowledge_item_repository,
        knowledge_item_version_repository=knowledge_item_version_repository,
        knowledge_item_chunk_repository=knowledge_item_chunk_repository,
        retrieval_projection_repository=retrieval_projection_repository,
        object_storage=storage,
        embedding_dimension=2,
    )

    response = service.import_document(
        markdown_bytes=b"# hello", manifest=build_manifest()
    )

    assert response.chunk_count == 2
    assert connection.committed is True
    assert knowledge_fs_entry_repository.calls == [
        ("ensure_root_entry", {"knowledge_base_id": 7, "kb_name": "人力制度知识库"}),
        (
            "ensure_file_entry",
            {
                "knowledge_base_id": 7,
                "root_entry_id": 70,
                "full_path": "dir1/item-1.md",
            },
        ),
    ]
    assert storage.promoted == [
        (
            "tmp/import-item-1-v1/content.md",
            "kb/7/item/10/version/v1/original",
            "knowledge-base",
        ),
        (
            "tmp/import-item-1-v1-markdown/content.md",
            "kb/7/item/10/version/v1/markdown",
            "knowledge-base-markdown",
        ),
    ]
    assert any(
        call[0] == "update_current_version" for call in knowledge_item_repository.calls
    )
    upsert_call = next(
        call for call in knowledge_item_repository.calls if call[0] == "upsert"
    )
    assert upsert_call[1]["item_code"] == "item-1"
    assert upsert_call[1]["item_kind"] == "FILE"
    assert upsert_call[1]["description"] is None
    version_upsert_call = next(
        call for call in knowledge_item_version_repository.calls if call[0] == "upsert"
    )
    assert version_upsert_call[1]["checksum"] == hashlib.sha256(b"# hello").hexdigest()
    assert (
        version_upsert_call[1]["markdown_object_key"]
        == "kb/7/item/10/version/v1/markdown"
    )
    assert version_upsert_call[1]["markdown_bucket_name"] == "knowledge-base-markdown"


def test_import_document_ignores_manifest_content_hash_and_generates_checksum():
    """Import should persist a checksum derived from the uploaded bytes."""
    connection = FakeConnection()
    knowledge_item_version_repository = FakeKnowledgeItemVersionRepository()
    manifest = KnowledgeItemImportManifest(
        kb_code="hr-policy",
        document={
            "item_code": "item-1",
            "full_path": "dir1/item-1.md",
            "title": "操作手册.pdf",
            "status": "ACTIVE",
            "source_code": "oa",
            "type_code": "policy_markdown",
            "version": "v1",
            "content_hash": "client-controlled-value",
        },
        chunks=[
            {
                "chunk_no": 1,
                "start_line": 1,
                "end_line": 10,
                "chunk_text": "hello",
                "embedding": [0.1, 0.2],
            },
            {
                "chunk_no": 2,
                "start_line": 11,
                "end_line": 20,
                "chunk_text": "world",
                "embedding": [0.3, 0.4],
            },
        ],
    )
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=knowledge_item_version_repository,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=2,
    )

    service.import_document(markdown_bytes=b"# hello", manifest=manifest)

    version_upsert_call = next(
        call for call in knowledge_item_version_repository.calls if call[0] == "upsert"
    )
    assert version_upsert_call[1]["checksum"] == hashlib.sha256(b"# hello").hexdigest()
    assert (
        version_upsert_call[1]["markdown_object_key"]
        == "kb/7/item/10/version/v1/markdown"
    )
    assert version_upsert_call[1]["markdown_bucket_name"] == "knowledge-base-markdown"


def test_import_document_rejects_embedding_dimension_mismatch():
    """Embedding arrays must match the configured dimension."""
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: FakeConnection(),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=3,
    )

    try:
        service.import_document(markdown_bytes=b"# hello", manifest=build_manifest())
    except KnowledgeBaseValidationError as exc:
        assert "embedding dimension" in str(exc).lower()
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")


def test_write_file_commits_promotes_and_updates_current_version():
    """Write-file should persist metadata, promote object, and update current version."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "id": 7,
            "kb_code": "hr-policy",
            "kb_name": "人力制度知识库",
            "status": "ACTIVE",
        }
    )
    knowledge_item_repository = FakeKnowledgeItemRepository()
    knowledge_item_version_repository = FakeKnowledgeItemVersionRepository()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_item_repository=knowledge_item_repository,
        knowledge_item_version_repository=knowledge_item_version_repository,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=storage,
        embedding_dimension=2,
    )

    response = service.write_file(
        WriteFileRequest(
            kb_code="hr-policy",
            file_code="file-001",
            file_path="/dir1/item-1.pdf",
            file_description="操作手册",
            file_content="ZmFrZS1iYXNlNjQ=",
            version="v1",
            source_code="oa",
            status="ACTIVE",
            metadata={"owner": "HR"},
        )
    )

    assert response.type_code == "pdf"
    assert connection.committed is True
    assert storage.uploaded == [
        (
            "write-file-001-v1",
            b"fake-base64",
            "application/octet-stream",
            "knowledge-base",
        )
    ]
    assert storage.promoted == [
        (
            "tmp/write-file-001-v1/content.md",
            "kb/7/item/10/version/v1/original",
            "knowledge-base",
        )
    ]
    upsert_call = next(
        call for call in knowledge_item_repository.calls if call[0] == "upsert"
    )
    assert upsert_call[1]["item_code"] == "file-001"
    assert upsert_call[1]["item_kind"] == "FILE"
    assert upsert_call[1]["description"] == "操作手册"
    assert upsert_call[1]["type_code"] == "pdf"
    assert upsert_call[1]["metadata"] == {
        "owner": "HR",
        "file_description": "操作手册",
    }
    version_upsert_call = next(
        call for call in knowledge_item_version_repository.calls if call[0] == "upsert"
    )
    assert version_upsert_call[1]["markdown_object_key"] is None


def test_write_file_rejects_invalid_base64():
    """Binary write-file requests should reject invalid base64 payloads."""
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: FakeConnection(),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=2,
    )

    try:
        service.write_file(
            WriteFileRequest(
                kb_code="hr-policy",
                file_code="file-001",
                file_path="/dir1/item-1.pdf",
                file_description=None,
                file_content="not-base64",
                version="v1",
                source_code="oa",
                status="ACTIVE",
                metadata=None,
            )
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "file_content must be valid base64"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")


def test_write_file_allows_same_file_code_on_existing_path():
    """Write-file should reuse an existing fs entry when the file_code matches."""
    connection = FakeConnection()
    knowledge_item_repository = FakeKnowledgeItemRepository()
    knowledge_item_repository.existing = {
        "kid": 10,
        "knowledge_base_id": 7,
        "fs_entry_id": 71,
        "item_code": "file-001",
        "current_version_id": 21,
        "status": "ACTIVE",
    }
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_item_repository=knowledge_item_repository,
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=2,
    )

    response = service.write_file(
        WriteFileRequest(
            kb_code="hr-policy",
            file_code="file-001",
            file_path="/dir1/item-1.pdf",
            file_description=None,
            file_content="ZmFrZS1iYXNlNjQ=",
            version="v2",
            source_code="oa",
            status="ACTIVE",
            metadata=None,
        )
    )

    assert response.file_code == "file-001"
    assert connection.committed is True


def test_write_file_rejects_soft_deleted_file_code():
    """Write-file should reject reusing a file_code still occupied by a soft-deleted item."""
    connection = FakeConnection()
    knowledge_item_repository = FakeKnowledgeItemRepository()
    knowledge_item_repository.existing = {
        "kid": 10,
        "knowledge_base_id": 7,
        "fs_entry_id": 71,
        "item_code": "file-001",
        "status": "INACTIVE",
        "is_deleted": True,
    }
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_item_repository=knowledge_item_repository,
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=2,
    )

    try:
        service.write_file(
            WriteFileRequest(
                kb_code="hr-policy",
                file_code="file-001",
                file_path="/dir1/new-item.pdf",
                file_description=None,
                file_content="ZmFrZS1iYXNlNjQ=",
                version="v1",
                source_code="oa",
                status="ACTIVE",
                metadata=None,
            )
        )
    except KnowledgeBaseValidationError as exc:
        assert (
            str(exc)
            == "file_code is occupied by a soft-deleted knowledge item: file-001"
        )
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert connection.rolled_back is True


def test_write_file_rejects_missing_parent_directory():
    """Write-file should fail instead of auto-creating missing directories."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.raise_missing_parent_directory = True
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=2,
    )

    try:
        service.write_file(
            WriteFileRequest(
                kb_code="hr-policy",
                file_code="file-001",
                file_path="/missing-dir/item-1.pdf",
                file_description=None,
                file_content="ZmFrZS1iYXNlNjQ=",
                version="v1",
                source_code="oa",
                status="ACTIVE",
                metadata=None,
            )
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "parent directory not found: missing-dir"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert connection.rolled_back is True


def test_write_index_replaces_chunks_embeddings_and_refreshes_projection():
    """Write-index should replace version chunks and refresh retrieval projection."""
    connection = FakeConnection()
    knowledge_item_repository = FakeKnowledgeItemRepository()
    knowledge_item_repository.existing = {
        "kid": 10,
        "knowledge_base_id": 7,
        "fs_entry_id": 71,
        "item_code": "file-001",
        "type_code": "md",
        "current_version_id": 21,
        "status": "ACTIVE",
    }
    knowledge_item_version_repository = FakeKnowledgeItemVersionRepository()
    knowledge_item_version_repository.existing = {
        "kid": 22,
        "bucket_name": "knowledge-base",
        "object_key": "kb/7/item/10/version/v1/original",
        "markdown_bucket_name": "knowledge-base-markdown",
        "markdown_object_key": "kb/7/item/10/version/v1/markdown",
        "markdown_file_size": 128,
        "markdown_checksum": "abc123",
        "file_size": 128,
        "checksum": "abc123",
    }
    knowledge_item_chunk_repository = FakeKnowledgeItemChunkRepository()
    retrieval_projection_repository = FakeRetrievalProjectionRepository()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_item_repository=knowledge_item_repository,
        knowledge_item_version_repository=knowledge_item_version_repository,
        knowledge_item_chunk_repository=knowledge_item_chunk_repository,
        retrieval_projection_repository=retrieval_projection_repository,
        object_storage=FakeObjectStorage(),
        embedding_dimension=2,
    )

    response = service.write_index(
        WriteIndexRequest(
            kb_code="hr-policy",
            file_code="file-001",
            version="v1",
            markdown_content="# hello",
            chunks=[
                {
                    "chunk_no": 1,
                    "start_line": 1,
                    "end_line": 10,
                    "chunk_text": "hello",
                    "embedding": [0.1, 0.2],
                }
            ],
        )
    )

    assert response.chunks.count == 1
    assert connection.committed is True
    assert service.object_storage.uploaded == [
        (
            "index-file-001-v1",
            b"# hello",
            "text/markdown; charset=utf-8",
            "knowledge-base-markdown",
        )
    ]
    assert service.object_storage.promoted == [
        (
            "tmp/index-file-001-v1/content.md",
            "kb/7/item/10/version/v1/markdown",
            "knowledge-base-markdown",
        )
    ]
    assert knowledge_item_chunk_repository.calls[0][0] == "replace_for_version"
    assert knowledge_item_chunk_repository.calls[1][0] == "replace_embeddings"
    assert retrieval_projection_repository.calls == [
        ("refresh_for_item", {"knowledge_item_id": 10})
    ]
    version_upsert_call = next(
        call for call in knowledge_item_version_repository.calls if call[0] == "upsert"
    )
    assert (
        version_upsert_call[1]["markdown_object_key"]
        == "kb/7/item/10/version/v1/markdown"
    )


def test_write_index_allows_non_markdown_file_types():
    """Write-index should allow indexing any stored file type."""
    connection = FakeConnection()
    knowledge_item_repository = FakeKnowledgeItemRepository()
    knowledge_item_repository.existing = {
        "kid": 10,
        "knowledge_base_id": 7,
        "fs_entry_id": 71,
        "item_code": "file-001",
        "type_code": "pdf",
        "current_version_id": 21,
        "status": "ACTIVE",
    }
    knowledge_item_version_repository = FakeKnowledgeItemVersionRepository()
    knowledge_item_version_repository.existing = {
        "kid": 22,
        "bucket_name": "knowledge-base",
        "object_key": "kb/7/item/10/version/v1/original",
        "markdown_bucket_name": "knowledge-base-markdown",
        "markdown_object_key": "kb/7/item/10/version/v1/markdown",
        "markdown_file_size": 128,
        "markdown_checksum": "abc123",
        "file_size": 128,
        "checksum": "abc123",
    }
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_item_repository=knowledge_item_repository,
        knowledge_item_version_repository=knowledge_item_version_repository,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=2,
    )

    response = service.write_index(
        WriteIndexRequest(
            kb_code="hr-policy",
            file_code="file-001",
            version="v1",
            markdown_content="# hello",
            chunks=[
                {
                    "chunk_no": 1,
                    "start_line": 1,
                    "end_line": 10,
                    "chunk_text": "hello",
                    "embedding": [0.1, 0.2],
                }
            ],
        )
    )

    assert response.chunks.count == 1
    assert connection.committed is True


def test_import_document_emits_internal_key_node_logs(monkeypatch):
    """Document import should log validation, storage, persistence, and commit steps."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(
        default_lookup_result={
            "id": 7,
            "kb_code": "hr-policy",
            "kb_name": "人力制度知识库",
            "status": "ACTIVE",
        }
    )
    knowledge_item_repository = FakeKnowledgeItemRepository()
    knowledge_item_version_repository = FakeKnowledgeItemVersionRepository()
    knowledge_item_chunk_repository = FakeKnowledgeItemChunkRepository()
    retrieval_projection_repository = FakeRetrievalProjectionRepository()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_item_repository=knowledge_item_repository,
        knowledge_item_version_repository=knowledge_item_version_repository,
        knowledge_item_chunk_repository=knowledge_item_chunk_repository,
        retrieval_projection_repository=retrieval_projection_repository,
        object_storage=storage,
        embedding_dimension=2,
    )
    info_messages: list[str] = []

    monkeypatch.setattr(
        logger,
        "info",
        lambda message, *args, **kwargs: info_messages.append(
            message % args if args else message
        ),
    )

    response = service.import_document(
        markdown_bytes=b"# hello", manifest=build_manifest()
    )

    assert response.chunk_count == 2
    assert info_messages == [
        "knowledge_item_ingestion_service.import_document started: kb_code=hr-policy, item_code=item-1, version=v1, chunk_count=2, content_bytes=7",
        "knowledge_item_ingestion_service embedding validation finished: item_code=item-1, expected_dimension=2, chunk_count=2",
        "knowledge_item_ingestion_service temp object upload finished: item_code=item-1, import_request_id=import-item-1-v1, temp_object_key=tmp/import-item-1-v1/content.md",
        "knowledge_item_ingestion_service knowledge base validation finished: kb_code=hr-policy, knowledge_base_id=7, status=ACTIVE",
        "knowledge_item_ingestion_service duplicate check finished: item_code=item-1, existing_item=False, existing_version=False",
        "knowledge_item_ingestion_service persistence finished: item_code=item-1, knowledge_item_id=10, version_id=22, chunk_count=2, final_object_key=kb/7/item/10/version/v1/original",
        "knowledge_item_ingestion_service.import_document finished: item_code=item-1, version=v1, chunk_count=2, final_object_key=kb/7/item/10/version/v1/original",
    ]


def test_import_document_rolls_back_and_deletes_temp_object_on_failure():
    """If a database step fails, the transaction should rollback and temp object be deleted."""

    class BrokenKnowledgeItemChunkRepository(FakeKnowledgeItemChunkRepository):
        def replace_for_version(self, cursor, **kwargs):
            raise RuntimeError("chunk persistence failed")

    connection = FakeConnection()
    storage = FakeObjectStorage()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
        knowledge_item_chunk_repository=BrokenKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=storage,
        embedding_dimension=2,
    )

    try:
        service.import_document(markdown_bytes=b"# hello", manifest=build_manifest())
    except RuntimeError as exc:
        assert "chunk persistence failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert connection.rolled_back is True
    assert storage.deleted == [
        ("tmp/import-item-1-v1/content.md", "knowledge-base"),
        ("tmp/import-item-1-v1-markdown/content.md", "knowledge-base-markdown"),
    ]


def test_import_document_rejects_duplicate_item_code_and_version():
    """Import should fail when the same item_code/version already exists."""
    connection = FakeConnection()
    knowledge_item_repository = FakeKnowledgeItemRepository()
    knowledge_item_repository.existing = {"id": 10}
    knowledge_item_version_repository = FakeKnowledgeItemVersionRepository()
    knowledge_item_version_repository.existing = {"id": 22, "version": "v1"}
    storage = FakeObjectStorage()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_item_repository=knowledge_item_repository,
        knowledge_item_version_repository=knowledge_item_version_repository,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=storage,
        embedding_dimension=2,
    )

    try:
        service.import_document(markdown_bytes=b"# hello", manifest=build_manifest())
    except KnowledgeBaseValidationError as exc:
        assert "item_code/version already exists" in str(exc)
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")


def test_import_knowledge_item_commits_original_markdown_and_chunks():
    """Combined import should atomically persist original file, markdown sidecar, and chunks."""
    connection = FakeConnection()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=2,
    )

    response = service.import_knowledge_item(
        KnowledgeItemImportRequest(
            kb_code="hr-policy",
            file_code="file-001",
            file_path="/dir1/item-1.pdf",
            file_description="操作手册",
            file_content="ZmFrZS1iYXNlNjQ=",
            version="v1",
            source_code="oa",
            status="ACTIVE",
            metadata={"owner": "HR"},
            markdown_content="# hello",
            chunks=[
                {
                    "chunk_no": 1,
                    "start_line": 1,
                    "end_line": 10,
                    "chunk_text": "hello",
                    "embedding": [0.1, 0.2],
                }
            ],
        )
    )

    assert response.model_dump() == {
        "kb_code": "hr-policy",
        "file_code": "file-001",
        "type_code": "pdf",
        "file_path": "/dir1/item-1.pdf",
        "file_description": "操作手册",
        "version": "v1",
        "status": "ACTIVE",
        "metadata": {"owner": "HR"},
        "chunks": {"count": 1},
    }
    assert connection.committed is True


def test_import_knowledge_item_rolls_back_and_deletes_temp_objects_on_failure():
    """Combined import should clean both temp objects when chunk persistence fails."""

    class BrokenChunkRepository(FakeKnowledgeItemChunkRepository):
        def replace_for_version(self, cursor, **kwargs):
            raise RuntimeError("chunk persistence failed")

    connection = FakeConnection()
    storage = FakeObjectStorage()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
        knowledge_item_chunk_repository=BrokenChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=storage,
        embedding_dimension=2,
    )

    try:
        service.import_knowledge_item(
            KnowledgeItemImportRequest(
                kb_code="hr-policy",
                file_code="file-001",
                file_path="/dir1/item-1.pdf",
                file_content="ZmFrZS1iYXNlNjQ=",
                version="v1",
                source_code="oa",
                status="ACTIVE",
                markdown_content="# hello",
                chunks=[
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 10,
                        "chunk_text": "hello",
                        "embedding": [0.1, 0.2],
                    }
                ],
            )
        )
    except RuntimeError as exc:
        assert "chunk persistence failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert connection.rolled_back is True
    assert storage.deleted == [
        ("tmp/import-file-001-v1-original/content.md", "knowledge-base"),
        ("tmp/import-file-001-v1-markdown/content.md", "knowledge-base-markdown"),
    ]


def test_import_knowledge_item_rejects_missing_parent_directory():
    """Combined import should fail instead of auto-creating missing directories."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.raise_missing_parent_directory = True
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=2,
    )

    try:
        service.import_knowledge_item(
            KnowledgeItemImportRequest(
                kb_code="hr-policy",
                file_code="file-001",
                file_path="/missing-dir/item-1.pdf",
                file_content="ZmFrZS1iYXNlNjQ=",
                version="v1",
                source_code="oa",
                status="ACTIVE",
                markdown_content="# hello",
                chunks=[
                    {
                        "chunk_no": 1,
                        "start_line": 1,
                        "end_line": 10,
                        "chunk_text": "hello",
                        "embedding": [0.1, 0.2],
                    }
                ],
            )
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "parent directory not found: missing-dir"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert connection.rolled_back is True


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
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
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
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
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


def test_import_document_rejects_missing_parent_directory():
    """Document import should fail instead of auto-creating missing directories."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.raise_missing_parent_directory = True
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={
                "id": 7,
                "kb_code": "hr-policy",
                "kb_name": "人力制度知识库",
                "status": "ACTIVE",
            }
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_item_repository=FakeKnowledgeItemRepository(),
        knowledge_item_version_repository=FakeKnowledgeItemVersionRepository(),
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        object_storage=FakeObjectStorage(),
        embedding_dimension=2,
    )

    manifest = KnowledgeItemImportManifest(
        kb_code="hr-policy",
        document={
            "item_code": "item-1",
            "full_path": "missing-dir/item-1.md",
            "status": "ACTIVE",
            "source_code": "oa",
            "type_code": "policy_markdown",
            "version": "v1",
        },
        chunks=[
            {
                "chunk_no": 1,
                "start_line": 1,
                "end_line": 10,
                "chunk_text": "hello",
                "embedding": [0.1, 0.2],
            }
        ],
    )

    try:
        service.import_document(markdown_bytes=b"# hello", manifest=manifest)
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "parent directory not found: missing-dir"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert connection.rolled_back is True

    assert connection.rolled_back is True


def test_list_dir_root_returns_virtual_knowledge_base_directories():
    """Root listing should return root knowledge-base directories."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.list_dir(
        KnowledgeItemListDirRequest(kb_codes=["hr-policy"], path="/")
    )

    assert (
        response.model_dump()["items"]
        == knowledge_fs_entry_repository.root_entries_by_kb_code["hr-policy"]
    )
    assert knowledge_fs_entry_repository.calls == [
        ("list_root_entries", {"kb_codes": ["hr-policy"]})
    ]


def test_list_dir_directory_path_returns_direct_children_only():
    """Directory path should resolve the directory and list its direct children."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.list_dir(
        KnowledgeItemListDirRequest(kb_codes=["hr-policy"], path="人力制度知识库/dir1/")
    )

    assert (
        response.model_dump()["items"]
        == knowledge_fs_entry_repository.directory_children
    )
    assert knowledge_fs_entry_repository.calls == [
        ("list_root_nodes", {"kb_codes": ["hr-policy"]}),
        ("list_child_nodes", {"parent_path_ltree": "kb_7"}),
        ("list_child_nodes", {"parent_path_ltree": "kb_7.d1_a"}),
    ]


def test_list_dir_literal_missing_path_raises_not_found():
    """List-dir should raise not-found when the requested literal path does not exist."""
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
            KnowledgeItemListDirRequest(kb_codes=["hr-policy"], path="*.md")
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "directory not found: *.md"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert knowledge_fs_entry_repository.calls == [
        ("list_root_nodes", {"kb_codes": ["hr-policy"]})
    ]


def test_glob_pattern_with_literal_prefix_uses_ancestor_path_ltree():
    """Literal-prefix pattern should resolve the prefix directory and constrain traversal."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.glob(
        KnowledgeItemGlobRequest(
            kb_codes=["hr-policy"], path="人力制度知识库/dir1/*.md"
        )
    )

    assert response.model_dump()["items"] == [
        {
            "kb_code": "hr-policy",
            "name": "/人力制度知识库/dir1/doc.md",
            "type": "file",
            "size": 128,
        }
    ]
    assert knowledge_fs_entry_repository.calls == [
        ("list_root_nodes", {"kb_codes": ["hr-policy"]}),
        ("list_child_nodes", {"parent_path_ltree": "kb_7"}),
        ("list_child_nodes", {"parent_path_ltree": "kb_7.d1_a"}),
    ]


def test_glob_pattern_with_root_glob_limits_search_to_matching_roots():
    """Multi-segment glob should match roots first, then only search matched root subtrees."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.glob(
        KnowledgeItemGlobRequest(kb_codes=["hr-policy"], path="人力*/*.md")
    )

    assert response.model_dump()["items"] == [
        {
            "kb_code": "hr-policy",
            "name": "/人力制度知识库/doc.md",
            "type": "file",
            "size": 128,
        }
    ]
    assert knowledge_fs_entry_repository.calls == [
        ("list_root_nodes", {"kb_codes": ["hr-policy"]}),
        ("list_child_nodes", {"parent_path_ltree": "kb_7"}),
    ]


def test_glob_directory_patterns_keep_root_prefix_and_treat_trailing_slash_as_list_contents():
    """Directory-like patterns should list matched directory contents and keep the root prefix."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.root_entries = [
        {"kb_code": "demo-kb", "name": "/DEMO知识库", "type": "directory", "size": 0}
    ]
    knowledge_fs_entry_repository.root_entries_by_kb_code["demo-kb"] = [
        {"kb_code": "demo-kb", "name": "/DEMO知识库", "type": "directory", "size": 0}
    ]
    knowledge_fs_entry_repository.root_nodes = [
        {
            "kid": 9,
            "kb_code": "demo-kb",
            "name": "DEMO知识库",
            "full_path": "DEMO知识库",
            "type": "directory",
            "size": 0,
            "path_ltree": "kb_9",
        }
    ]
    knowledge_fs_entry_repository.root_nodes_by_kb_code["demo-kb"] = list(
        knowledge_fs_entry_repository.root_nodes
    )
    knowledge_fs_entry_repository.child_nodes_by_parent = {
        "kb_9": [
            {
                "kid": 91,
                "kb_code": "demo-kb",
                "name": "教程",
                "full_path": "教程",
                "type": "directory",
                "size": 0,
                "path_ltree": "kb_9.d1_tutorial",
            }
        ],
        "kb_9.d1_tutorial": [
            {
                "kid": 92,
                "kb_code": "demo-kb",
                "name": "单跳.pdf",
                "full_path": "单跳.pdf",
                "type": "file",
                "size": 100,
                "path_ltree": "kb_9.d1_tutorial.f2_pdf",
            }
        ],
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    level_one_with_slash = service.glob(
        KnowledgeItemGlobRequest(kb_codes=["demo-kb"], path="DEMO知识*/")
    )
    level_one_without_slash = service.glob(
        KnowledgeItemGlobRequest(kb_codes=["demo-kb"], path="DEMO知识*")
    )
    level_two_with_slash = service.glob(
        KnowledgeItemGlobRequest(kb_codes=["demo-kb"], path="DEMO知识*/*/")
    )
    level_two_without_slash = service.glob(
        KnowledgeItemGlobRequest(kb_codes=["demo-kb"], path="DEMO知识*/*")
    )
    level_three = service.glob(
        KnowledgeItemGlobRequest(kb_codes=["demo-kb"], path="DEMO知识*/*/*")
    )

    assert level_one_with_slash.model_dump()["items"] == [
        {
            "kb_code": "demo-kb",
            "name": "/DEMO知识库/教程",
            "type": "directory",
            "size": 0,
        }
    ]
    assert level_one_without_slash.model_dump()["items"] == [
        {"kb_code": "demo-kb", "name": "/DEMO知识库", "type": "directory", "size": 0}
    ]
    assert level_two_with_slash.model_dump()["items"] == [
        {
            "kb_code": "demo-kb",
            "name": "/DEMO知识库/教程/单跳.pdf",
            "type": "file",
            "size": 100,
        }
    ]
    assert level_two_without_slash.model_dump()["items"] == [
        {
            "kb_code": "demo-kb",
            "name": "/DEMO知识库/教程",
            "type": "directory",
            "size": 0,
        }
    ]
    assert level_three.model_dump()["items"] == [
        {
            "kb_code": "demo-kb",
            "name": "/DEMO知识库/教程/单跳.pdf",
            "type": "file",
            "size": 100,
        }
    ]


def test_list_dir_root_filters_visible_knowledge_bases_by_kb_codes():
    """Root listing should only expose authorized knowledge bases."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.list_dir(
        KnowledgeItemListDirRequest(kb_codes=["hr-policy"], path="/")
    )

    assert (
        response.model_dump()["items"]
        == knowledge_fs_entry_repository.root_entries_by_kb_code["hr-policy"]
    )
    assert knowledge_fs_entry_repository.calls == [
        ("list_root_entries", {"kb_codes": ["hr-policy"]})
    ]


def test_list_dir_returns_empty_when_kb_codes_is_empty():
    """Empty kb_codes means the caller has no visible knowledge bases."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = service.list_dir(KnowledgeItemListDirRequest(kb_codes=[], path="/"))

    assert response.model_dump()["items"] == []
    assert knowledge_fs_entry_repository.calls == []


def test_fetch_downloads_current_version_and_caches_file(tmp_path):
    """Fetch should download on cache miss, then serve the requested line window."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    cache_repository = FakeKnowledgeFetchCacheRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        object_storage=storage,
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )

    response = service.fetch(
        KnowledgeItemFetchRequest(
            kb_codes=["hr-policy"],
            path="人力制度知识库/dir1/doc.md",
            content_type="markdown",
            start_line=2,
            end_line=3,
        )
    )

    assert response.data == "line2\nline3\n"
    assert response.reached_eof is True
    assert response.kb_code == "hr-policy"
    assert storage.downloaded == [
        ("kb/7/item/10/version/v1/markdown", "knowledge-base-markdown")
    ]
    assert (tmp_path / "人力制度知识库" / "dir1" / "doc.md").read_text(
        encoding="utf-8"
    ) == "line1\nline2\nline3\n"
    assert knowledge_fs_entry_repository.calls == [
        ("list_root_nodes", {"kb_codes": ["hr-policy"]}),
        ("list_child_nodes", {"parent_path_ltree": "kb_7"}),
        ("list_child_nodes", {"parent_path_ltree": "kb_7.d1_a"}),
        ("get_current_file_version_by_entry_id", {"fs_entry_id": 71}),
    ]
    assert cache_repository.calls[0] == (
        "get_by_version_id",
        {"knowledge_item_version_id": 22},
    )
    assert cache_repository.calls[1][0] == "upsert_cache_entry"


def test_fetch_returns_access_url_for_binary_files(tmp_path):
    """Binary files should return a MinIO access URL instead of downloading content."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    cache_repository = FakeKnowledgeFetchCacheRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        object_storage=storage,
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )
    knowledge_fs_entry_repository.get_current_file_version_by_entry_id = (
        lambda cursor, *, fs_entry_id: {
            "knowledge_base_id": 7,
            "knowledge_item_id": 10,
            "knowledge_item_version_id": 22,
            "kb_code": "hr-policy",
            "full_path": "dir1/doc.pdf",
            "version": "v1",
            "bucket_name": "knowledge-base",
            "object_key": "kb/7/item/10/version/v1/original",
            "markdown_bucket_name": "knowledge-base-markdown",
            "markdown_object_key": "kb/7/item/10/version/v1/markdown",
            "checksum": "abc123",
        }
    )

    response = service.fetch(
        KnowledgeItemFetchRequest(
            kb_codes=["hr-policy"],
            path="人力制度知识库/dir1/doc.md",
            content_type="original",
        )
    )

    assert response.model_dump(exclude_none=True) == {
        "kb_code": "hr-policy",
        "path": "/人力制度知识库/dir1/doc.md",
        "content_type": "original",
        "url": "https://minio.example/knowledge-base/kb/7/item/10/version/v1/original?ttl=3600",
    }
    assert storage.downloaded == []
    assert cache_repository.calls == []


def test_download_file_returns_original_bytes(tmp_path):
    """Download-file should fetch the current original object bytes."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    storage.object_payloads[("knowledge-base", "kb/7/item/10/version/v1/original")] = (
        b"%PDF-1.4 binary payload"
    )
    cache_repository = FakeKnowledgeFetchCacheRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        object_storage=storage,
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )
    knowledge_fs_entry_repository.get_current_file_version_by_entry_id = (
        lambda cursor, *, fs_entry_id: {
            "knowledge_base_id": 7,
            "knowledge_item_id": 10,
            "knowledge_item_version_id": 22,
            "kb_code": "hr-policy",
            "full_path": "dir1/doc.pdf",
            "version": "v1",
            "bucket_name": "knowledge-base",
            "object_key": "kb/7/item/10/version/v1/original",
            "markdown_bucket_name": "knowledge-base-markdown",
            "markdown_object_key": "kb/7/item/10/version/v1/markdown",
            "checksum": "abc123",
        }
    )
    knowledge_fs_entry_repository.child_nodes_by_parent["kb_7.d1_a"] = [
        {
            "kid": 71,
            "kb_code": "hr-policy",
            "name": "doc.pdf",
            "full_path": "doc.pdf",
            "type": "file",
            "size": 128,
            "path_ltree": "kb_7.d1_a.f2_doc",
        }
    ]

    response = service.download_file(
        KnowledgeItemDownloadRequest(
            kb_codes=["hr-policy"],
            path="人力制度知识库/dir1/doc.pdf",
        )
    )

    assert response == {
        "filename": "doc.pdf",
        "media_type": "application/pdf",
        "content": b"%PDF-1.4 binary payload",
    }
    assert storage.downloaded == [
        ("kb/7/item/10/version/v1/original", "knowledge-base")
    ]
    assert cache_repository.calls == []


def test_fetch_returns_full_markdown_when_line_window_is_omitted(tmp_path):
    """Markdown reads without a line range should return the whole sidecar file."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    cache_repository = FakeKnowledgeFetchCacheRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        object_storage=storage,
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )

    response = service.fetch(
        KnowledgeItemFetchRequest(
            kb_codes=["hr-policy"],
            path="人力制度知识库/dir1/doc.md",
            content_type="markdown",
        )
    )

    assert response.model_dump(exclude_none=True) == {
        "kb_code": "hr-policy",
        "path": "/人力制度知识库/dir1/doc.md",
        "content_type": "markdown",
        "data": "line1\nline2\nline3\n",
        "reached_eof": True,
    }


def test_fetch_markdown_request_falls_back_to_original_url_when_sidecar_missing(
    tmp_path,
):
    """Markdown reads should degrade to the original file URL when no markdown sidecar exists."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    cache_repository = FakeKnowledgeFetchCacheRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        object_storage=storage,
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )
    knowledge_fs_entry_repository.get_current_file_version_by_entry_id = (
        lambda cursor, *, fs_entry_id: {
            "knowledge_base_id": 7,
            "knowledge_item_id": 10,
            "knowledge_item_version_id": 22,
            "kb_code": "hr-policy",
            "full_path": "dir1/doc.pdf",
            "version": "v1",
            "bucket_name": "knowledge-base",
            "object_key": "kb/7/item/10/version/v1/original",
            "markdown_bucket_name": None,
            "markdown_object_key": None,
            "checksum": "abc123",
            "file_size": 128,
        }
    )

    response = service.fetch(
        KnowledgeItemFetchRequest(
            kb_codes=["hr-policy"],
            path="人力制度知识库/dir1/doc.md",
            content_type="markdown",
        )
    )

    assert response.model_dump(exclude_none=True) == {
        "kb_code": "hr-policy",
        "path": "/人力制度知识库/dir1/doc.md",
        "content_type": "original",
        "url": "https://minio.example/knowledge-base/kb/7/item/10/version/v1/original?ttl=3600",
    }


def test_fetch_uses_fresh_matching_cache_without_redownloading(tmp_path):
    """Fresh cache entries with matching checksum should bypass object storage."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    cache_repository = FakeKnowledgeFetchCacheRepository()
    cache_file = tmp_path / "人力制度知识库" / "dir1" / "doc.md"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("line1\nline2\nline3\n", encoding="utf-8")
    cache_repository.entries_by_version_id[22] = {
        "kid": 301,
        "knowledge_item_version_id": 22,
        "checksum": "abc123",
        "cache_file_path": str(cache_file),
        "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "cache_status": "READY",
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        object_storage=storage,
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )

    response = service.fetch(
        KnowledgeItemFetchRequest(
            kb_codes=["hr-policy"],
            path="人力制度知识库/dir1/doc.md",
            content_type="markdown",
            start_line=1,
            end_line=1,
        )
    )

    assert response.data == "line1\n"
    assert response.reached_eof is False
    assert storage.downloaded == []
    assert cache_repository.calls[-1] == (
        "touch_cache_entry",
        {"cache_entry_id": 301, "cache_ttl_seconds": 24 * 60 * 60},
    )


def test_fetch_cache_hit_does_not_read_whole_file_with_read_text(monkeypatch, tmp_path):
    """Cache hits should read only the requested line window instead of read_text()."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    cache_repository = FakeKnowledgeFetchCacheRepository()
    cache_file = tmp_path / "人力制度知识库" / "dir1" / "doc.md"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("line1\nline2\nline3\n", encoding="utf-8")
    cache_repository.entries_by_version_id[22] = {
        "kid": 301,
        "knowledge_item_version_id": 22,
        "checksum": "abc123",
        "cache_file_path": str(cache_file),
        "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "cache_status": "READY",
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        object_storage=storage,
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("read_text should not be used for fetch window reads")
        ),
    )

    response = service.fetch(
        KnowledgeItemFetchRequest(
            kb_codes=["hr-policy"],
            path="人力制度知识库/dir1/doc.md",
            content_type="markdown",
            start_line=2,
            end_line=2,
        )
    )

    assert response.data == "line2\n"


def test_fetch_redownloads_when_cache_is_expired(tmp_path):
    """Expired cache entries should be refreshed from object storage."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    cache_repository = FakeKnowledgeFetchCacheRepository()
    cache_file = tmp_path / "人力制度知识库" / "dir1" / "doc.md"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("stale\n", encoding="utf-8")
    cache_repository.entries_by_version_id[22] = {
        "kid": 301,
        "knowledge_item_version_id": 22,
        "checksum": "abc123",
        "cache_file_path": str(cache_file),
        "expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
        "cache_status": "READY",
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        object_storage=storage,
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )

    response = service.fetch(
        KnowledgeItemFetchRequest(
            kb_codes=["hr-policy"],
            path="人力制度知识库/dir1/doc.md",
            content_type="markdown",
            start_line=1,
            end_line=2,
        )
    )

    assert response.data == "line1\nline2\n"
    assert response.reached_eof is False
    assert storage.downloaded == [
        ("kb/7/item/10/version/v1/markdown", "knowledge-base-markdown")
    ]


def test_fetch_removes_expired_cache_files_before_redownloading(tmp_path):
    """Expired cache files should be cleaned up before the fresh download is written."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    cache_repository = FakeKnowledgeFetchCacheRepository()
    cache_file = tmp_path / "人力制度知识库" / "dir1" / "doc.md"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("stale\n", encoding="utf-8")
    cache_repository.entries_by_version_id[22] = {
        "kid": 301,
        "knowledge_item_version_id": 22,
        "checksum": "abc123",
        "cache_file_path": str(cache_file),
        "expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
        "cache_status": "READY",
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        object_storage=storage,
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )

    service.fetch(
        KnowledgeItemFetchRequest(
            kb_codes=["hr-policy"],
            path="人力制度知识库/dir1/doc.md",
            content_type="markdown",
            start_line=1,
            end_line=3,
        )
    )

    assert cache_file.read_text(encoding="utf-8") == "line1\nline2\nline3\n"


def test_fetch_rejects_invalid_line_window(tmp_path):
    """Fetch should reject invalid line ranges before hitting object storage."""
    connection = FakeConnection()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_fetch_cache_repository=FakeKnowledgeFetchCacheRepository(),
        object_storage=FakeObjectStorage(),
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )

    try:
        service.fetch(
            KnowledgeItemFetchRequest(
                kb_codes=["hr-policy"],
                path="人力制度知识库/dir1/doc.md",
                content_type="markdown",
                start_line=0,
                end_line=1,
            )
        )
    except KnowledgeBaseValidationError as exc:
        assert "start_line" in str(exc)
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")


def test_fetch_logs_cache_hit_and_minio_refresh(monkeypatch, tmp_path):
    """Fetch should log whether content came from local cache or MinIO refresh."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeObjectStorage()
    cache_repository = FakeKnowledgeFetchCacheRepository()
    info_messages: list[str] = []
    cache_file = tmp_path / "人力制度知识库" / "dir1" / "doc.md"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        logger,
        "info",
        lambda message, *args, **kwargs: info_messages.append(
            message % args if args else message
        ),
    )

    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        object_storage=storage,
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )

    service.fetch(
        KnowledgeItemFetchRequest(
            kb_codes=["hr-policy"],
            path="人力制度知识库/dir1/doc.md",
            content_type="markdown",
            start_line=1,
            end_line=3,
        )
    )

    cache_repository.entries_by_version_id[22] = {
        "kid": 301,
        "knowledge_item_version_id": 22,
        "checksum": "abc123",
        "cache_file_path": str(cache_file),
        "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "cache_status": "READY",
    }

    service.fetch(
        KnowledgeItemFetchRequest(
            kb_codes=["hr-policy"],
            path="人力制度知识库/dir1/doc.md",
            start_line=1,
            end_line=1,
        )
    )

    assert any("source=minio" in message for message in info_messages)
    assert any("source=cache" in message for message in info_messages)


def test_fetch_rejects_paths_outside_allowed_kb_codes(tmp_path):
    """Fetch should not resolve files from knowledge bases outside request.kb_codes."""
    connection = FakeConnection()
    service = KnowledgeBaseService(
        connection_factory=lambda: connection,
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
        knowledge_fetch_cache_repository=FakeKnowledgeFetchCacheRepository(),
        object_storage=FakeObjectStorage(),
        cache_root=tmp_path,
        cache_ttl_seconds=24 * 60 * 60,
    )

    try:
        service.fetch(
            KnowledgeItemFetchRequest(
                kb_codes=["legal-policy"],
                path="人力制度知识库/dir1/doc.md",
                start_line=1,
                end_line=1,
            )
        )
    except KnowledgeBaseValidationError as exc:
        assert "file not found" in str(exc)
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")
