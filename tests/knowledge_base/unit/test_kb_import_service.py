"""Tests for knowledge-base services transactional behavior."""

from datetime import datetime, timezone
from pathlib import PurePosixPath

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import (
    CreateDirectoryRequest,
    CreateKnowledgeBaseRequest,
    DeleteDirectoryRequest,
    DeleteKnowledgeBaseRequest,
    DeleteKnowledgeItemRequest,
    FileBuildStatusRequest,
    FileToMarkdownIndexRequest,
    KnowledgeItemDownloadRequest,
    KnowledgeItemGlobRequest,
    KnowledgeItemListDirRequest,
    KnowledgeItemUploadRequest,
    ReadFileRequest,
    UpdateDirectoryRequest,
    UpdateKnowledgeBaseRequest,
)
from by_qa.knowledge_base.infrastructure.storage import StorageLocation, StoredObject
from by_qa.knowledge_base.services import (
    knowledge_item_ingestion_service as ingestion_service_module,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService
from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    KnowledgeItemIngestionService,
)
from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload


async def _async_return(value):
    return value


class FakeConnection:
    """Simple transaction double."""

    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.cursor_obj = FakeServiceCursor()

    def cursor(self):
        return self.cursor_obj

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def close(self):
        return None


class FakeServiceCursor:
    """Minimal cursor double for service-level raw SQL assertions."""

    def __init__(self):
        self.executed = []

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))


