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
