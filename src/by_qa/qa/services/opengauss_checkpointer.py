"""openGauss-compatible LangGraph checkpoint savers."""

# pylint: disable=redefined-builtin

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import CheckpointTuple, get_checkpoint_id
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.postgres.base import BasePostgresSaver
from langgraph.checkpoint.serde.types import TASKS
from psycopg.rows import DictRow


def _opengauss_migrations() -> list[str]:
    """Build openGauss-compatible migrations from the PostgreSQL defaults."""
    migrations = list(BasePostgresSaver.MIGRATIONS)
    migrations[-1] = (
        "ALTER TABLE checkpoint_writes ADD COLUMN task_path TEXT NOT NULL DEFAULT '';"
    )
    return migrations


class _OpenGaussMixin:
    """Shared SQL compatibility layer for openGauss."""

    MIGRATIONS = _opengauss_migrations()
    UPSERT_CHECKPOINT_BLOBS_SQL = """
        INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE NOTHING
    """
    UPSERT_CHECKPOINTS_SQL = """
        INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            checkpoint = EXCLUDED.checkpoint,
            metadata = EXCLUDED.metadata
    """
    UPSERT_CHECKPOINT_WRITES_SQL = """
        INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, blob)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            channel = EXCLUDED.channel,
            type = EXCLUDED.type,
            blob = EXCLUDED.blob
    """
    INSERT_CHECKPOINT_WRITES_SQL = """
        INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, blob)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE NOTHING
    """

    @staticmethod
    def _task_path_exists_query() -> str:
        return """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = CURRENT_SCHEMA()
              AND table_name = 'checkpoint_writes'
              AND column_name = 'task_path'
            LIMIT 1
        """

    def _normalize_blob_rows(
        self,
        rows: list[dict[str, Any]],
        channel_versions: dict[str, Any],
    ) -> list[tuple[bytes, bytes, bytes]]:
        expected_versions = {key: str(value) for key, value in channel_versions.items()}
        values: list[tuple[bytes, bytes, bytes]] = []
        for row in rows:
            channel = row["channel"]
            if expected_versions.get(channel) != row["version"]:
                continue
            values.append((channel.encode(), row["type"].encode(), row["blob"]))
        return values

    def _normalize_write_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> list[tuple[bytes, bytes, bytes, bytes]]:
        return [
            (
                row["task_id"].encode(),
                row["channel"].encode(),
                row["type"].encode(),
                row["blob"],
            )
            for row in rows
        ]

    def _normalize_pending_send_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> list[tuple[bytes, bytes]]:
        return [(row["type"].encode(), row["blob"]) for row in rows]


