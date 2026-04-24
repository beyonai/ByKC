"""Tests for BaseQAEngine."""

from unittest.mock import AsyncMock, patch

import pytest

from by_qa.qa.common.base_engine import BaseQAEngine
from by_qa.qa.common.exceptions import ValidationError
from by_qa.qa.common.models import CoreInput, StreamEvent


class ConcreteEngine(BaseQAEngine):
    THREAD_ID_PREFIX = "test_engine"

    async def _get_graph(self):
        return AsyncMock()

    async def _do_stream_search(self, input_data, session_id, message_id, config):
        yield StreamEvent.done(session_id=session_id, role="test")


def _make_engine(config=None):
    with patch("by_qa.qa.common.base_engine.get_settings", return_value=object()):
        return ConcreteEngine(config=config)


def test_prepare_run_raises_on_empty_query():
    engine = _make_engine()
    with pytest.raises(ValidationError):
        engine._prepare_run(CoreInput(query="   "))


def test_prepare_run_returns_session_and_config():
    engine = _make_engine()
    session_id, message_id, config = engine._prepare_run(
        CoreInput(query="test", session_id="s1", message_id="m1"),
        recursion_limit=20,
    )
    assert session_id == "s1"
    assert message_id == "m1"
    assert config["configurable"]["thread_id"] == "test_engine_s1"
    assert config["run_id"] == "m1"
    assert config["recursion_limit"] == 20


def test_prepare_run_generates_uuid_when_ids_missing():
    engine = _make_engine()
    session_id, message_id, _ = engine._prepare_run(
        CoreInput(query="test"), recursion_limit=20
    )
    assert len(session_id) == 36
    assert len(message_id) == 36


@pytest.mark.asyncio
async def test_stream_search_calls_prepare_run_automatically():
    """stream_search must invoke _prepare_run so subclasses don't have to."""
    engine = _make_engine()
    events = [e async for e in engine.stream_search(CoreInput(query="hello"))]
    assert len(events) == 1
    assert events[0].type.value == "done"


@pytest.mark.asyncio
async def test_stream_search_raises_on_empty_query():
    engine = _make_engine()
    with pytest.raises(ValidationError):
        async for _ in engine.stream_search(CoreInput(query="  ")):
            pass


@pytest.mark.asyncio
async def test_close_clears_graph_and_checkpointer():
    engine = _make_engine()
    engine._graph = object()
    engine._checkpointer = object()
    with patch(
        "by_qa.qa.common.base_engine.close_checkpointer_async",
        new_callable=AsyncMock,
    ) as mock_close:
        await engine.close()
    mock_close.assert_awaited_once()
    assert engine._graph is None
    assert engine._checkpointer is None


def test_get_config_value_from_dict():
    engine = _make_engine(config={"key": "val"})
    assert engine._get_config_value("key") == "val"
    assert engine._get_config_value("missing", "default") == "default"


def test_base_engine_exported_from_common():
    from by_qa.qa import common as qa_common

    assert hasattr(qa_common, "BaseQAEngine")
