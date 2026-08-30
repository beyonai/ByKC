"""Single source of truth for metadata property value types.

Adding a new type means changing this module and the consumers below;
no SQL CHECK constraint to ALTER and no schema string list to keep in
sync.
"""

from __future__ import annotations

import re
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

_ISO_DATE_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:\d{2})?$"
)


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
    "fileSize": ("COALESCE(fe.file_size, 0)", "number"),
    "mimeType": ("fe.mime_type", "string"),
    "createdAt": ("fe.created_at", "datetime"),
    "updatedAt": ("fe.updated_at", "datetime"),
    "fileSignature": ("fe.checksum", "string"),
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
    "fileSignature": "File checksum",
    "filePath": "Full file path within the knowledge base",
}


SYSTEM_FIELD_TO_ENTRY_KEY: Final[dict[str, str]] = {
    "fileName": "name",
    "fileSize": "file_size",
    "mimeType": "mime_type",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "fileSignature": "checksum",
    "filePath": "virtual_path",
}


def extract_system_metadata(
    entry: dict[str, Any],
    property_names: list[str] | None,
) -> dict[str, dict[str, Any]]:
    """Format requested knowledge_fs_entry columns as metadata values."""
    requested = (
        set(property_names)
        if property_names is not None
        else set(SYSTEM_FIELD_VALUE_TYPES)
    )
    metadata: dict[str, dict[str, Any]] = {}
    for name, value_type in SYSTEM_FIELD_VALUE_TYPES.items():
        if name not in requested:
            continue
        if name == "fileType":
            file_name = str(entry.get("name") or "")
            value = file_name.rsplit(".", 1)[1].lower() if "." in file_name else ""
        else:
            value = entry.get(SYSTEM_FIELD_TO_ENTRY_KEY[name])
            if name == "fileSize" and value is None:
                value = 0
        if value_type == "datetime" and hasattr(value, "isoformat"):
            value = value.isoformat()
        metadata[name] = {"valueType": value_type, "value": value}
    return metadata


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


def prepare_front_matter_metadata_value(value: Any) -> tuple[str, Any]:
    """Infer and normalize a YAML value, including quoted ISO timestamps."""
    parsed_datetime = (
        _parse_iso_datetime_string(value) if isinstance(value, str) else None
    )
    if parsed_datetime is not None:
        return "datetime", parsed_datetime

    value_type = infer_metadata_value_type(value)
    return value_type, normalize_metadata_value(value, value_type)


def _parse_iso_datetime_string(value: str) -> date | datetime | None:
    """Parse strict ISO date/time strings without coercing date-like free text."""
    try:
        if _ISO_DATE_RE.fullmatch(value):
            return date.fromisoformat(value)
        if _ISO_DATETIME_RE.fullmatch(value):
            normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
            return datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return None
