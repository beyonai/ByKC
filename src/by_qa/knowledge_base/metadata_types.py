"""Single source of truth for metadata property value types.

Adding a new type means changing this module and the consumers below;
no SQL CHECK constraint to ALTER and no schema string list to keep in
sync.
"""

from __future__ import annotations

from typing import Final

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
}


SYSTEM_FIELD_VALUE_TYPES: Final[dict[str, str]] = {
    name: value_type for name, (_, value_type) in SYSTEM_FIELD_TO_FE_EXPR.items()
}