class OpenGaussSaver(_OpenGaussMixin, PostgresSaver):
    """Synchronous LangGraph saver compatible with openGauss."""

    def setup(self) -> None:
        with self._cursor() as cur:
            cur.execute(self.MIGRATIONS[0])
            results = cur.execute(
                "SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1"
            )
            row = results.fetchone()
            version = -1 if row is None else row["v"]
            for v, migration in zip(
                range(version + 1, len(self.MIGRATIONS)),
                self.MIGRATIONS[version + 1 :],
                strict=False,
            ):
                if v == len(self.MIGRATIONS) - 1:
                    exists = cur.execute(self._task_path_exists_query()).fetchone()
                    if exists is None:
                        cur.execute(migration)
                else:
                    cur.execute(migration)
                cur.execute("INSERT INTO checkpoint_migrations (v) VALUES (%s)", (v,))
        if self.pipe:
            self.pipe.sync()

    def _fetch_blob_rows(
        self, thread_id: str, checkpoint_ns: str, channel_versions: dict[str, Any]
    ) -> list[tuple[bytes, bytes, bytes]]:
        if not channel_versions:
            return []

        with self._cursor() as cur:
            cur.execute(
                """
                SELECT channel, version, type, blob
                FROM checkpoint_blobs
                WHERE thread_id = %s
                  AND checkpoint_ns = %s
                  AND channel = ANY(%s)
                """,
                (thread_id, checkpoint_ns, list(channel_versions.keys())),
            )
            rows = cur.fetchall()
        return self._normalize_blob_rows(rows, channel_versions)

    def _fetch_write_rows(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> list[tuple[bytes, bytes, bytes, bytes]]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT task_id, channel, type, blob
                FROM checkpoint_writes
                WHERE thread_id = %s
                  AND checkpoint_ns = %s
                  AND checkpoint_id = %s
                ORDER BY task_id, idx
                """,
                (thread_id, checkpoint_ns, checkpoint_id),
            )
            rows = cur.fetchall()
        return self._normalize_write_rows(rows)

    def _fetch_pending_sends(
        self, thread_id: str, parent_checkpoint_id: str
    ) -> list[tuple[bytes, bytes]]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT type, blob
                FROM checkpoint_writes
                WHERE thread_id = %s
                  AND checkpoint_id = %s
                  AND channel = %s
                ORDER BY task_path, task_id, idx
                """,
                (thread_id, parent_checkpoint_id, TASKS),
            )
            rows = cur.fetchall()
        return self._normalize_pending_send_rows(rows)

    def _hydrate_checkpoint_row(self, value: DictRow) -> CheckpointTuple:
        channel_values = self._fetch_blob_rows(
            value["thread_id"],
            value["checkpoint_ns"],
            value["checkpoint"].get("channel_versions", {}),
        )
        pending_writes = self._fetch_write_rows(
            value["thread_id"],
            value["checkpoint_ns"],
            value["checkpoint_id"],
        )

        if value["checkpoint"]["v"] < 4 and value["parent_checkpoint_id"]:
            pending_sends = self._fetch_pending_sends(
                value["thread_id"], value["parent_checkpoint_id"]
            )
            self._migrate_pending_sends(
                pending_sends,
                value["checkpoint"],
                channel_values,
            )

        value = dict(value)
        value["channel_values"] = channel_values
        value["pending_writes"] = pending_writes
        return self._load_checkpoint_tuple(value)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        where, args = self._search_where(config, filter, before)
        query = (
            """
            SELECT
                thread_id,
                checkpoint,
                checkpoint_ns,
                checkpoint_id,
                parent_checkpoint_id,
                metadata
            FROM checkpoints
        """
            + where
            + " ORDER BY checkpoint_id DESC"
        )
        params = list(args)
        if limit is not None:
            query += " LIMIT %s"
            params.append(int(limit))

        with self._cursor() as cur:
            cur.execute(query, params)
            values = cur.fetchall()

        for value in values:
            yield self._hydrate_checkpoint_row(value)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = get_checkpoint_id(config)
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        if checkpoint_id:
            args: tuple[Any, ...] = (thread_id, checkpoint_ns, checkpoint_id)
            where = "WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s"
        else:
            args = (thread_id, checkpoint_ns)
            where = "WHERE thread_id = %s AND checkpoint_ns = %s ORDER BY checkpoint_id DESC LIMIT 1"

        query = (
            """
            SELECT
                thread_id,
                checkpoint,
                checkpoint_ns,
                checkpoint_id,
                parent_checkpoint_id,
                metadata
            FROM checkpoints
        """
            + where
        )
        with self._cursor() as cur:
            cur.execute(query, args)
            value = cur.fetchone()

        if value is None:
            return None
        return self._hydrate_checkpoint_row(value)