class FakeKnowledgeBaseRepository:
    """Repository double for knowledge-base metadata."""

    def __init__(self, *, default_lookup_result=None):
        self.calls = []
        self.existing_by_code = {}
        self.existing_by_name = {}
        self.default_lookup_result = default_lookup_result

    async def create_knowledge_base(self, cursor, **kwargs):
        self.calls.append(("create_knowledge_base", kwargs))
        row = {
            "kid": 7,
            "kb_name": kwargs["kb_name"],
            "kb_description": kwargs.get("kb_description"),
        }
        self.default_lookup_result = row
        return row

    async def get_by_name(self, cursor, kb_name):
        self.calls.append(("get_by_name", {"kb_name": kb_name}))
        return self.existing_by_name.get(kb_name)

    async def get_by_code(self, cursor, kb_code):
        self.calls.append(("get_by_code", {"kb_code": kb_code}))
        if kb_code in self.existing_by_code:
            row = self.existing_by_code[kb_code]
            if row.get("is_deleted") is True:
                return None
            return row
        return self.default_lookup_result

    async def get_any_by_code(self, cursor, kb_code):
        self.calls.append(("get_any_by_code", {"kb_code": kb_code}))
        if kb_code in self.existing_by_code:
            return self.existing_by_code[kb_code]
        return self.default_lookup_result

    async def soft_delete_by_code(self, cursor, *, kb_code):
        self.calls.append(("soft_delete_by_code", {"kb_code": kb_code}))

    async def update_knowledge_base(self, cursor, *, kb_code, updates):
        self.calls.append(
            ("update_knowledge_base", {"kb_code": kb_code, "updates": updates})
        )
        existing = self.existing_by_code.get(kb_code) or self.default_lookup_result
        if existing is None:
            return
        for key, value in updates.items():
            existing[key] = value

    async def update_root_entry(self, cursor, *, knowledge_base_id, root_entry_id):
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

    async def ensure_root_entry(self, cursor, *, knowledge_base_id, kb_name):
        self.calls.append(
            (
                "ensure_root_entry",
                {"knowledge_base_id": knowledge_base_id, "kb_name": kb_name},
            )
        )
        return self.root_entry

    async def rename_entry(self, cursor, *, entry_id, new_name):
        self.calls.append(
            ("rename_entry", {"entry_id": entry_id, "new_name": new_name})
        )
        entry = self.entry_by_id.get(entry_id)
        if entry is not None:
            entry["name"] = new_name

    async def create_directory_entry(
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

    async def create_file_entry(
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

    async def update_file_entry_storage(self, cursor, **kwargs):
        self.calls.append(("update_file_entry_storage", kwargs))

    async def list_subtree_entry_ids(
        self, cursor, *, knowledge_base_id, root_fs_entry_id
    ):
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

    async def soft_delete_subtree(self, cursor, *, knowledge_base_id, root_fs_entry_id):
        self.calls.append(
            (
                "soft_delete_subtree",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "root_fs_entry_id": root_fs_entry_id,
                },
            )
        )

    async def ensure_file_entry(
        self, cursor, *, knowledge_base_id, root_entry_id, full_path
    ):
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

    async def list_root_entries(self, cursor, *, kb_codes):
        self.calls.append(("list_root_entries", {"kb_codes": kb_codes}))
        entries = []
        for kb_code in kb_codes:
            entries.extend(self.root_entries_by_kb_code.get(kb_code, []))
        return entries

    async def list_root_nodes(self, cursor, *, kb_codes):
        self.calls.append(("list_root_nodes", {"kb_codes": kb_codes}))
        nodes = []
        for kb_code in kb_codes:
            nodes.extend(self.root_nodes_by_kb_code.get(kb_code, []))
        return nodes

    async def list_all_root_nodes(self, cursor):
        self.calls.append(("list_all_root_nodes", {}))
        nodes = []
        for node_list in self.root_nodes_by_kb_code.values():
            nodes.extend(node_list)
        return nodes

    async def get_directory_by_path(self, cursor, *, knowledge_base_id, full_path):
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

    async def list_children_by_parent_entry_id(
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

    async def get_file_by_path(self, cursor, *, knowledge_base_id, full_path):
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

    async def get_entry_by_id(self, cursor, *, entry_id):
        self.calls.append(("get_entry_by_id", {"entry_id": entry_id}))
        return self.entry_by_id.get(entry_id)

    async def get_child_entry(
        self, cursor, *, knowledge_base_id, parent_entry_id, name
    ):
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

    async def get_virtual_path_by_entry_id(self, cursor, *, entry_id):
        self.calls.append(("get_virtual_path_by_entry_id", {"entry_id": entry_id}))
        entry = self.entry_by_id.get(entry_id)
        if entry is None:
            return None
        if entry_id == 80:
            return f"考勤制度/{entry['name']}"
        if entry_id == 71:
            return f"考勤制度/{entry['name']}"
        return None

    async def list_children(self, cursor, *, parent_path_ltree):
        self.calls.append(("list_children", {"parent_path_ltree": parent_path_ltree}))
        if parent_path_ltree == self.directory_entry["path_ltree"]:
            return list(self.directory_children)
        return []

    async def list_child_nodes(self, cursor, *, parent_path_ltree):
        self.calls.append(
            ("list_child_nodes", {"parent_path_ltree": parent_path_ltree})
        )
        return list(self.child_nodes_by_parent.get(parent_path_ltree, []))

    async def list_entries_by_path_pattern(
        self, cursor, *, path_regex, ancestor_path_ltree=None
    ):
        self.calls.append(
            (
                "list_entries_by_path_pattern",
                {"path_regex": path_regex, "ancestor_path_ltree": ancestor_path_ltree},
            )
        )
        return list(self.pattern_matches)

    async def get_current_file_version_by_entry_id(self, cursor, *, fs_entry_id):
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

    async def soft_delete_by_knowledge_base_id(self, cursor, *, knowledge_base_id):
        self.calls.append(
            (
                "soft_delete_by_knowledge_base_id",
                {"knowledge_base_id": knowledge_base_id},
            )
        )

    async def soft_delete_file_entry(self, cursor, *, knowledge_base_id, fs_entry_id):
        self.calls.append(
            (
                "soft_delete_file_entry",
                {"knowledge_base_id": knowledge_base_id, "fs_entry_id": fs_entry_id},
            )
        )

    async def update_markdown_metadata(
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

    async def replace_for_version(self, cursor, **kwargs):
        self.calls.append(("replace_for_version", kwargs))
        chunks = kwargs["chunks"]
        return [
            {"kid": 100 + item["chunk_no"], "chunk_no": item["chunk_no"]}
            for item in chunks
        ]

    async def replace_embeddings(self, cursor, **kwargs):
        self.calls.append(("replace_embeddings", kwargs))

    async def replace_for_fs_entry(self, cursor, *, fs_entry_id, chunks):
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

    async def refresh_for_item(self, cursor, **kwargs):
        self.calls.append(("refresh_for_item", kwargs))

    async def delete_for_knowledge_base(self, cursor, **kwargs):
        self.calls.append(("delete_for_knowledge_base", kwargs))

    async def delete_for_item(self, cursor, **kwargs):
        self.calls.append(("delete_for_item", kwargs))

    async def delete_for_fs_entry_ids(self, cursor, **kwargs):
        self.calls.append(("delete_for_fs_entry_ids", kwargs))

    async def refresh_for_fs_entry(
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


class FakeStorageProvider:
    """Storage provider double implementing KnowledgeStorageProvider with backward compat."""

    def __init__(self):
        self.provider_name = "fake"
        self.storage_path_bound_to_logical_path = False
        # Self-reference for backward compat via .storage access
        self.storage = self
        # Protocol tracking
        self.written: list[tuple[StorageLocation, bytes, str]] = []
        self.reads: list[StorageLocation] = []
        self.moved: list[tuple[StorageLocation, StorageLocation]] = []
        # Backward compat attributes (old FakeStorageProvider)
        self.bucket_name = "knowledge-base"
        self.markdown_bucket_name = "knowledge-base-markdown"
        self.uploaded: list = []
        self.promoted: list = []
        self.deleted: list = []
        self.downloaded: list = []
        self.object_payloads = {
            (
                "knowledge-base-markdown",
                "kb/7/item/10/version/v1/markdown",
            ): b"line1\nline2\nline3\n",
        }

    # ── Protocol methods (KnowledgeStorageProvider) ──────────────────────
    # pylint: disable=unused-argument

    def build_original_location(
        self,
        *,
        kb_code: str,
        knowledge_base_id: int,
        fs_entry_id: int,
        file_path: str,
        mime_type: str,
    ) -> StorageLocation:
        suffix = PurePosixPath(file_path.strip("/")).suffix
        return StorageLocation(
            namespace=self.bucket_name,
            key=f"kb/{knowledge_base_id}/fs-entry/{fs_entry_id}/original{suffix}",
        )

    def build_markdown_location(
        self,
        *,
        kb_code: str,
        knowledge_base_id: int,
        fs_entry_id: int,
        file_path: str,
    ) -> StorageLocation:
        return StorageLocation(
            namespace=self.markdown_bucket_name,
            key=f"kb/{knowledge_base_id}/fs-entry/{fs_entry_id}/markdown.md",
        )

    async def write(
        self,
        location: StorageLocation,
        content: bytes,
        *,
        content_type: str,
    ) -> StoredObject:
        self.written.append((location, content, content_type))
        return StoredObject(
            location=location,
            size=len(content),
            content_type=content_type,
        )

    async def read(self, location: StorageLocation) -> bytes:
        self.reads.append(location)
        key = (location.namespace, location.key)
        return self.object_payloads.get(key, b"")

    async def delete_quietly(self, location: StorageLocation) -> None:
        self.deleted.append((location.key, location.namespace))

    async def move(
        self,
        source: StorageLocation,
        target: StorageLocation,
        *,
        overwrite: bool = False,
    ) -> None:
        self.moved.append((source, target))

    # ── Backward compat methods (old FakeStorageProvider API) ──────────────

    async def upload_temp_object(
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

    async def promote_temp_object(
        self, temp_object_key, final_object_key, *, bucket_name=None
    ):
        self.promoted.append((temp_object_key, final_object_key, bucket_name))

    async def delete_object_quietly(self, object_key, *, bucket_name=None):
        self.deleted.append((object_key, bucket_name))

    async def download_object(self, object_key, *, bucket_name=None):
        self.downloaded.append((object_key, bucket_name))
        return self.object_payloads[(bucket_name or self.bucket_name, object_key)]

    def build_access_url(self, object_key, *, expires, bucket_name=None):
        target_bucket = bucket_name or self.bucket_name
        return f"https://minio.example/{target_bucket}/{object_key}?ttl={int(expires.total_seconds())}"


class FakeKnowledgeFetchCacheRepository:
    """Repository double for fetch cache index rows."""

    def __init__(self):
        self.calls = []
        self.entries_by_fs_entry_id = {}

    async def upsert_cache_entry(self, cursor, **kwargs):
        self.calls.append(("upsert_cache_entry", kwargs))
        self.entries_by_fs_entry_id[kwargs["fs_entry_id"]] = {
            "kid": 301,
            "fs_entry_id": kwargs["fs_entry_id"],
            "checksum": kwargs["checksum"],
            "cache_file_path": kwargs["cache_file_path"],
            "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "cache_status": "READY",
        }
        return {"kid": 301}

    async def get_by_fs_entry_id(self, cursor, *, fs_entry_id):
        self.calls.append(
            (
                "get_by_fs_entry_id",
                {"fs_entry_id": fs_entry_id},
            )
        )
        return self.entries_by_fs_entry_id.get(fs_entry_id)

    async def touch_cache_entry(self, cursor, *, cache_entry_id, cache_ttl_seconds):
        self.calls.append(
            (
                "touch_cache_entry",
                {
                    "cache_entry_id": cache_entry_id,
                    "cache_ttl_seconds": cache_ttl_seconds,
                },
            )
        )


class FakeKnowledgeBuildTaskRepository:
    """Repository double for file build task lookups."""

    def __init__(self):
        self.calls = []
        self.latest_task_by_fs_entry_id = {}
        self.raise_on_create = False

    async def get_latest_by_fs_entry_id(self, cursor, *, fs_entry_id):
        self.calls.append(("get_latest_by_fs_entry_id", {"fs_entry_id": fs_entry_id}))
        return self.latest_task_by_fs_entry_id.get(fs_entry_id)

    async def create_task(
        self, cursor, *, knowledge_base_id, fs_entry_id, status, current_step
    ):
        if self.raise_on_create:
            raise ValueError("running task already exists")
        task = {
            "kid": 9901,
            "knowledge_base_id": knowledge_base_id,
            "fs_entry_id": fs_entry_id,
            "status": status,
            "current_step": current_step,
        }
        self.calls.append(
            (
                "create_task",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "fs_entry_id": fs_entry_id,
                    "status": status,
                    "current_step": current_step,
                },
            )
        )
        self.latest_task_by_fs_entry_id[fs_entry_id] = task
        return task

    async def update_task(
        self,
        cursor,
        *,
        task_id,
        status=None,
        current_step=None,
        error_message=None,
        finished=False,
    ):
        self.calls.append(
            (
                "update_task",
                {
                    "task_id": task_id,
                    "status": status,
                    "current_step": current_step,
                    "error_message": error_message,
                    "finished": finished,
                },
            )
        )


async def test_create_knowledge_base_commits_and_returns_business_fields():
    """Knowledge base creation should generate kb_code from the persisted row id."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(default_lookup_result=None)
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = await service.create_knowledge_base(
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


async def test_create_knowledge_base_rejects_duplicate_name():
    """Knowledge base creation should reject duplicate kb names."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(default_lookup_result=None)
    knowledge_base_repository.existing_by_name["人力制度知识库"] = {
        "kid": 9,
        "kb_name": "人力制度知识库",
    }
    fs_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=fs_repository,
    )

    try:
        await service.create_knowledge_base(
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


async def test_delete_knowledge_base_marks_kb_and_descendants_deleted():
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
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = await service.delete_knowledge_base(
        DeleteKnowledgeBaseRequest(kb_code="hr-policy")
    )

    assert response is None
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
    assert any(
        "update knowledge_file_metadata_value" in sql.lower()
        and "set is_deleted = true" in sql.lower()
        and params == {"knowledge_base_id": 7}
        for sql, params in connection.cursor_obj.executed
    )


async def test_update_knowledge_base_commits_and_returns_success():
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
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = await service.update_knowledge_base(
        UpdateKnowledgeBaseRequest(
            knCode="hr-policy",
            knName="新知识库名称",
            knDescription="新描述",
        )
    )

    assert response is None
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


async def test_update_knowledge_base_rejects_missing_kb():
    """Updating a KB should fail when kb_code does not exist."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(default_lookup_result=None)
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
    )

    try:
        await service.update_knowledge_base(
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


async def test_update_knowledge_base_rejects_duplicate_name():
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
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
    )

    try:
        await service.update_knowledge_base(
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


async def test_update_knowledge_base_keeps_omitted_fields_unchanged():
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
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = await service.update_knowledge_base(
        UpdateKnowledgeBaseRequest(
            kb_code="hr-policy",
            kb_name="新知识库名称",
        )
    )

    assert response is None
    assert (
        "update_knowledge_base",
        {
            "kb_code": "hr-policy",
            "updates": {"kb_name": "新知识库名称"},
        },
    ) in knowledge_base_repository.calls


async def test_update_knowledge_base_clears_fields_only_when_null_is_explicit():
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
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=FakeKnowledgeFsEntryRepository(),
    )

    response = await service.update_knowledge_base(
        UpdateKnowledgeBaseRequest(
            kb_code="hr-policy",
            kb_description=None,
        )
    )

    assert response is None
    assert (
        "update_knowledge_base",
        {
            "kb_code": "hr-policy",
            "updates": {"kb_description": None},
        },
    ) in knowledge_base_repository.calls


async def test_create_directory_commits_and_returns_business_fields():
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
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=knowledge_base_repository,
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = await service.create_directory(
        CreateDirectoryRequest(
            knCode="hr-policy",
            directoryPath="/考勤制度/归档",
            directoryDescription="考勤制度归档目录",
        )
    )

    assert response is None
    assert connection.committed is True
    assert (
        "create_directory_entry",
        {
            "knowledge_base_id": 7,
            "full_path": "考勤制度/归档",
            "directory_description": "考勤制度归档目录",
        },
    ) in knowledge_fs_entry_repository.calls


async def test_create_directory_supports_recursive_creation():
    """Directory creation should support recursive parent creation."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
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

    response = await service.create_directory(
        CreateDirectoryRequest(
            knCode="hr-policy",
            directoryPath="/missing-dir/归档",
            directoryDescription=None,
        )
    )

    assert response is None
    assert connection.committed is True


async def test_delete_directory_marks_subtree_deleted_and_clears_projection():
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
        connection_factory=lambda: _async_return(connection),
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

    response = await service.delete_directory(
        DeleteDirectoryRequest(
            knCode="hr-policy",
            directoryPath="/考勤制度/归档",
        )
    )

    assert response is None
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
    assert any(
        "delete from knowledge_chunk_retrieval_mv" in sql.lower()
        and params == {"knowledge_base_id": 7, "fs_entry_ids": [81, 82, 83]}
        for sql, params in connection.cursor_obj.executed
    )
    assert any(
        "update knowledge_file_metadata_value" in sql.lower()
        and "set is_deleted = true" in sql.lower()
        and params == {"knowledge_base_id": 7, "fs_entry_ids": [81, 82, 83]}
        for sql, params in connection.cursor_obj.executed
    )


async def test_delete_directory_rejects_missing_directory():
    """Deleting a directory should fail when the path does not exist."""
    connection = FakeConnection()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
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
        await service.delete_directory(
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


async def test_update_directory_renames_directory_by_path():
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
        connection_factory=lambda: _async_return(connection),
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

    response = await service.update_directory(
        UpdateDirectoryRequest(
            knCode="hr-policy",
            directoryPath="/考勤制度/归档",
            directoryName="历史归档",
        )
    )

    assert response is None
    assert connection.committed is True
    assert (
        "get_directory_by_path",
        {"knowledge_base_id": 7, "full_path": "考勤制度/归档"},
    ) in knowledge_fs_entry_repository.calls
    assert (
        "rename_entry",
        {"entry_id": 80, "new_name": "历史归档"},
    ) in knowledge_fs_entry_repository.calls


async def test_update_directory_allows_same_name_without_conflict():
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
        connection_factory=lambda: _async_return(connection),
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

    response = await service.update_directory(
        UpdateDirectoryRequest(
            knCode="hr-policy",
            directoryPath="/考勤制度/归档",
            directoryName="归档",
        )
    )

    assert response is None
    assert connection.committed is True


async def test_update_directory_rejects_sibling_name_conflict():
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
        connection_factory=lambda: _async_return(connection),
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
        await service.update_directory(
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


async def test_delete_knowledge_item_marks_file_entry_deleted_and_clears_artifacts():
    """Deleting one file should logically delete the file entry and clear derived artifacts."""
    connection = FakeConnection()
    storage_provider = FakeStorageProvider()
    storage_provider.storage_path_bound_to_logical_path = True
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
        connection_factory=lambda: _async_return(connection),
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
        storage_provider=storage_provider,
        embedding_dimension=2,
    )

    response = await service.delete_knowledge_item(
        DeleteKnowledgeItemRequest(kb_code="hr-policy", file_path="/Policies/delete.md")
    )

    assert response is None
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
    assert any(
        "update knowledge_file_metadata_value" in sql.lower()
        and "set is_deleted = true" in sql.lower()
        and params == {"knowledge_base_id": 7, "fs_entry_id": 71}
        for sql, params in connection.cursor_obj.executed
    )
    assert (
        "kb/7/fs-entry/71/original.md",
        "knowledge-base",
    ) in storage_provider.deleted
    assert (
        "kb/7/fs-entry/71/markdown.md",
        "knowledge-base-markdown",
    ) in storage_provider.deleted


async def test_create_knowledge_base_emits_internal_key_node_logs(monkeypatch):
    """Knowledge base creation should log persistence and commit steps."""
    connection = FakeConnection()
    knowledge_base_repository = FakeKnowledgeBaseRepository(default_lookup_result=None)
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
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

    response = await service.create_knowledge_base(
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


async def test_upload_file_commits_object_and_updates_fs_entry_storage():
    """Multipart upload should only persist original file metadata on the fs entry."""
    connection = FakeConnection()
    storage = FakeStorageProvider()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: _async_return(connection),
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
        storage_provider=storage,
        embedding_dimension=2,
    )

    response = await service.upload_file(
        KnowledgeItemUploadRequest(
            knCode="hr-policy",
            filePath="/dir1/item-1.pdf",
            fileDescription="操作手册",
            fileContent=b"pdf-bytes",
        )
    )

    assert response is None
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
    assert storage_call[1]["file_object_key"] == "kb/7/fs-entry/71/original.pdf"
    assert storage_call[1]["file_size"] == len(b"pdf-bytes")
    assert storage_call[1]["mime_type"] == "application/pdf"
    assert len(storage.written) == 1
    loc, content, ct = storage.written[0]
    assert loc.namespace == "knowledge-base"
    assert loc.key == "kb/7/fs-entry/71/original.pdf"
    assert content == b"pdf-bytes"
    assert ct == "application/pdf"


async def test_upload_file_recursively_creates_missing_parent_directories():
    """Multipart upload should recursively create missing parent directories."""
    connection = FakeConnection()
    storage = FakeStorageProvider()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: _async_return(connection),
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
        storage_provider=storage,
        embedding_dimension=2,
    )

    response = await service.upload_file(
        KnowledgeItemUploadRequest(
            knCode="hr-policy",
            filePath="/missing-dir/item-1.pdf",
            fileContent=b"pdf-bytes",
        )
    )

    assert response is None
    assert connection.committed is True
    assert (
        "create_file_entry",
        {
            "knowledge_base_id": 7,
            "full_path": "missing-dir/item-1.pdf",
            "file_description": None,
        },
    ) in knowledge_fs_entry_repository.calls
    assert len(storage.written) == 1
    loc, content, ct = storage.written[0]
    assert loc.namespace == "knowledge-base"
    assert loc.key == "kb/7/fs-entry/71/original.pdf"
    assert content == b"pdf-bytes"
    assert ct == "application/pdf"


async def test_upload_file_writes_via_provider_and_persists_locator():
    """upload_file should write via the provider and persist namespace/key on the fs entry."""
    connection = FakeConnection()
    storage = FakeStorageProvider()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: _async_return(connection),
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
        storage_provider=storage,
        embedding_dimension=2,
    )

    await service.upload_file(
        KnowledgeItemUploadRequest(
            knCode="hr-policy",
            filePath="/doc.md",
            fileContent=b"# Hello",
        )
    )

    # Provider was called with correct location
    assert len(storage.written) == 1
    loc, content, ct = storage.written[0]
    assert loc.namespace == "knowledge-base"
    assert loc.key == "kb/7/fs-entry/71/original.md"
    assert content == b"# Hello"
    assert ct == "text/markdown"

    # Repository persisted the namespace/key from the stored location
    storage_call = [
        call
        for call in knowledge_fs_entry_repository.calls
        if call[0] == "update_file_entry_storage"
    ][0]
    assert storage_call[1]["file_bucket_name"] == "knowledge-base"
    assert storage_call[1]["file_object_key"] == "kb/7/fs-entry/71/original.md"
    assert storage_call[1]["file_size"] == len(b"# Hello")
    assert storage_call[1]["mime_type"] == "text/markdown"


async def test_list_dir_root_returns_top_level_entries():
    """Root listing should return top-level entries inside the requested knowledge base."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = await service.list_dir(
        KnowledgeItemListDirRequest(kb_code="hr-policy", directory_path="/")
    )

    assert response.model_dump()["data"] == [
        {"kb_code": "hr-policy", "name": "/dir1", "type": "directory", "size": 0}
    ]
    assert knowledge_fs_entry_repository.calls == [
        (
            "list_children_by_parent_entry_id",
            {"knowledge_base_id": 7, "parent_entry_id": None},
        )
    ]


async def test_list_dir_directory_path_returns_direct_children_only():
    """Directory path should resolve the directory and list its direct children."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = await service.list_dir(
        KnowledgeItemListDirRequest(kb_code="hr-policy", directory_path="/dir1")
    )

    assert response.model_dump()["data"] == [
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


async def test_list_dir_literal_missing_path_raises_not_found():
    """List-dir should raise not-found when the requested literal path does not exist."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    try:
        await service.list_dir(
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


async def test_glob_pattern_matches_one_segment_at_a_time():
    """Glob should match pathRule with * limited to one path segment."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    response = await service.glob(
        KnowledgeItemGlobRequest(kb_code="hr-policy", path_rule="/dir1/*.md")
    )

    assert response.model_dump()["data"] == [
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


async def test_glob_rejects_double_star_multi_level_matching():
    """Glob should reject ** because only single-level * matching is supported."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    try:
        await service.glob(
            KnowledgeItemGlobRequest(kb_code="hr-policy", path_rule="/dir1/**/*.md")
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "pathRule does not support ** multi-level matching"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert knowledge_fs_entry_repository.calls == []


async def test_list_dir_raises_when_knowledge_base_is_missing():
    """List-dir should fail when knCode does not resolve to a knowledge base."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result=None
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
    )

    try:
        await service.list_dir(
            KnowledgeItemListDirRequest(kb_code="missing-kb", directory_path="/")
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "knowledge base not found: missing-kb"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")

    assert knowledge_fs_entry_repository.calls == []


async def test_file_build_status_returns_latest_task_for_file():
    """Build-status should resolve the file and return its latest task snapshot."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    build_task_repository = FakeKnowledgeBuildTaskRepository()
    knowledge_fs_entry_repository.file_entry_by_path["制度/人事/请假制度.pdf"] = {
        "kid": 71,
        "knowledge_base_id": 7,
        "parent_entry_id": 80,
        "entry_type": "FILE",
        "name": "请假制度.pdf",
        "path_ltree": "d1_a.f2_doc",
        "depth": 2,
    }
    build_task_repository.latest_task_by_fs_entry_id[71] = {
        "kid": 9001,
        "status": "running",
        "current_step": "chunking",
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_build_task_repository=build_task_repository,
    )

    response = await service.file_build_status(
        FileBuildStatusRequest(kb_code="hr-policy", file_path="/制度/人事/请假制度.pdf")
    )

    assert response == {
        "status": "running",
        "currentStep": "chunking",
        "statusDict": [
            {
                "standCode": "complete",
                "standDisplayValue": "已完成",
                "standDisplayValueEn": "complete",
            },
            {
                "standCode": "failed",
                "standDisplayValue": "失败",
                "standDisplayValueEn": "failed",
            },
            {
                "standCode": "running",
                "standDisplayValue": "构建中",
                "standDisplayValueEn": "running",
            },
        ],
        "stepDict": [
            {
                "standCode": "markdown",
                "standDisplayValue": "原始文件转 Markdown",
                "standDisplayValueEn": "markdown",
            },
            {
                "standCode": "chunking",
                "standDisplayValue": "文档切片",
                "standDisplayValueEn": "chunking",
            },
            {
                "standCode": "vectorizing",
                "standDisplayValue": "切片向量化",
                "standDisplayValueEn": "vectorizing",
            },
            {
                "standCode": "complete",
                "standDisplayValue": "已完成",
                "standDisplayValueEn": "complete",
            },
        ],
    }
    assert knowledge_fs_entry_repository.calls == [
        (
            "get_file_by_path",
            {
                "knowledge_base_id": 7,
                "full_path": "制度/人事/请假制度.pdf",
            },
        )
    ]
    assert build_task_repository.calls == [
        ("get_latest_by_fs_entry_id", {"fs_entry_id": 71})
    ]


async def test_file_build_status_rejects_missing_file():
    """Build-status should fail when the requested file path does not exist."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_build_task_repository=FakeKnowledgeBuildTaskRepository(),
    )

    try:
        await service.file_build_status(
            FileBuildStatusRequest(kb_code="hr-policy", file_path="/missing.pdf")
        )
    except KnowledgeBaseValidationError as exc:
        assert str(exc) == "file not found: /missing.pdf"
    else:
        raise AssertionError("expected KnowledgeBaseValidationError")


async def test_download_file_returns_original_bytes(tmp_path):
    """Download-file should fetch the current original object bytes."""
    connection = FakeConnection()
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    storage = FakeStorageProvider()
    storage.object_payloads[("knowledge-base", "kb/7/fs-entry/71/original.pdf")] = (
        b"%PDF-1.4 binary payload"
    )
    cache_repository = FakeKnowledgeFetchCacheRepository()
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        knowledge_fetch_cache_repository=cache_repository,
        storage_provider=storage,
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

    response = await service.download_file(
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
    assert len(storage.reads) == 1
    assert storage.reads[0] == StorageLocation(
        namespace="knowledge-base", key="kb/7/fs-entry/71/original.pdf"
    )
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


async def test_file_to_markdown_index_kb_not_found():
    """fileToMarkdownIndex returns error when knowledge base does not exist."""
    import pytest

    kb_repo = FakeKnowledgeBaseRepository(default_lookup_result=None)
    fs_repo = FakeKnowledgeFsEntryRepository()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: _async_return(FakeConnection()),
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        storage_provider=FakeStorageProvider(),
        embedding_dimension=3,
    )
    request = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "999", "filePath": "/doc.pdf"}
    )
    with pytest.raises(KnowledgeBaseValidationError, match="knowledge base not found"):
        await service.file_to_markdown_index(
            request, document_chunking_service=FakeDocumentChunkingService()
        )


