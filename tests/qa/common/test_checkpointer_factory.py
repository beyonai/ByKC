"""Tests for QA checkpointer factory helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from by_qa.qa.services.checkpointer_factory import (
    _create_async_opengauss_saver,
    _create_sync_opengauss_saver,
    create_checkpointer,
    create_checkpointer_async,
    get_checkpointer_backend_name,
)


def _mock_settings():
    settings = type("Settings", (), {})()
    settings.checkpointer_backend = "memory"
    settings.checkpointer_sqlite_path = "/tmp/qa-checkpoints.db"
    settings.build_opengauss_dsn = MagicMock(
        return_value="postgresql://user:pass@localhost:5432/test"
    )
    return settings


def test_create_checkpointer_uses_sqlite_saver_for_sqlite_backend():
    settings = _mock_settings()
    fake_connection = object()
    fake_saver = MagicMock()

    with (
        patch("by_qa.qa.services.checkpointer_factory.SqliteSaver") as sqlite_saver,
        patch("sqlite3.connect", return_value=fake_connection) as connect,
    ):
        sqlite_saver.return_value = fake_saver

        saver = create_checkpointer(settings=settings, backend="sqlite")

    connect.assert_called_once_with(
        settings.checkpointer_sqlite_path, check_same_thread=False
    )
    sqlite_saver.assert_called_once_with(fake_connection)
    fake_saver.setup.assert_called_once_with()
    assert saver is fake_saver


@pytest.mark.asyncio
async def test_create_checkpointer_async_uses_opengauss_factory():
    settings = _mock_settings()
    fake_saver = MagicMock()
    fake_saver.setup = AsyncMock()

    with patch(
        "by_qa.qa.services.checkpointer_factory._create_async_opengauss_saver",
        new=AsyncMock(return_value=fake_saver),
    ) as factory:
        saver = await create_checkpointer_async(settings=settings, backend="opengauss")

    factory.assert_awaited_once_with(settings, settings.build_opengauss_dsn())
    fake_saver.setup.assert_awaited_once_with()
    assert saver is fake_saver


def test_create_checkpointer_uses_opengauss_saver_and_runs_setup():
    settings = _mock_settings()
    fake_saver = MagicMock()

    with patch(
        "by_qa.qa.services.checkpointer_factory._create_sync_opengauss_saver",
        return_value=fake_saver,
    ) as factory:
        saver = create_checkpointer(settings=settings, backend="opengauss")

    factory.assert_called_once_with(settings, settings.build_opengauss_dsn())
    fake_saver.setup.assert_called_once_with()
    assert saver is fake_saver


def test_create_checkpointer_derives_opengauss_dsn_from_db_parts():
    settings = _mock_settings()
    settings.db_host = "10.10.168.204"
    settings.db_port = 5432
    settings.db_schema = "byai"
    settings.db_user = "gaussdb"
    settings.db_pass = "Admin@123"
    settings.build_opengauss_dsn = MagicMock(
        return_value=(
            "postgresql://gaussdb:Admin%40123@10.10.168.204:5432/postgres"
            "?options=-c%20search_path%3Dbyai%2Cpublic"
        )
    )
    fake_saver = MagicMock()

    with patch(
        "by_qa.qa.services.checkpointer_factory._create_sync_opengauss_saver",
        return_value=fake_saver,
    ) as factory:
        saver = create_checkpointer(settings=settings, backend="opengauss")

    settings.build_opengauss_dsn.assert_called_once_with()
    factory.assert_called_once_with(
        settings,
        "postgresql://gaussdb:Admin%40123@10.10.168.204:5432/postgres?options=-c%20search_path%3Dbyai%2Cpublic",
    )
    fake_saver.setup.assert_called_once_with()
    assert saver is fake_saver


def test_sync_opengauss_saver_creates_configured_schema(monkeypatch):
    settings = _mock_settings()
    settings.db_schema = "byai"
    settings.build_opengauss_dsn = MagicMock(
        return_value="postgresql://gaussdb:secret@localhost:5432/postgres"
    )
    calls = []
    fake_saver = MagicMock()

    class FakeConnection:
        def execute(self, statement):
            calls.append(statement.as_string(None))

    monkeypatch.setattr(
        "by_qa.qa.services.checkpointer_factory.PostgresSaver", object()
    )
    monkeypatch.setattr(
        "by_qa.qa.services.checkpointer_factory.OpenGaussSaver",
        MagicMock(return_value=fake_saver),
    )

    with patch("psycopg.Connection.connect", return_value=FakeConnection()):
        saver = _create_sync_opengauss_saver(settings, None)

    assert calls == ['CREATE SCHEMA IF NOT EXISTS "byai"']
    assert saver is fake_saver


@pytest.mark.asyncio
async def test_async_opengauss_saver_creates_configured_schema(monkeypatch):
    settings = _mock_settings()
    settings.db_schema = "byai"
    settings.build_opengauss_dsn = MagicMock(
        return_value="postgresql://gaussdb:secret@localhost:5432/postgres"
    )
    calls = []
    fake_saver = MagicMock()

    class FakeConnection:
        async def execute(self, statement):
            calls.append(statement.as_string(None))

    monkeypatch.setattr(
        "by_qa.qa.services.checkpointer_factory.AsyncPostgresSaver", object()
    )
    monkeypatch.setattr(
        "by_qa.qa.services.checkpointer_factory.AsyncOpenGaussSaver",
        MagicMock(return_value=fake_saver),
    )

    with patch(
        "psycopg.AsyncConnection.connect", new=AsyncMock(return_value=FakeConnection())
    ):
        saver = await _create_async_opengauss_saver(settings, None)

    assert calls == ['CREATE SCHEMA IF NOT EXISTS "byai"']
    assert saver is fake_saver


def test_get_checkpointer_backend_name_recognizes_memory_backend():
    assert get_checkpointer_backend_name(InMemorySaver()) == "memory"
    assert get_checkpointer_backend_name(MagicMock()) == "unknown"
