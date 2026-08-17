"""Canonical metadata defaults shared by document write paths."""

from __future__ import annotations

from typing import Any

from by_qa.core import logger

DOCUMENT_KIND_PROPERTY = "documentKind"
ORIGINAL_DOCUMENT_KIND = "original"
KNOWLEDGE_ENTITY_DOCUMENT_KIND = "knowledgeEntity"
KNOWLEDGE_ENTITY_DIRECTORY = "/KnowledgeEntity"


def default_document_kind_for_path(file_path: str) -> str:
    """Classify a file by the reserved KnowledgeEntity directory boundary."""
    normalized_path = "/" + file_path.strip("/")
    if normalized_path == KNOWLEDGE_ENTITY_DIRECTORY or normalized_path.startswith(
        f"{KNOWLEDGE_ENTITY_DIRECTORY}/"
    ):
        return KNOWLEDGE_ENTITY_DOCUMENT_KIND
    return ORIGINAL_DOCUMENT_KIND


async def ensure_document_kind_metadata(
    cursor: Any,
    *,
    file_metadata_value_repository: Any | None,
    fs_entry_id: int,
    knowledge_base_id: int,
    file_path: str,
) -> bool:
    """Persist a document kind only when the file has no active explicit value.

    ``processingCapabilities`` is deliberately not materialized here. Its absence
    means that the processing service applies the default for the document kind,
    while an explicit empty list disables processing.
    """
    if file_metadata_value_repository is None:
        logger.debug(
            "document kind metadata skipped: reason=repository_unavailable kb_id=%s source_id=%s file_path=%s",
            knowledge_base_id,
            fs_entry_id,
            file_path,
        )
        return False

    rows = await file_metadata_value_repository.get_file_metadata(
        cursor,
        fs_entry_id=fs_entry_id,
        property_names=[DOCUMENT_KIND_PROPERTY],
    )
    if any(row.get("property_name") == DOCUMENT_KIND_PROPERTY for row in rows):
        logger.debug(
            "document kind metadata preserved: kb_id=%s source_id=%s file_path=%s",
            knowledge_base_id,
            fs_entry_id,
            file_path,
        )
        return False

    document_kind = default_document_kind_for_path(file_path)
    await file_metadata_value_repository.upsert_value(
        cursor,
        fs_entry_id=fs_entry_id,
        knowledge_base_id=knowledge_base_id,
        property_name=DOCUMENT_KIND_PROPERTY,
        value_type="string",
        value=document_kind,
    )
    logger.info(
        "document kind metadata initialized: kb_id=%s source_id=%s file_path=%s document_kind=%s",
        knowledge_base_id,
        fs_entry_id,
        file_path,
        document_kind,
    )
    return True
