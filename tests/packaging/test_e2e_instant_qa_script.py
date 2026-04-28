"""Tests for the instant QA example CLI renderer."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import dotenv
import pytest

SCRIPT_PATH = Path("examples/e2e_kb_qa/run_instant_qa.py")


def _patch_qa_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch optional QA imports without leaking mocks to later test modules."""
    for mod in [
        "langchain_core",
        "langchain_core.runnables",
        "by_qa.qa.services",
        "by_qa.qa.services.checkpointer_factory",
        "by_qa.qa.services.llm_service",
    ]:
        monkeypatch.setitem(sys.modules, mod, MagicMock())


@pytest.fixture
def qa_models(monkeypatch: pytest.MonkeyPatch):
    """Load QA models without importing the heavy qa.common package."""
    _patch_qa_imports(monkeypatch)
    models_path = Path("src/by_qa/qa/common/models.py").resolve()
    spec = importlib.util.spec_from_file_location(
        "_packaging_qa_common_models",
        models_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    package = types.ModuleType("by_qa.qa.common")
    package.__path__ = []  # type: ignore[attr-defined]
    package.models = module
    monkeypatch.setitem(sys.modules, "by_qa.qa.common", package)
    monkeypatch.setitem(sys.modules, "by_qa.qa.common.models", module)

    return module.StreamEvent, module.StreamEventType


def _load_module(monkeypatch: pytest.MonkeyPatch):
    # The example helper imports common.py, which loads examples/e2e_kb_qa/.env at
    # import time. Keep that .env from mutating process-wide settings that later
    # integration tests rely on.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.syspath_prepend(str(SCRIPT_PATH.parent.resolve()))
    sys.modules.pop("common", None)
    sys.modules.pop("e2e_run_instant_qa", None)
    spec = importlib.util.spec_from_file_location("e2e_run_instant_qa", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_renderer_streams_tokens_without_echoing_final_answer(
    capsys,
    monkeypatch,
    qa_models,  # pylint: disable=redefined-outer-name
):
    module = _load_module(monkeypatch)
    stream_event_cls, _ = qa_models
    renderer = module.EventRenderer(stream_tokens=True, verbose_events=False)

    renderer.render(stream_event_cls.token(content="上"))
    renderer.render(stream_event_cls.token(content="海"))
    renderer.render(
        stream_event_cls.answer(
            content="上海",
            role="final_answer",
            instance_id="run-final",
        )
    )
    renderer.finish()

    output = capsys.readouterr().out
    assert "上海" in output
    assert "Final Answer" not in output


def test_renderer_hides_node_events_unless_verbose(
    capsys,
    monkeypatch,
    qa_models,  # pylint: disable=redefined-outer-name
):
    module = _load_module(monkeypatch)
    stream_event_cls, stream_event_type = qa_models
    renderer = module.EventRenderer(stream_tokens=True, verbose_events=False)

    renderer.render(
        stream_event_cls(
            type=stream_event_type.NODE_START,
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
