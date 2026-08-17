"""Schema bootstrap helpers for knowledge base ingestion."""

import asyncio
import hashlib
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from by_qa.core import logger
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError

_MIGRATION_LEDGER_FILE = "032_knowledge_schema_migration.sql"
_MIGRATION_LEDGER_TABLE = "knowledge_schema_migration"
_LEGACY_BASELINE_VERSION = 30
_SCHEMA_LOCK_NAMESPACE = "by-qa:knowledge-base-schema"


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    """One checksummed schema migration artifact."""

    version: str
    checksum: str
    statements: tuple[str, ...]
    numeric_version: int


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
        deadlock_max_attempts: int = 3,
        deadlock_retry_base_seconds: float = 0.1,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ):
        self.embedding_model_name = embedding_model_name
        self.embedding_dimension = embedding_dimension
        self.embedding_table_name = normalize_embedding_table_name(embedding_model_name)
        self.sql_directory = (
            sql_directory or Path(__file__).resolve().parents[1] / "sql"
        )
        self.deadlock_max_attempts = max(1, deadlock_max_attempts)
        self.deadlock_retry_base_seconds = max(0.0, deadlock_retry_base_seconds)
        self._sleep = sleep
        self._jitter = jitter

    def build_schema_statements(self) -> list[str]:
        """Return DDL statements required by the knowledge base schema."""
        return [
            statement
            for migration in self._load_migrations()
            for statement in migration.statements
        ]

    async def apply(self, connection) -> None:
        """Apply pending schema migrations once across concurrent instances."""
        migrations = self._load_migrations()
        ledger_migration = self._find_ledger_migration(migrations)
        lock_key: int | None = None
        try:
            async with connection.cursor() as cursor:
                await self._prepare_extension_search_path(cursor)
                lock_key = await self._schema_lock_key(cursor)
                logger.info(
                    "knowledge base schema migration lock waiting: lock_key=%s",
                    lock_key,
                )
                await cursor.execute(
                    "SELECT pg_advisory_lock(%(lock_key)s::bigint)",
                    {"lock_key": lock_key},
                )
            # Session advisory locks survive commit. Clear the transaction used
            # to discover the schema and acquire the lock before migration work.
            await connection.commit()
            logger.info(
                "knowledge base schema migration lock acquired: lock_key=%s",
                lock_key,
            )

            await self._ensure_migration_ledger(connection, ledger_migration)
            await self._baseline_legacy_schema(connection, migrations)
            applied = await self._load_applied_migrations(connection)
            self._validate_migration_checksums(migrations, applied)

            async with connection.cursor() as cursor:
                await self._validate_embedding_table(cursor)
            await connection.commit()

            for migration in migrations:
                if migration.version in applied:
                    continue
                await self._apply_migration_with_retry(connection, migration)
                applied[migration.version] = migration.checksum
        finally:
            if lock_key is not None:
                await self._release_schema_lock(connection, lock_key)

    def _load_migrations(self) -> list[SchemaMigration]:
        """Load static and rendered-template migrations in numeric file order."""
        paths = sorted(
            [
                *self.sql_directory.glob("*.sql"),
                *self.sql_directory.glob("*.sql.tpl"),
            ],
            key=lambda path: path.name,
        )
        migrations: list[SchemaMigration] = []
        for path in paths:
            match = re.match(r"^(\d+)_", path.name)
            if match is None:
                raise KnowledgeBaseConfigurationError(
                    f"Schema migration file must start with a numeric version: {path.name}"
                )
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                continue
            if path.name.endswith(".sql.tpl"):
                content = self._render_template(content)
                version = f"{path.name}:{self.embedding_table_name}"
            else:
                version = path.name
            migrations.append(
                SchemaMigration(
                    version=version,
                    checksum=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    statements=tuple(split_sql_statements(content)),
                    numeric_version=int(match.group(1)),
                )
            )
        return migrations

    @staticmethod
    def _find_ledger_migration(
        migrations: list[SchemaMigration],
    ) -> SchemaMigration:
        for migration in migrations:
            if migration.version == _MIGRATION_LEDGER_FILE:
                return migration
        raise KnowledgeBaseConfigurationError(
            f"Required schema migration is missing: {_MIGRATION_LEDGER_FILE}"
        )

    async def _schema_lock_key(self, cursor) -> int:
        """Derive a stable signed bigint lock key for the current DB and schema."""
        await cursor.execute(
            "SELECT current_database() AS database_name, "
            "current_schema() AS schema_name"
        )
        row = await cursor.fetchone()
        database_name = self._get_row_value(row, "database_name", 0)
        schema_name = self._get_row_value(row, "schema_name", 1)
        lock_identity = f"{_SCHEMA_LOCK_NAMESPACE}:{database_name}:{schema_name}"
        digest = hashlib.sha256(lock_identity.encode("utf-8")).digest()[:8]
        return int.from_bytes(digest, byteorder="big", signed=True)

    async def _ensure_migration_ledger(
        self,
        connection,
        ledger_migration: SchemaMigration,
    ) -> None:
        """Create the ledger from its additive SQL artifact before inspection."""
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'knowledge_schema_migration'
                    ) AS ledger_exists
                    """
                )
                ledger_exists = bool(
                    self._get_scalar_value(await cursor.fetchone(), "ledger_exists")
                )
                if ledger_exists:
                    await cursor.execute(
                        f"""
                        SELECT checksum FROM {_MIGRATION_LEDGER_TABLE}
                        WHERE version = %(version)s
                        """,
                        {"version": ledger_migration.version},
                    )
                    if await cursor.fetchone() is None:
                        await self._insert_migration_record(cursor, ledger_migration)
                    await connection.commit()
                    return
                for statement in ledger_migration.statements:
                    await cursor.execute(statement)
                await self._insert_migration_record(cursor, ledger_migration)
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise

    async def _baseline_legacy_schema(
        self,
        connection,
        migrations: list[SchemaMigration],
    ) -> None:
        """Record 000-030 for a ledger-less database already complete at 030."""
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT count(*) FROM {_MIGRATION_LEDGER_TABLE}
                WHERE version <> %(ledger_version)s
                """,
                {"ledger_version": _MIGRATION_LEDGER_FILE},
            )
            applied_count = self._get_scalar_value(await cursor.fetchone(), "count")
            if int(applied_count or 0) > 0:
                await connection.commit()
                return

            await cursor.execute(
                """
                SELECT
                    EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'knowledge_semantic_processing_task'
                    )
                    AND EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'knowledge_file_reference'
                          AND column_name = 'target_locator_type'
                          AND is_nullable = 'NO'
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'knowledge_file_reference'
                          AND column_name = 'reference_type'
                    )
                    AND EXISTS (
                        SELECT 1 FROM pg_indexes
                        WHERE schemaname = current_schema()
                          AND indexname = 'idx_kfr_source_task'
                    ) AS legacy_complete,
                    EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = %(embedding_table_name)s
                    ) AS embedding_table_exists
                """,
                {"embedding_table_name": self.embedding_table_name},
            )
            row = await cursor.fetchone()
            legacy_complete = bool(self._get_scalar_value(row, "legacy_complete"))
            if not legacy_complete:
                await connection.commit()
                return

            embedding_table_exists = bool(
                self._get_row_value(row, "embedding_table_exists", 1)
            )
            baseline = [
                migration
                for migration in migrations
                if migration.numeric_version <= _LEGACY_BASELINE_VERSION
                and (".sql.tpl:" not in migration.version or embedding_table_exists)
            ]
            for migration in baseline:
                await self._insert_migration_record(cursor, migration)
        await connection.commit()
        logger.info(
            "knowledge base legacy schema baselined: through_version=%s, migration_count=%s",
            _LEGACY_BASELINE_VERSION,
            len(baseline),
        )

    async def _load_applied_migrations(self, connection) -> dict[str, str]:
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"SELECT version, checksum FROM {_MIGRATION_LEDGER_TABLE}"
            )
            rows = await cursor.fetchall()
        await connection.commit()
        return {
            str(self._get_row_value(row, "version", 0)): str(
                self._get_row_value(row, "checksum", 1)
            )
            for row in rows
        }

    @staticmethod
    def _validate_migration_checksums(
        migrations: list[SchemaMigration],
        applied: dict[str, str],
    ) -> None:
        for migration in migrations:
            existing_checksum = applied.get(migration.version)
            if existing_checksum is None or existing_checksum == migration.checksum:
                continue
            raise KnowledgeBaseConfigurationError(
                "Schema migration checksum drift detected for "
                f"{migration.version}: database={existing_checksum}, "
                f"package={migration.checksum}. Add a new incremental SQL migration "
                "instead of editing an applied migration."
            )

    async def _apply_migration_with_retry(
        self,
        connection,
        migration: SchemaMigration,
    ) -> None:
        for attempt in range(1, self.deadlock_max_attempts + 1):
            try:
                async with connection.cursor() as cursor:
                    for statement in migration.statements:
                        await cursor.execute(statement)
                    await self._insert_migration_record(cursor, migration)
                await connection.commit()
                logger.info(
                    "knowledge base schema migration applied: version=%s, attempt=%s",
                    migration.version,
                    attempt,
                )
                return
            except Exception as exc:
                await connection.rollback()
                if not self._is_deadlock(exc) or attempt >= self.deadlock_max_attempts:
                    raise
                upper = self.deadlock_retry_base_seconds * (2 ** (attempt - 1))
                delay = self._jitter(0.0, upper) if upper > 0 else 0.0
                logger.warning(
                    "knowledge base schema migration deadlock; retrying: "
                    "version=%s, attempt=%s, max_attempts=%s, delay_seconds=%.3f",
                    migration.version,
                    attempt,
                    self.deadlock_max_attempts,
                    delay,
                )
                await self._sleep(delay)

    @staticmethod
    async def _insert_migration_record(cursor, migration: SchemaMigration) -> None:
        await cursor.execute(
            f"""
            INSERT INTO {_MIGRATION_LEDGER_TABLE} (version, checksum)
            VALUES (%(version)s, %(checksum)s)
            """,
            {
                "version": migration.version,
                "checksum": migration.checksum,
            },
        )

    async def _release_schema_lock(self, connection, lock_key: int) -> None:
        """Release the session lock even after a failed migration transaction."""
        try:
            await connection.rollback()
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT pg_advisory_unlock(%(lock_key)s::bigint)",
                    {"lock_key": lock_key},
                )
            await connection.commit()
            logger.info(
                "knowledge base schema migration lock released: lock_key=%s",
                lock_key,
            )
        except Exception:
            logger.exception(
                "knowledge base schema migration lock release failed: lock_key=%s",
                lock_key,
            )

    @staticmethod
    def _is_deadlock(exc: BaseException) -> bool:
        current: BaseException | None = exc
        while current is not None:
            if getattr(current, "sqlstate", None) == "40P01":
                return True
            current = current.__cause__ or current.__context__
        return False

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

    async def _validate_embedding_table(self, cursor) -> None:
        """Fail fast when an existing embedding table uses another vector dimension."""
        await cursor.execute(
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
        row = await cursor.fetchone()
        if row is None:
            return

        existing_type = self._get_scalar_value(row, "format_type")
        expected_type = f"vector({self.embedding_dimension})"
        if existing_type == expected_type:
            return

        await cursor.execute(
            f"SELECT count(*) FROM {self.embedding_table_name}",
        )
        row_count = self._get_scalar_value(await cursor.fetchone(), "count")
        raise KnowledgeBaseConfigurationError(
            f"Embedding table {self.embedding_table_name} uses {existing_type}, "
            f"but EMBEDDING_DIMENSION={self.embedding_dimension} requires {expected_type}. "
            f"Existing rows: {row_count}. Run `make reset-kb-data` or migrate the table "
            "before starting the service."
        )

    async def _prepare_extension_search_path(self, cursor) -> None:
        """Include existing extension schemas in this connection's search path."""
        await cursor.execute("SELECT current_schema() AS current_schema")
        current_schema = self._get_scalar_value(
            await cursor.fetchone(), "current_schema"
        )

        await cursor.execute(
            """
            SELECT n.nspname
            FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE e.extname IN ('ltree', 'pg_trgm')
            ORDER BY e.extname
            """
        )
        extension_schemas = [
            self._get_scalar_value(row, "nspname") for row in await cursor.fetchall()
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

        await cursor.execute(
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
    def _get_row_value(row, key: str, index: int):
        if isinstance(row, dict):
            return row[key]
        return row[index]
