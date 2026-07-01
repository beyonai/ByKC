"""Single source of truth for metadata property value types.

Adding a new type means changing this module and the consumers below;
no SQL CHECK constraint to ALTER and no schema string list to keep in
sync.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

METADATA_VALUE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "string",
        "stringList",
        "number",
        "boolean",
        "datetime",
    }
)

VALUE_TYPE_TO_COLUMN: Final[dict[str, str]] = {
    "string": "value_string",
    "stringList": "value_string_list",
    "number": "value_number",
    "boolean": "value_boolean",
    "datetime": "value_datetime",
}


# Reserved system fields surfaced through the metadata DSL. These are
# stored on knowledge_fs_entry / derived from it, not in
# knowledge_file_metadata_value, so the DSL compiler must reference the
# main-table column directly instead of the EXISTS subquery used for
# user-defined properties.
#
# fileType has no dedicated column; it is the lowercase trailing
# extension of the file name. The expression here strips the leading
# dot so callers can compare against {"md", "pdf", ...} without needing
# to remember the dot.
SYSTEM_FIELD_TO_FE_EXPR: Final[dict[str, tuple[str, str]]] = {
    "fileName": ("fe.name", "string"),
    "fileType": (
        "lower("
        "CASE WHEN fe.name LIKE '%%.%%' "
        "THEN substring(fe.name FROM '[^.]+$') "
        "ELSE '' END"
        ")",
        "string",
    ),
    "fileSize": ("fe.file_size", "number"),
    "mimeType": ("fe.mime_type", "string"),
    "createdAt": ("fe.created_at", "datetime"),
    "updatedAt": ("fe.updated_at", "datetime"),
    "filePath": ("fe.virtual_path", "string"),
}


SYSTEM_FIELD_VALUE_TYPES: Final[dict[str, str]] = {
    name: value_type for name, (_, value_type) in SYSTEM_FIELD_TO_FE_EXPR.items()
}

SYSTEM_FIELD_DESCRIPTIONS: Final[dict[str, str]] = {
    "fileName": "File name",
    "fileType": "File extension",
    "fileSize": "File size in bytes",
    "mimeType": "MIME type",
    "createdAt": "Creation time",
    "updatedAt": "Last update time",
    "filePath": "Full file path within the knowledge base",
}


def infer_metadata_value_type(value: Any) -> str:
    """Infer the storage value type for free-form metadata values."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, datetime | date):
        return "datetime"
    if isinstance(value, list):
        return "stringList"
    return "string"


def normalize_metadata_value(value: Any, value_type: str) -> Any:
    """Normalize YAML values into shapes accepted by metadata value columns."""
    if value_type == "stringList":
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]
    if value_type == "string" and not isinstance(value, str):
        return str(value)
    return value
