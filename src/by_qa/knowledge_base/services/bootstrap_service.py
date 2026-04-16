"""Schema bootstrap helpers for knowledge base ingestion."""

import re
from pathlib import Path

from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError


def normalize_embedding_table_name(model_name: str) -> str:
    """Convert model names into a stable SQL table name."""
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", model_name.strip().lower()).strip("_")
    return f"chunk_embedding_{normalized}"


def split_sql_statements(script: str) -> list[str]:
    """Split a SQL script into top-level statements.

    This keeps semicolons inside quoted strings, comments, and dollar-quoted
    blocks intact so callers can store multiple DDL statements in one file.
    """
    statements: list[str] = []
    current: list[str] = []
    index = 0
    length = len(script)
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False
    dollar_tag: str | None = None

    while index < length:
        char = script[index]
        next_char = script[index + 1] if index + 1 < length else ""

        if in_line_comment:
            current.append(char)
            if char == "\n":
                in_line_comment = False
            index += 1
            continue

        if in_block_comment:
            current.append(char)
            if char == "*" and next_char == "/":
                current.append(next_char)
                index += 2
                in_block_comment = False
            else:
                index += 1
            continue

        if dollar_tag is not None:
            if script.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
            else:
                current.append(char)
                index += 1
            continue

        if in_single_quote:
            current.append(char)
            if char == "'" and next_char == "'":
                current.append(next_char)
                index += 2
                continue
            if char == "'":
                in_single_quote = False
            index += 1
            continue

        if in_double_quote:
            current.append(char)
            if char == '"' and next_char == '"':
                current.append(next_char)
                index += 2
                continue
            if char == '"':
                in_double_quote = False
            index += 1
            continue

        if char == "-" and next_char == "-":
            current.append(char)
            current.append(next_char)
            index += 2
            in_line_comment = True
            continue

        if char == "/" and next_char == "*":
            current.append(char)
            current.append(next_char)
            index += 2
            in_block_comment = True
            continue

        if char == "'":
            current.append(char)
            in_single_quote = True
            index += 1
            continue

        if char == '"':
            current.append(char)
            in_double_quote = True
            index += 1
            continue

        if char == "$":
            match = re.match(r"\$[A-Za-z0-9_]*\$", script[index:])
            if match:
                token = match.group(0)
                current.append(token)
                index += len(token)
                dollar_tag = token
                continue

        if char == ";":
            current.append(char)
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


class KnowledgeBaseSchemaBootstrapService:
    """Generate and apply knowledge base schema artifacts."""

    def __init__(
        self,
        *,
        embedding_model_name: str,
        embedding_dimension: int,
        sql_directory: Path | None = None,
    ):
        self.embedding_model_name = embedding_model_name
        self.embedding_dimension = embedding_dimension
        self.embedding_table_name = normalize_embedding_table_name(embedding_model_name)
        self.sql_directory = (
            sql_directory or Path(__file__).resolve().parents[1] / "sql"
        )

    def build_schema_statements(self) -> list[str]:
        """Return DDL statements required by the knowledge base schema."""
        statements: list[str] = []
        for sql_path in sorted(self.sql_directory.glob("*.sql")):
            content = sql_path.read_text(encoding="utf-8").strip()
            if content:
                statements.extend(split_sql_statements(content))
        for template_path in sorted(self.sql_directory.glob("*.sql.tpl")):
            content = template_path.read_text(encoding="utf-8").strip()
            if content:
                statements.extend(split_sql_statements(self._render_template(content)))
        return statements

    def apply(self, connection) -> None:
        """Apply knowledge base schema DDL using an open connection."""
        with connection.cursor() as cursor:
            self._prepare_extension_search_path(cursor)
            self._validate_embedding_table(cursor)
            for statement in self.build_schema_statements():
                cursor.execute(statement)
        connection.commit()

    def _render_template(self, template: str) -> str:
        """Render the small SQL template surface used by dynamic vector tables."""
        rendered = template.replace(
            "{{ embedding_table_name }}", self.embedding_table_name
        )
        rendered = rendered.replace(
            "{{ embedding_dimension }}",
            str(self.embedding_dimension),
        )
        return rendered

    def _validate_embedding_table(self, cursor) -> None:
        """Fail fast when an existing embedding table uses another vector dimension."""
        cursor.execute(
            """
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON a.attrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = current_schema()
              AND c.relname = %(table_name)s
              AND a.attname = 'embedding'
              AND a.attnum > 0
              AND NOT a.attisdropped
            """,
            {"table_name": self.embedding_table_name},
        )
        row = cursor.fetchone()
        if row is None:
            return

        existing_type = self._get_scalar_value(row, "format_type")
        expected_type = f"vector({self.embedding_dimension})"
        if existing_type == expected_type:
            return

        cursor.execute(
            f"SELECT count(*) FROM {self.embedding_table_name}",
        )
        row_count = self._get_scalar_value(cursor.fetchone(), "count")
        raise KnowledgeBaseConfigurationError(
            f"Embedding table {self.embedding_table_name} uses {existing_type}, "
            f"but EMBEDDING_DIMENSION={self.embedding_dimension} requires {expected_type}. "
            f"Existing rows: {row_count}. Run `make reset-kb-data` or migrate the table "
            "before starting the service."
        )

    def _prepare_extension_search_path(self, cursor) -> None:
        """Include existing extension schemas in this connection's search path."""
        cursor.execute("SELECT current_schema() AS current_schema")
        current_schema = self._get_scalar_value(cursor.fetchone(), "current_schema")

        cursor.execute(
            """
            SELECT n.nspname
            FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE e.extname IN ('ltree', 'pg_trgm')
            ORDER BY e.extname
            """
        )
        extension_schemas = [
            self._get_scalar_value(row, "nspname") for row in self._fetchall(cursor)
        ]

        schemas = self._dedupe_schema_names(
            [
                current_schema,
                *extension_schemas,
                "public",
            ]
        )
        if not schemas:
            return

        cursor.execute(
            "SELECT set_config('search_path', %(search_path)s, false)",
            {
                "search_path": ",".join(
                    self._format_search_path_schema(s) for s in schemas
                )
            },
        )

    @staticmethod
    def _dedupe_schema_names(schemas: list[str | None]) -> list[str]:
        """Return schema names without blanks or duplicates, preserving order."""
        seen: set[str] = set()
        result: list[str] = []
        for schema in schemas:
            if not schema:
                continue
            normalized = schema.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    @staticmethod
    def _format_search_path_schema(schema: str) -> str:
        """Quote schema names only when they are not safe unquoted identifiers."""
        if re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
            return schema
        return '"' + schema.replace('"', '""') + '"'

    @staticmethod
    def _get_scalar_value(row, key: str):
        """Read a single-column result from either tuple-like or mapping-like rows."""
        if row is None:
            return None
        if isinstance(row, dict):
            return row[key]
        return row[0]

    @staticmethod
    def _fetchall(cursor) -> list:
        fetchall = getattr(cursor, "fetchall", None)
        if not callable(fetchall):
            return []
        return list(fetchall())