async def test_file_to_markdown_index_file_not_found():
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
        connection_factory=lambda: _async_return(FakeConnection()),
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        storage_provider=FakeStorageProvider(),
        embedding_dimension=3,
    )
    request = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "1", "filePath": "/nonexistent.pdf"}
    )
    with pytest.raises(KnowledgeBaseValidationError, match="file not found"):
        await service.file_to_markdown_index(
            request, document_chunking_service=FakeDocumentChunkingService()
        )


async def test_file_to_markdown_index_file_not_uploaded():
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
        connection_factory=lambda: _async_return(FakeConnection()),
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        storage_provider=FakeStorageProvider(),
        embedding_dimension=3,
    )
    request = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"}
    )
    with pytest.raises(
        KnowledgeBaseValidationError, match="file has not been uploaded"
    ):
        await service.file_to_markdown_index(
            request, document_chunking_service=FakeDocumentChunkingService()
        )


async def test_file_to_markdown_index_maps_create_task_race_to_running_task_error():
    """Task creation races should surface as the documented duplicate-task error."""
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
            "file_bucket_name": "test-bucket",
            "file_object_key": "kb/7/fs-entry/71/original.pdf",
            "mime_type": "application/pdf",
        }
    }
    build_task_repo = FakeKnowledgeBuildTaskRepository()
    build_task_repo.raise_on_create = True
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: _async_return(FakeConnection()),
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_build_task_repository=build_task_repo,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        storage_provider=FakeStorageProvider(),
        embedding_dimension=3,
    )
    request = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"}
    )

    with pytest.raises(
        KnowledgeBaseValidationError,
        match="build task already exists for file: /制度/人事/请假制度.pdf",
    ):
        await service.create_file_to_markdown_index_task(request)