class AsyncOpenGaussSaver(_OpenGaussMixin, AsyncPostgresSaver):
    """Asynchronous LangGraph saver compatible with openGauss."""

    async def setup(self) -> None:
        async with self._cursor() as cur:
            await cur.execute(self.MIGRATIONS[0])
            results = await cur.execute(
                "SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1"
            )
            row = await results.fetchone()
            version = -1 if row is None else row["v"]
            for v, migration in zip(
                range(version + 1, len(self.MIGRATIONS)),
                self.MIGRATIONS[version + 1 :],
                strict=False,
            ):
                if v == len(self.MIGRATIONS) - 1:
                    exists_results = await cur.execute(self._task_path_exists_query())
                    exists = await exists_results.fetchone()
                    if exists is None:
                        await cur.execute(migration)
                else:
                    await cur.execute(migration)
                await cur.execute(
                    "INSERT INTO checkpoint_migrations (v) VALUES (%s)", (v,)
                )
        if self.pipe:
            await self.pipe.sync()

    async def _fetch_blob_rows(
        self, thread_id: str, checkpoint_ns: str, channel_versions: dict[str, Any]
    ) -> list[tuple[bytes, bytes, bytes]]:
        if not channel_versions:
            return []

        async with self._cursor() as cur:
            await cur.execute(
                """
                SELECT channel, version, type, blob
                FROM checkpoint_blobs
                WHERE thread_id = %s
                  AND checkpoint_ns = %s
                  AND channel = ANY(%s)
                """,
                (thread_id, checkpoint_ns, list(channel_versions.keys())),
            )
            rows = await cur.fetchall()
        return self._normalize_blob_rows(rows, channel_versions)

    async def _fetch_write_rows(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> list[tuple[bytes, bytes, bytes, bytes]]:
        async with self._cursor() as cur:
            await cur.execute(
                """
                SELECT task_id, channel, type, blob
                FROM checkpoint_writes
                WHERE thread_id = %s
                  AND checkpoint_ns = %s
                  AND checkpoint_id = %s
                ORDER BY task_id, idx
                """,
                (thread_id, checkpoint_ns, checkpoint_id),
            )
            rows = await cur.fetchall()
        return self._normalize_write_rows(rows)

    async def _fetch_pending_sends(
        self, thread_id: str, parent_checkpoint_id: str
    ) -> list[tuple[bytes, bytes]]:
        async with self._cursor() as cur:
            await cur.execute(
                """
                SELECT type, blob
                FROM checkpoint_writes
                WHERE thread_id = %s
                  AND checkpoint_id = %s
                  AND channel = %s
                ORDER BY task_path, task_id, idx
                """,
                (thread_id, parent_checkpoint_id, TASKS),
            )
            rows = await cur.fetchall()
        return self._normalize_pending_send_rows(rows)

    async def _hydrate_checkpoint_row(self, value: DictRow) -> CheckpointTuple:
        channel_values = await self._fetch_blob_rows(
            value["thread_id"],
            value["checkpoint_ns"],
            value["checkpoint"].get("channel_versions", {}),
        )
        pending_writes = await self._fetch_write_rows(
            value["thread_id"],
            value["checkpoint_ns"],
            value["checkpoint_id"],
        )

        if value["checkpoint"]["v"] < 4 and value["parent_checkpoint_id"]:
            pending_sends = await self._fetch_pending_sends(
                value["thread_id"], value["parent_checkpoint_id"]
            )
            self._migrate_pending_sends(
                pending_sends,
                value["checkpoint"],
                channel_values,
            )

        payload = dict(value)
        payload["channel_values"] = channel_values
        payload["pending_writes"] = pending_writes
        return await self._load_checkpoint_tuple(payload)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        where, args = self._search_where(config, filter, before)
        query = (
            """
            SELECT
                thread_id,
                checkpoint,
                checkpoint_ns,
                checkpoint_id,
                parent_checkpoint_id,
                metadata
            FROM checkpoints
        """
            + where
            + " ORDER BY checkpoint_id DESC"
        )
        params = list(args)
        if limit is not None:
            query += " LIMIT %s"
            params.append(int(limit))

        async with self._cursor() as cur:
            await cur.execute(query, params)
            values = await cur.fetchall()

        for value in values:
            yield await self._hydrate_checkpoint_row(value)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = get_checkpoint_id(config)
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        if checkpoint_id:
            args: tuple[Any, ...] = (thread_id, checkpoint_ns, checkpoint_id)
            where = "WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s"
        else:
            args = (thread_id, checkpoint_ns)
            where = "WHERE thread_id = %s AND checkpoint_ns = %s ORDER BY checkpoint_id DESC LIMIT 1"

        query = (
            """
            SELECT
                thread_id,
                checkpoint,
                checkpoint_ns,
                checkpoint_id,
                parent_checkpoint_id,
                metadata
            FROM checkpoints
        """
            + where
        )
        async with self._cursor() as cur:
            await cur.execute(query, args)
            value = await cur.fetchone()

        if value is None:
            return None
        return await self._hydrate_checkpoint_row(value)


__all__ = ["OpenGaussSaver", "AsyncOpenGaussSaver"]
