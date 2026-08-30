"""Shared normalization and persistence for entry metadata mappings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from by_qa.knowledge_base.metadata_types import (
    SYSTEM_FIELD_VALUE_TYPES,
    prepare_front_matter_metadata_value,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError


def merge_entry_metadata(
    request_metadata: Mapping[str, Any] | None,
    front_matter: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge metadata sources with Markdown front matter taking precedence."""
    return {**dict(request_metadata or {}), **dict(front_matter or {})}


async def upsert_entry_metadata(
    cursor: Any,
    *,
    metadata_repository: Any | None,
    fs_entry_id: int,
    knowledge_base_id: int,
    metadata: Mapping[str, Any],
) -> None:
    """Infer existing metadata value types and upsert one entry atomically."""
    normalized_metadata = {str(name): value for name, value in metadata.items()}
    for name in normalized_metadata:
        if name in SYSTEM_FIELD_VALUE_TYPES:
            raise KnowledgeBaseValidationError(f"metadata field is read-only: {name}")
    if metadata_repository is None:
        return
    for name, value in normalized_metadata.items():
        value_type, normalized_value = prepare_front_matter_metadata_value(value)
        await metadata_repository.upsert_value(
            cursor,
            fs_entry_id=fs_entry_id,
            knowledge_base_id=knowledge_base_id,
            property_name=name,
            value_type=value_type,
            value=normalized_value,
        )
