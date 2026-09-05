"""Integration tests for OpenGaussSaver compatibility behavior.

Includes regression coverage for schema-local migrations and exact blob-version
loading. The latter must be filtered by openGauss before ``fetchall()`` so a
long-lived thread does not load every historical blob into worker memory.
"""

# pylint: disable=protected-access,redefined-outer-name,wrong-import-position

from __future__ import annotations

import os
import uuid
from typing import Any, TypedDict
from unittest.mock import patch
from urllib.parse import quote

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("langgraph.checkpoint.postgres")

from psycopg import sql  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from by_qa.core.model_config import LLMModelProfile  # noqa: E402
from by_qa.qa.common.models import CoreInput, StreamEventType  # noqa: E402
from by_qa.qa.engines.fast.engine import FastQAEngine  # noqa: E402
from by_qa.qa.services.opengauss_checkpointer import (  # noqa: E402
    AsyncOpenGaussSaver,
    OpenGaussSaver,
)

pytestmark = pytest.mark.integration


DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = "15432"
DEFAULT_DB_DATABASE = "postgres"
DEFAULT_DB_USER = "gaussdb"
DEFAULT_DB_PASS = "OpenGauss#2026"
BLOB_VERSION_ROWS = [
    ("retrieval_results", "v1", b"retrieval-v1"),
    ("retrieval_results", "v2", b"retrieval-v2"),
    ("retrieval_results", "v3", b"wrong-cross-pair"),
    ("messages", "v1", b"messages-v1"),
    ("messages", "v2", b"wrong-cross-pair"),
    ("messages", "v3", b"messages-v3"),
]


def _base_dsn() -> str:
    host = os.getenv("DB_HOST", DEFAULT_DB_HOST)
    port = os.getenv("DB_PORT", DEFAULT_DB_PORT)
    database = os.getenv("DB_DATABASE", DEFAULT_DB_DATABASE)
    user = os.getenv("DB_USER", DEFAULT_DB_USER)
    password = quote(os.getenv("DB_PASS", DEFAULT_DB_PASS), safe="")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def _set_search_path(conn, schema: str) -> None:
    conn.execute(
        sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
    )


async def _set_search_path_async(conn, schema: str) -> None:
    await conn.execute(
        sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
    )


def _new_schema_name() -> str:
    return f"by_qa_ckpt_{uuid.uuid4().hex[:10]}"


def _create_schema(schema: str) -> None:
    with psycopg.connect(_base_dsn(), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
        )


def _drop_schema(schema: str) -> None:
    with psycopg.connect(_base_dsn(), autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )


def _column_exists(schema: str, table: str, column: str) -> bool:
    with psycopg.connect(_base_dsn(), autocommit=True, row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT 1 AS present
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
            LIMIT 1
            """,
            (schema, table, column),
        ).fetchone()
    return row is not None


@pytest.fixture
def two_fresh_schemas():
    schema_a = _new_schema_name()
    schema_b = _new_schema_name()
    _create_schema(schema_a)
    _create_schema(schema_b)
    try:
        yield schema_a, schema_b
    finally:
        _drop_schema(schema_a)
        _drop_schema(schema_b)


@pytest.fixture
def fresh_schema():
    schema = _new_schema_name()
    _create_schema(schema)
    try:
        yield schema
    finally:
        _drop_schema(schema)


def _insert_blob_versions(conn) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO checkpoint_blobs
                (thread_id, checkpoint_ns, channel, version, type, blob)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                ("thread-1", "ns-1", channel, version, "bytes", payload)
                for channel, version, payload in BLOB_VERSION_ROWS
            ],
        )


def _expected_blob_values() -> set[tuple[bytes, bytes, bytes]]:
    return {
        (b"retrieval_results", b"bytes", b"retrieval-v2"),
        (b"messages", b"bytes", b"messages-v3"),
    }


class _CheckpointState(TypedDict):
    payload: list[str]


class _FakeLLMService:
    """Return deterministic models while keeping the real Fast QA graph."""

    def __init__(self) -> None:
        self._models = {
            LLMModelProfile.LIGHTWEIGHT: FakeMessagesListChatModel(
                responses=[
                    AIMessage(content="rewritten round 1"),
                    AIMessage(content="rewritten round 2"),
                ]
            ),
            LLMModelProfile.STANDARD: FakeMessagesListChatModel(
                responses=[
                    AIMessage(content="answer round 1"),
                    AIMessage(content="answer round 2"),
                ]
            ),
        }

    async def _get_streaming_model(self, model_type):
        return self._models[model_type]


def test_sync_setup_in_two_schemas_each_has_task_path(two_fresh_schemas):
    """Setting up the saver in schema B after schema A must still add task_path to B."""
    schema_a, schema_b = two_fresh_schemas

    for schema in (schema_a, schema_b):
        with psycopg.connect(
            _base_dsn(),
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        ) as conn:
            _set_search_path(conn, schema)
            saver = OpenGaussSaver(conn)
            saver.setup()

    assert _column_exists(schema_a, "checkpoint_writes", "task_path"), (
        f"schema {schema_a} missing checkpoint_writes.task_path after setup()"
    )
    assert _column_exists(schema_b, "checkpoint_writes", "task_path"), (
        f"schema {schema_b} missing checkpoint_writes.task_path after setup() — "
        "schema-leakage regression in _task_path_exists_query"
    )


async def test_async_setup_in_two_schemas_each_has_task_path(two_fresh_schemas):
    schema_a, schema_b = two_fresh_schemas

    for schema in (schema_a, schema_b):
        conn = await psycopg.AsyncConnection.connect(
            _base_dsn(),
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        try:
            await _set_search_path_async(conn, schema)
            saver = AsyncOpenGaussSaver(conn)
            await saver.setup()
        finally:
            await conn.close()

    assert _column_exists(schema_a, "checkpoint_writes", "task_path")
    assert _column_exists(schema_b, "checkpoint_writes", "task_path")


def test_sync_fetch_blob_rows_filters_exact_versions_in_database(fresh_schema):
    with psycopg.connect(
        _base_dsn(),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    ) as conn:
        _set_search_path(conn, fresh_schema)
        saver = OpenGaussSaver(conn)
        saver.setup()
        _insert_blob_versions(conn)

        fetched_row_counts = []
        normalize = saver._normalize_blob_rows

        def record_fetched_rows(rows, channel_versions):
            fetched_row_counts.append(len(rows))
            return normalize(rows, channel_versions)

        saver._normalize_blob_rows = record_fetched_rows
        values = saver._fetch_blob_rows(
            "thread-1",
            "ns-1",
            {"retrieval_results": "v2", "messages": "v3"},
        )

    assert fetched_row_counts == [2]
    assert set(values) == _expected_blob_values()


async def test_async_fetch_blob_rows_filters_exact_versions_in_database(fresh_schema):
    conn = await psycopg.AsyncConnection.connect(
        _base_dsn(),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    try:
        await _set_search_path_async(conn, fresh_schema)
        saver = AsyncOpenGaussSaver(conn)
        await saver.setup()
        async with conn.cursor() as cur:
            await cur.executemany(
                """
                INSERT INTO checkpoint_blobs
                    (thread_id, checkpoint_ns, channel, version, type, blob)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        "thread-1",
                        "ns-1",
                        channel,
                        version,
                        "bytes",
                        payload,
                    )
                    for channel, version, payload in BLOB_VERSION_ROWS
                ],
            )

        fetched_row_counts = []
        normalize = saver._normalize_blob_rows

        def record_fetched_rows(rows, channel_versions):
            fetched_row_counts.append(len(rows))
            return normalize(rows, channel_versions)

        saver._normalize_blob_rows = record_fetched_rows
        values = await saver._fetch_blob_rows(
            "thread-1",
            "ns-1",
            {"retrieval_results": "v2", "messages": "v3"},
        )
    finally:
        await conn.close()

    assert fetched_row_counts == [2]
    assert set(values) == _expected_blob_values()


