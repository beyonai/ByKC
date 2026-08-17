"""Read models used by KnowledgeEntity processing workflows.

The repository deliberately keeps KnowledgeEntity metadata in the existing EAV
table.  Each public read is a single SQL statement and folds the repeated EAV
rows into one Python dictionary per live file.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


class KnowledgeEntityRepository:
    """Read live files together with processing and entity metadata."""

    _METADATA_FIELDS = (
        "documentKind",
        "processingCapabilities",
        "entityName",
        "aliases",
        "definitionVersion",
        "subjectFileId",
        "entityType",
        "enrichVersion",
    )
    _LIST_METADATA_FIELDS = frozenset({"processingCapabilities", "aliases"})
    _OUTPUT_FIELD_BY_PROPERTY = {
        "documentKind": "document_kind",
        "processingCapabilities": "processing_capabilities",
        "entityName": "entity_name",
        "aliases": "aliases",
        "definitionVersion": "definition_version",
        "subjectFileId": "subject_file_id",
        "entityType": "entity_type",
        "enrichVersion": "enrich_version",
    }

    async def get_file_with_metadata(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        file_path: str,
    ) -> dict[str, Any] | None:
        """Return one live file and its KnowledgeEntity-related metadata."""
        normalized_path = self._normalize_path(file_path)
        await cursor.execute(
            f"""
            {self._file_metadata_select()}
            WHERE fe.knowledge_base_id = %(knowledge_base_id)s
              AND fe.virtual_path = %(file_path)s
              AND fe.entry_type = 'FILE'
              AND fe.is_deleted = FALSE
            ORDER BY mv.property_name ASC, mv.kid ASC
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "file_path": normalized_path,
                "property_names": list(self._METADATA_FIELDS),
            },
        )
        records = self._fold_file_rows(await cursor.fetchall())
        return records[0] if records else None

    async def list_files_with_metadata(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        path_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        """List live files in stable path order, optionally below a path prefix."""
        normalized_prefix = (
            self._normalize_path(path_prefix) if path_prefix is not None else None
        )
        await cursor.execute(
            f"""
            {self._file_metadata_select()}
            WHERE fe.knowledge_base_id = %(knowledge_base_id)s
              AND fe.entry_type = 'FILE'
              AND fe.is_deleted = FALSE
              AND (
                    %(path_prefix)s::text IS NULL
                    OR %(path_prefix)s = '/'
                    OR fe.virtual_path = %(path_prefix)s
                    OR LEFT(
                        fe.virtual_path,
                        LENGTH(%(path_prefix)s) + 1
                    ) = %(path_prefix)s || '/'
                  )
            ORDER BY fe.virtual_path ASC, fe.kid ASC,
                     mv.property_name ASC, mv.kid ASC
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "path_prefix": normalized_prefix,
                "property_names": list(self._METADATA_FIELDS),
            },
        )
        return self._fold_file_rows(await cursor.fetchall())

    async def get_files_by_ids(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_ids: list[int],
    ) -> list[dict[str, Any]]:
        """Batch-read live files and metadata without an N+1 query pattern."""
        if not fs_entry_ids:
            return []

        await cursor.execute(
            f"""
            {self._file_metadata_select()}
            WHERE fe.knowledge_base_id = %(knowledge_base_id)s
              AND fe.kid = ANY(%(fs_entry_ids)s)
              AND fe.entry_type = 'FILE'
              AND fe.is_deleted = FALSE
            ORDER BY fe.kid ASC, mv.property_name ASC, mv.kid ASC
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_ids": fs_entry_ids,
                "property_names": list(self._METADATA_FIELDS),
            },
        )
        return self._fold_file_rows(await cursor.fetchall())

    async def list_entity_surfaces(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read the entity surface snapshot for one KB or the entire system.

        The query intentionally returns EAV rows instead of issuing one query per
        entity.  Keeping ``knowledge_base_id`` in every folded result lets the
        matcher use a system-wide vocabulary for recall while its caller anchors
        persisted relations only to entities in the current knowledge base.
        """
        await cursor.execute(
            """
            SELECT
                fe.kid,
                fe.knowledge_base_id,
                fe.name,
                fe.virtual_path AS file_path,
                fe.updated_at,
                mv.kid AS metadata_value_id,
                mv.property_name,
                mv.value_type,
                mv.value_string,
                mv.value_number,
                mv.value_boolean,
                mv.value_datetime,
                mv.value_string_list
            FROM knowledge_fs_entry fe
            JOIN knowledge_file_metadata_value document_kind
              ON document_kind.fs_entry_id = fe.kid
             AND document_kind.is_deleted = FALSE
             AND document_kind.property_name = 'documentKind'
             AND document_kind.value_type = 'string'
             AND document_kind.value_string = 'knowledgeEntity'
            LEFT JOIN knowledge_file_metadata_value mv
              ON mv.fs_entry_id = fe.kid
             AND mv.is_deleted = FALSE
             AND mv.property_name = ANY(%(property_names)s)
            WHERE fe.entry_type = 'FILE'
              AND fe.is_deleted = FALSE
              AND (
                    %(knowledge_base_id)s::bigint IS NULL
                    OR fe.knowledge_base_id = %(knowledge_base_id)s
                  )
            ORDER BY fe.knowledge_base_id ASC, fe.virtual_path ASC, fe.kid ASC,
                     mv.property_name ASC, mv.kid ASC
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "property_names": ["entityName", "aliases", "subjectFileId"],
            },
        )
        return self._fold_surface_rows(await cursor.fetchall())

    @classmethod
    def _file_metadata_select(cls) -> str:
        return """
            SELECT
                fe.kid,
                fe.knowledge_base_id,
                fe.name,
                fe.virtual_path AS file_path,
                fe.checksum,
                fe.file_bucket_name,
                fe.file_object_key,
                fe.markdown_bucket_name,
                fe.markdown_object_key,
                fe.mime_type,
                fe.line_count,
                fe.updated_at,
                mv.kid AS metadata_value_id,
                mv.property_name,
                mv.value_type,
                mv.value_string,
                mv.value_number,
                mv.value_boolean,
                mv.value_datetime,
                mv.value_string_list
            FROM knowledge_fs_entry fe
            LEFT JOIN knowledge_file_metadata_value mv
              ON mv.fs_entry_id = fe.kid
             AND mv.is_deleted = FALSE
             AND mv.property_name = ANY(%(property_names)s)
        """

    @classmethod
    def _fold_file_rows(cls, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        records: dict[int, dict[str, Any]] = {}
        for row in rows:
            file_id = int(row["kid"])
            record = records.get(file_id)
            if record is None:
                record = {
                    "kid": file_id,
                    "knowledge_base_id": row["knowledge_base_id"],
                    "name": row["name"],
                    "file_path": row["file_path"],
                    "checksum": row["checksum"],
                    "file_bucket_name": row["file_bucket_name"],
                    "file_object_key": row["file_object_key"],
                    "markdown_bucket_name": row["markdown_bucket_name"],
                    "markdown_object_key": row["markdown_object_key"],
                    "mime_type": row["mime_type"],
                    "line_count": row["line_count"],
                    "updated_at": row["updated_at"],
                    "document_kind": None,
                    "document_kind_configured": False,
                    "processing_capabilities": [],
                    "processing_capabilities_configured": False,
                    "entity_name": None,
                    "aliases": [],
                    "definition_version": None,
                    "subject_file_id": None,
                    "entity_type": None,
                    "enrich_version": None,
                }
                records[file_id] = record
            cls._apply_metadata(record, row)
        return list(records.values())

    @classmethod
    def _fold_surface_rows(
        cls, rows: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        records: dict[int, dict[str, Any]] = {}
        for row in rows:
            file_id = int(row["kid"])
            record = records.get(file_id)
            if record is None:
                record = {
                    "kid": file_id,
                    "knowledge_base_id": row["knowledge_base_id"],
                    "name": row["name"],
                    "file_path": row["file_path"],
                    "updated_at": row["updated_at"],
                    "entity_name": None,
                    "aliases": [],
                    "subject_file_id": None,
                }
                records[file_id] = record
            cls._apply_metadata(record, row)
        return list(records.values())

    @classmethod
    def _apply_metadata(cls, record: dict[str, Any], row: Mapping[str, Any]) -> None:
        property_name = row.get("property_name")
        output_name = cls._OUTPUT_FIELD_BY_PROPERTY.get(property_name)
        if output_name is None or output_name not in record:
            return
        value = cls._metadata_value(row)
        if property_name in cls._LIST_METADATA_FIELDS:
            value = cls._normalize_string_list(value)
        if property_name == "documentKind":
            record["document_kind_configured"] = True
        if property_name == "processingCapabilities":
            record["processing_capabilities_configured"] = True
        record[output_name] = value

    @staticmethod
    def _metadata_value(row: Mapping[str, Any]) -> Any:
        value_type = row.get("value_type")
        if value_type == "string":
            return row.get("value_string")
        if value_type == "number":
            return row.get("value_number")
        if value_type == "boolean":
            return row.get("value_boolean")
        if value_type == "datetime":
            return row.get("value_datetime")
        if value_type == "stringList":
            return row.get("value_string_list")
        return None

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                return []
        if not isinstance(value, (list, tuple)):
            return []
        return [item for item in value if isinstance(item, str)]

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = path.strip()
        if not normalized:
            raise ValueError("path must not be empty")
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        if normalized != "/":
            normalized = normalized.rstrip("/")
        return normalized
