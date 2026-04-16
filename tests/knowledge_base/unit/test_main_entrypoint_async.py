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
    """The async entrypoint should use uvicorn.Server.serve."""
    recorded = {}

    class FakeServer:
        def __init__(self, config):
            recorded["config"] = config

        async def serve(self):
            recorded["served"] = True

    fake_uvicorn = SimpleNamespace(
        Config=lambda *args, **kwargs: SimpleNamespace(
            app=args[0] if args else kwargs.get("app"),
            host=kwargs.get("host"),
            port=kwargs.get("port"),
            reload=kwargs.get("reload"),
            factory=kwargs.get("factory"),
        ),
        Server=FakeServer,
    )

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", fake_uvicorn)

    await main_module.async_main()

    assert recorded["served"] is True
    cfg = recorded["config"]
    assert cfg.host == main_module.settings.host
    assert cfg.port == main_module.settings.port
    assert cfg.reload is False
    assert cfg.factory is True


@pytest.mark.asyncio
async def test_lifespan_logs_configuration_and_registers_service(monkeypatch):
    """Lifespan should log startup configuration and register the service."""
    info_calls = []
    warning_calls = []
    recorded = {}

    async def fake_register(**kwargs):
        recorded["register_kwargs"] = kwargs

    async def fake_unregister():
        recorded["unregistered"] = True

    fake_settings = SimpleNamespace(
        service_name="by-qa-manager",
        host="0.0.0.0",
        port=8000,
        host_machine="192.168.1.10",
        checkpointer_backend="sqlite",
        agent_data_path="agent_data",
        redis_host="10.10.168.204",
        redis_port=6379,
        redis_username="",
        redis_password="admin123",
        redis_database=0,
        kb_minio_endpoint="127.0.0.1:19000",
        kb_minio_bucket="knowledge-base",
        embedding_model_name="",
        embedding_base_url="https://embedding.example.com",
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="secret",
        ensure_directories=lambda: None,
    )
    fake_registry = SimpleNamespace(register=fake_register, unregister=fake_unregister)
    fake_application = SimpleNamespace(state=SimpleNamespace(enabled_modules=[]))
    fake_redis_client = object()

    monkeypatch.setattr(main_module, "settings", fake_settings)
    monkeypatch.setattr(
        main_module,
        "ServiceRegistry",
        lambda redis_client=None: recorded.update(service_registry_client=redis_client)
        or fake_registry,
    )
    monkeypatch.setattr(
        main_module,
        "Redis",
        lambda **kwargs: recorded.update(redis_kwargs=kwargs) or fake_redis_client,
    )
    monkeypatch.setattr(
        main_module.logger, "info", lambda *args: info_calls.append(args)
    )
    monkeypatch.setattr(
        main_module.logger, "warning", lambda *args: warning_calls.append(args)
    )

    async def fake_initialize(enabled_modules):
        recorded["initialized"] = enabled_modules

    async def fake_shutdown(enabled_modules):
        recorded["shutdown"] = enabled_modules

    monkeypatch.setattr(
        main_module,
        "_initialize_knowledge_base_runtime",
        fake_initialize,
    )
    monkeypatch.setattr(
        main_module,
        "_shutdown_knowledge_base_runtime",
        fake_shutdown,
    )

    async with main_module.lifespan(fake_application):
        pass

    assert recorded["register_kwargs"] == {
        "service_name": "by-qa-manager",
        "host": "192.168.1.10",
        "port": 8000,
        "weight": 10,
        "metadata": {"version": "0.1.1"},
    }
    assert recorded["redis_kwargs"] == {
        "host": "10.10.168.204",
        "port": 6379,
        "db": 0,
        "password": "admin123",
        "decode_responses": True,
    }
    assert recorded["service_registry_client"] is fake_redis_client
    assert recorded["unregistered"] is True
    assert recorded["initialized"] == []
    assert recorded["shutdown"] == []
    assert (
        "application startup configuration: service_name=%s, host=%s, port=%s, host_machine=%s, checkpointer_backend=%s, agent_data_path=%s, knowledge_base_configured=%s, document_chunking_configured=%s, qa_llm_configured=%s",
        "by-qa-manager",
        "0.0.0.0",
        8000,
        "192.168.1.10",
        "sqlite",
        "agent_data",
        False,
        False,
        True,
    ) in info_calls
    assert (
        "service registry registered: service_name=%s, host=%s, port=%s, metadata=%s",
        "by-qa-manager",
        "192.168.1.10",
        8000,
        {"version": "0.1.1"},
    ) in info_calls
    assert (
        "service registry redis configured: host=%s, port=%s, db=%s, username_set=%s, password_set=%s",
        "10.10.168.204",
        6379,
        0,
        False,
        True,
    ) in info_calls
    assert (
        "service registry unregistered: service_name=%s",
        "by-qa-manager",
    ) in info_calls
    assert (
        "application startup configuration gaps: missing=%s",
        "DB_HOST/DB_USER/DB_PASS,EMBEDDING_MODEL_NAME",
    ) in warning_calls