async def test_async_saver_restores_latest_blob_value_end_to_end(fresh_schema):
    conn = await psycopg.AsyncConnection.connect(
        _base_dsn(),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    try:
        await _set_search_path_async(conn, fresh_schema)
        saver = AsyncOpenGaussSaver(conn)
        await saver.setup()

        async def preserve_state(state: _CheckpointState) -> dict:
            del state
            return {}

        builder = StateGraph(_CheckpointState)
        builder.add_node("preserve", preserve_state)
        builder.add_edge(START, "preserve")
        builder.add_edge("preserve", END)
        graph = builder.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": "thread-1"}}

        for version in ("v1", "v2", "v3"):
            result = await graph.ainvoke({"payload": [version]}, config=config)
            assert result["payload"] == [version]

        checkpoint = await saver.aget_tuple(config)
        checkpoint_history = [item async for item in saver.alist(config)]
    finally:
        await conn.close()

    assert checkpoint is not None
    assert checkpoint.checkpoint["channel_values"]["payload"] == ["v3"]
    historical_payloads = {
        tuple(item.checkpoint["channel_values"]["payload"])
        for item in checkpoint_history
        if "payload" in item.checkpoint["channel_values"]
    }
    assert historical_payloads == {("v1",), ("v2",), ("v3",)}


async def test_fast_engine_runs_two_rounds_with_opengauss_checkpoint(fresh_schema):
    """Exercise the real Fast graph and restore one session on round two."""
    conn = await psycopg.AsyncConnection.connect(
        _base_dsn(),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    await _set_search_path_async(conn, fresh_schema)
    saver = AsyncOpenGaussSaver(conn)
    await saver.setup()

    session_id = f"fast-flow-{uuid.uuid4()}"
    thread_id = f"fast_qa_{session_id}"
    config = {"configurable": {"thread_id": thread_id}}
    engine = FastQAEngine(
        config={
            "llm_service": _FakeLLMService(),
            "retrieval": {
                "knowledge_bases": [
                    {
                        "kb_code": "test-kb",
                        "kb_name": "Test KB",
                        "service_name": "test-service",
                        "operations": {"knowledgeSearch": "/search"},
                    }
                ]
            },
        }
    )
    engine._checkpointer = saver

    async def fake_dispatch(
        dispatcher: Any,
        operation_type: Any,
        payload: dict[str, Any],
        runtime_context: Any,
    ) -> list[dict[str, Any]]:
        del dispatcher, operation_type, runtime_context
        return [
            {
                "chunk_id": payload["query"],
                "content": f"knowledge for {payload['query']}",
                "source": "fake://two-round-flow",
                "score": 1.0,
            }
        ]

    try:
        with patch(
            "by_qa.qa.engines.fast.nodes.retrieve.ServiceToolDispatcher.dispatch",
            new=fake_dispatch,
        ):
            for round_number in (1, 2):
                events = [
                    event
                    async for event in engine.stream_search(
                        CoreInput(
                            query=f"question round {round_number}",
                            session_id=session_id,
                            message_id=str(uuid.uuid4()),
                        )
                    )
                ]
                assert not [
                    event for event in events if event.type == StreamEventType.ERROR
                ]
                assert [
                    event.data["content"]
                    for event in events
                    if event.type == StreamEventType.ANSWER
                ] == [f"answer round {round_number}"]
                assert [
                    event.data["chunks"][0]["content"]
                    for event in events
                    if event.type == StreamEventType.SEARCH_RESULT_CHUNKS
                ] == [f"knowledge for rewritten round {round_number}"]

                checkpoint = await saver.aget_tuple(config)
                assert checkpoint is not None
                assert (
                    checkpoint.checkpoint["channel_values"]["final_answer"]
                    == f"answer round {round_number}"
                )
    finally:
        await saver.adelete_thread(thread_id)
        await engine.close()