async def test_file_to_markdown_index_rejects_running_task():
    """fileToMarkdownIndex rejects duplicate requests when a task is already running."""
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
            "file_bucket_name": "test-bucket",
            "file_object_key": "kb/7/fs-entry/71/original.pdf",
            "mime_type": "application/pdf",
        }
    }
    build_task_repo = FakeKnowledgeBuildTaskRepository()
    build_task_repo.latest_task_by_fs_entry_id[71] = {
        "kid": 9001,
        "status": "running",
        "current_step": "chunking",
    }
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: _async_return(FakeConnection()),
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_build_task_repository=build_task_repo,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        storage_provider=FakeStorageProvider(),
        embedding_dimension=3,
    )
    request = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"}
    )

    with pytest.raises(
        KnowledgeBaseValidationError,
        match="build task already exists for file: /制度/人事/请假制度.pdf",
    ):
        await service.file_to_markdown_index(
            request, document_chunking_service=FakeDocumentChunkingService()
        )


async def test_file_to_markdown_index_success():
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
    build_task_repo = FakeKnowledgeBuildTaskRepository()
    obj_storage = FakeStorageProvider()
    obj_storage.object_payloads[("test-bucket", "kb/7/fs-entry/71/original.pdf")] = (
        b"fake-pdf-bytes"
    )
    chunking_service = FakeDocumentChunkingService()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: _async_return(FakeConnection()),
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_build_task_repository=build_task_repo,
        knowledge_item_chunk_repository=chunk_repo,
        retrieval_projection_repository=retrieval_repo,
        storage_provider=obj_storage,
        embedding_dimension=3,
    )
    request = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"}
    )
    await service.file_to_markdown_index(
        request, document_chunking_service=chunking_service
    )

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

    assert build_task_repo.calls == [
        ("get_latest_by_fs_entry_id", {"fs_entry_id": 71}),
        (
            "create_task",
            {
                "knowledge_base_id": 7,
                "fs_entry_id": 71,
                "status": "running",
                "current_step": "markdown",
            },
        ),
        (
            "update_task",
            {
                "task_id": 9901,
                "status": "running",
                "current_step": "chunking",
                "error_message": None,
                "finished": False,
            },
        ),
        (
            "update_task",
            {
                "task_id": 9901,
                "status": "running",
                "current_step": "vectorizing",
                "error_message": None,
                "finished": False,
            },
        ),
        (
            "update_task",
            {
                "task_id": 9901,
                "status": "complete",
                "current_step": "complete",
                "error_message": None,
                "finished": True,
            },
        ),
    ]


