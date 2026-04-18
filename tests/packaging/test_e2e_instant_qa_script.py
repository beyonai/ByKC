"""Tests for the instant QA example CLI renderer."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from by_qa.qa.common.models import StreamEvent, StreamEventType

SCRIPT_PATH = Path("examples/e2e_kb_qa/run_instant_qa.py")


def _load_module():
    sys.path.insert(0, str(SCRIPT_PATH.parent.resolve()))
    spec = importlib.util.spec_from_file_location("e2e_run_instant_qa", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_renderer_streams_tokens_without_echoing_final_answer(capsys):
    module = _load_module()
    renderer = module.EventRenderer(stream_tokens=True, verbose_events=False)

    renderer.render(StreamEvent.token(content="上"))
    renderer.render(StreamEvent.token(content="海"))
    renderer.render(
        StreamEvent.answer(content="上海", role="final_answer", instance_id="run-final")
    )
    renderer.finish()

    output = capsys.readouterr().out
    assert "上海" in output
    assert "Final Answer" not in output


def test_renderer_hides_node_events_unless_verbose(capsys):
    module = _load_module()
    renderer = module.EventRenderer(stream_tokens=True, verbose_events=False)

    renderer.render(
        StreamEvent(
            type=StreamEventType.NODE_START,
            role="decomposer",
            data={},
        )
    )
    renderer.finish()

    output = capsys.readouterr().out
    assert output == ""


def test_instant_qa_example_does_not_configure_sqlite_checkpointer():
    content = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "CHECKPOINTER_SQLITE_PATH" not in content
