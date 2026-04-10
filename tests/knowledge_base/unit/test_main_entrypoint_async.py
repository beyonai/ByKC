"""Tests for async-aware application entrypoints."""

from types import SimpleNamespace

import pytest

import by_qa.main as main_module


def test_main_runs_async_entrypoint(monkeypatch):
    """The sync CLI entrypoint should delegate to asyncio.run."""
    recorded = {}

    def fake_asyncio_run(awaitable):
        recorded["awaitable"] = awaitable
        awaitable.close()

    monkeypatch.setattr(main_module.asyncio, "run", fake_asyncio_run)

    main_module.main()

    assert recorded["awaitable"].cr_code.co_name == "async_main"


@pytest.mark.asyncio
async def test_async_main_runs_uvicorn(monkeypatch):
    """The async entrypoint should preserve the existing uvicorn invocation."""
    recorded = {}

    fake_uvicorn = SimpleNamespace(
        run=lambda *args, **kwargs: recorded.update(args=args, kwargs=kwargs)
    )
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", fake_uvicorn)

    await main_module.async_main()

    assert recorded["args"] == ("by_qa.main:create_app",)
    assert recorded["kwargs"] == {
        "host": main_module.settings.host,
        "port": main_module.settings.port,
        "reload": True,
        "factory": True,
    }