async def test_execute_file_to_markdown_index_reads_and_writes_via_provider():
    """fileToMarkdownIndex reads original and writes markdown via provider protocol."""
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
        "doc.md": {
            "kid": 71,
            "entry_type": "FILE",
            "name": "doc.md",
            "file_bucket_name": "my-bucket",
            "file_object_key": "kb/7/fs-entry/71/original.md",
            "mime_type": "text/markdown",
        }
    }
    build_task_repo = FakeKnowledgeBuildTaskRepository()
    obj_storage = FakeStorageProvider()
    obj_storage.object_payloads[("my-bucket", "kb/7/fs-entry/71/original.md")] = (
        b"# Hello\n\nWorld"
    )
    chunking_service = FakeDocumentChunkingService()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: _async_return(FakeConnection()),
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_build_task_repository=build_task_repo,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        storage_provider=obj_storage,
        embedding_dimension=3,
    )
    request = FileToMarkdownIndexRequest.model_validate(
        {"knCode": "1", "filePath": "/doc.md"}
    )
    await service.file_to_markdown_index(
        request, document_chunking_service=chunking_service
    )

    # Provider.read was called for the original file
    assert len(obj_storage.reads) == 1
    read_loc = obj_storage.reads[0]
    assert read_loc.namespace == "my-bucket"
    assert read_loc.key == "kb/7/fs-entry/71/original.md"

    # Provider.write was called for the markdown
    write_calls = [w for w in obj_storage.written if w[0].key.endswith("markdown.md")]
    assert len(write_calls) == 1
    write_loc, _, write_ct = write_calls[0]
    assert write_loc.namespace == "knowledge-base-markdown"
    assert write_loc.key == "kb/7/fs-entry/71/markdown.md"
    assert write_ct == "text/markdown; charset=utf-8"

    # No backward-compat temp upload or promote should have happened
    assert len(obj_storage.uploaded) == 0
    assert len(obj_storage.promoted) == 0


