"""Integration test: verify checkpointer connection is released on engine close."""

from unittest.mock import MagicMock, patch

import pytest

from by_qa.qa.engines.instant.engine import InstantQAEngine


def _mock_settings(tmp_path):
    settings = type("Settings", (), {})()
    settings.checkpointer_backend = "sqlite"
    settings.checkpointer_sqlite_path = str(tmp_path / "test_checkpoints.db")
    return settings


@pytest.mark.asyncio
async def test_async_context_manager_closes_sqlite_connection(tmp_path):
    """After exiting `async with`, the underlying aiosqlite connection must be closed."""
    settings = _mock_settings(tmp_path)

    mock_graph = MagicMock()

    async def mock_astream_events(*_args, **_kwargs):
        yield {
            "event": "on_chain_end",
            "name": "final_answer",
            "metadata": {"langgraph_node": "final_answer"},
            "run_id": "run-final",
            "parent_ids": [],
            "data": {"output": {"final_answer": "answer"}},
        }

    mock_graph.astream_events = mock_astream_events

    with patch("by_qa.qa.common.base_engine.get_settings", return_value=settings):
        async with InstantQAEngine() as engine:
            from by_qa.qa.services.checkpointer_factory import create_checkpointer_async

            engine._checkpointer = await create_checkpointer_async(settings)
            engine._graph = mock_graph

            conn = engine._checkpointer.conn
            assert conn._running, "connection should be open before close"

        assert not conn._running, "connection should be closed after exiting async with"
        assert engine._checkpointer is None


@pytest.mark.asyncio
async def test_explicit_close_releases_sqlite_connection(tmp_path):
    """Calling close() directly must also release the connection."""
    settings = _mock_settings(tmp_path)

    with patch("by_qa.qa.common.base_engine.get_settings", return_value=settings):
        engine = InstantQAEngine()

    from by_qa.qa.services.checkpointer_factory import create_checkpointer_async

    engine._checkpointer = await create_checkpointer_async(settings)
    conn = engine._checkpointer.conn

    assert conn._running
    await engine.close()
    assert not conn._running
    assert engine._checkpointer is None


@pytest.mark.asyncio
async def test_close_is_safe_when_no_checkpointer():
    """close() on a fresh engine (no graph built) should not raise."""
    settings = type("Settings", (), {})()
    with patch("by_qa.qa.common.base_engine.get_settings", return_value=settings):
        engine = InstantQAEngine()

    await engine.close()
    assert engine._checkpointer is None


@pytest.mark.asyncio
async def test_close_is_idempotent(tmp_path):
    """Calling close() twice should not raise."""
    settings = _mock_settings(tmp_path)

    with patch("by_qa.qa.common.base_engine.get_settings", return_value=settings):
        engine = InstantQAEngine()

    from by_qa.qa.services.checkpointer_factory import create_checkpointer_async

    engine._checkpointer = await create_checkpointer_async(settings)
    await engine.close()
    await engine.close()
    assert engine._checkpointer is None