async def test_file_to_markdown_index_offloads_sync_work_to_threads(monkeypatch):
    """Heavy sync extraction and chunking work should be offloaded via asyncio.to_thread."""
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
    build_task_repo = FakeKnowledgeBuildTaskRepository()
    obj_storage = FakeStorageProvider()
    obj_storage.object_payloads[("test-bucket", "kb/7/fs-entry/71/original.pdf")] = (
        b"fake-pdf-bytes"
    )
    chunking_service = FakeDocumentChunkingService()
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: _async_return(FakeConnection()),
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_build_task_repository=build_task_repo,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        storage_provider=obj_storage,
        embedding_dimension=3,
    )
    calls = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(ingestion_service_module.asyncio, "to_thread", fake_to_thread)

    await service.file_to_markdown_index(
        FileToMarkdownIndexRequest.model_validate(
            {"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"}
        ),
        document_chunking_service=chunking_service,
    )

    assert [item[0] for item in calls] == [
        "extract_text_from_file",
        "chunk_and_embed",
    ]


async def test_file_to_markdown_index_rebuilds_after_failed_task():
    """fileToMarkdownIndex creates a new task when the previous task failed."""
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
    build_task_repo = FakeKnowledgeBuildTaskRepository()
    build_task_repo.latest_task_by_fs_entry_id[71] = {
        "kid": 9001,
        "status": "failed",
        "current_step": "vectorizing",
    }
    obj_storage = FakeStorageProvider()
    obj_storage.object_payloads[("test-bucket", "kb/7/fs-entry/71/original.pdf")] = (
        b"fake-pdf-bytes"
    )
    service = KnowledgeItemIngestionService(
        connection_factory=lambda: _async_return(FakeConnection()),
        knowledge_base_repository=kb_repo,
        knowledge_fs_entry_repository=fs_repo,
        knowledge_build_task_repository=build_task_repo,
        knowledge_item_chunk_repository=FakeKnowledgeItemChunkRepository(),
        retrieval_projection_repository=FakeRetrievalProjectionRepository(),
        storage_provider=obj_storage,
        embedding_dimension=3,
    )

    await service.file_to_markdown_index(
        FileToMarkdownIndexRequest.model_validate(
            {"knCode": "1", "filePath": "/制度/人事/请假制度.pdf"}
        ),
        document_chunking_service=FakeDocumentChunkingService(),
    )

    assert ("get_latest_by_fs_entry_id", {"fs_entry_id": 71}) in build_task_repo.calls
    assert (
        "create_task",
        {
            "knowledge_base_id": 7,
            "fs_entry_id": 71,
            "status": "running",
            "current_step": "markdown",
        },
    ) in build_task_repo.calls


async def test_delete_knowledge_item_skips_storage_when_path_not_bound():
    """When storage_path_bound_to_logical_path=False, storage objects are NOT deleted."""
    connection = FakeConnection()
    storage_provider = FakeStorageProvider()
    storage_provider.storage_path_bound_to_logical_path = False
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
        connection_factory=lambda: _async_return(connection),
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
        storage_provider=storage_provider,
        embedding_dimension=2,
    )

    response = await service.delete_knowledge_item(
        DeleteKnowledgeItemRequest(kb_code="hr-policy", file_path="/Policies/delete.md")
    )

    assert response is None
    assert connection.committed is True
    # When path is not bound, no storage objects should be deleted
    assert storage_provider.deleted == []


async def test_delete_knowledge_item_deletes_storage_when_path_bound():
    """When storage_path_bound_to_logical_path=True, storage objects are deleted."""
    connection = FakeConnection()
    storage_provider = FakeStorageProvider()
    storage_provider.storage_path_bound_to_logical_path = True
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
        connection_factory=lambda: _async_return(connection),
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
        storage_provider=storage_provider,
        embedding_dimension=2,
    )

    response = await service.delete_knowledge_item(
        DeleteKnowledgeItemRequest(kb_code="hr-policy", file_path="/Policies/delete.md")
    )

    assert response is None
    assert connection.committed is True
    # Both original and markdown storage locations should be deleted
    assert (
        "kb/7/fs-entry/71/original.md",
        "knowledge-base",
    ) in storage_provider.deleted
    assert (
        "kb/7/fs-entry/71/markdown.md",
        "knowledge-base-markdown",
    ) in storage_provider.deleted


async def test_download_file_reads_via_provider():
    """download_file should call storage_provider.read() with the correct StorageLocation."""
    connection = FakeConnection()
    storage = FakeStorageProvider()
    storage.object_payloads[("my-bucket", "kb/7/fs-entry/71/original.md")] = (
        b"# Test content"
    )
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.file_entry_by_path["dir1/doc.md"] = {
        "kid": 71,
        "knowledge_base_id": 7,
        "parent_entry_id": 80,
        "entry_type": "FILE",
        "name": "doc.md",
        "path_ltree": "d1_a.f2_doc",
        "depth": 2,
        "file_bucket_name": "my-bucket",
        "file_object_key": "kb/7/fs-entry/71/original.md",
        "mime_type": "text/markdown",
        "file_size": 128,
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        storage_provider=storage,
    )

    response = await service.download_file(
        KnowledgeItemDownloadRequest(
            kb_code="hr-policy",
            file_path="/dir1/doc.md",
        )
    )

    assert response["content"] == b"# Test content"
    assert response["filename"] == "doc.md"
    assert response["media_type"] == "text/markdown"
    assert len(storage.reads) == 1
    assert storage.reads[0] == StorageLocation(
        namespace="my-bucket", key="kb/7/fs-entry/71/original.md"
    )


async def test_read_file_reads_markdown_via_provider():
    """read_file should call storage_provider.read() with the correct markdown StorageLocation."""
    connection = FakeConnection()
    storage = FakeStorageProvider()
    storage.object_payloads[
        ("knowledge-base-markdown", "kb/7/fs-entry/71/markdown.md")
    ] = b"# Built Markdown\n\nSome processed content."
    knowledge_fs_entry_repository = FakeKnowledgeFsEntryRepository()
    knowledge_fs_entry_repository.file_entry_by_path["dir1/doc.md"] = {
        "kid": 71,
        "knowledge_base_id": 7,
        "parent_entry_id": 80,
        "entry_type": "FILE",
        "name": "doc.md",
        "path_ltree": "d1_a.f2_doc",
        "depth": 2,
        "markdown_bucket_name": "knowledge-base-markdown",
        "markdown_object_key": "kb/7/fs-entry/71/markdown.md",
    }
    service = KnowledgeBaseService(
        connection_factory=lambda: _async_return(connection),
        knowledge_base_repository=FakeKnowledgeBaseRepository(
            default_lookup_result={"kid": 7, "kb_name": "人力制度知识库"}
        ),
        knowledge_fs_entry_repository=knowledge_fs_entry_repository,
        storage_provider=storage,
    )

    response = await service.read_file(
        ReadFileRequest(
            kb_code="hr-policy",
            file_path="/dir1/doc.md",
        )
    )

    assert response["data"] == "# Built Markdown\n\nSome processed content."
    assert response["knCode"] == "hr-policy"
    assert response["filePath"] == "/dir1/doc.md"
    assert response["reachedEof"] is True
    assert len(storage.reads) == 1
    assert storage.reads[0] == StorageLocation(
        namespace="knowledge-base-markdown", key="kb/7/fs-entry/71/markdown.md"
    )
