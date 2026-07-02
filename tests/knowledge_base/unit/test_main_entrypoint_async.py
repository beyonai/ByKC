"""Tests for async-aware application entrypoints."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import by_qa.main as main_module


@pytest.fixture(autouse=True)
def reset_main_runtime_state(monkeypatch):
    """Keep module-level runtime caches isolated across tests."""
    monkeypatch.setattr(main_module, "_knowledge_base_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_ingestion_service", None)
    monkeypatch.setattr(main_module, "_knowledge_item_search_service", None)
    monkeypatch.setattr(main_module, "_knowledge_fetch_cache_cleanup_service", None)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)
    monkeypatch.setattr(main_module, "_knowledge_base_schema_initialized", False)
    monkeypatch.setattr(main_module, "_knowledge_base_schema_lock", asyncio.Lock())


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
async def test_knowledge_item_services_receive_model_config_provider(monkeypatch):
    """Main runtime wiring should pass the configured provider into KB services."""
    provider = object()
    recorded = {}

    async def fake_build_ingestion(settings, provider=None):
        recorded["ingestion"] = (settings, provider)
        return "ingestion-service"

    async def fake_build_search(settings, provider=None):
        recorded["search"] = (settings, provider)
        return "search-service"

    fake_runtime = SimpleNamespace(
        build_knowledge_item_ingestion_service=fake_build_ingestion,
        build_knowledge_item_search_service=fake_build_search,
    )

    def fake_import(name, global_vars=None, local_vars=None, fromlist=(), level=0):
        if name == "by_qa.knowledge_base.infrastructure.runtime":
            return fake_runtime
        return original_import(name, global_vars, local_vars, fromlist, level)

    original_import = __import__
    monkeypatch.setattr(main_module, "load_model_config_provider", lambda: provider)
    monkeypatch.setattr(__import__("builtins"), "__import__", fake_import)

    async def fake_ensure_schema(provider=None):
        recorded.setdefault("ensured", []).append(provider)

    monkeypatch.setattr(
        main_module,
        "_ensure_knowledge_base_schema_initialized",
        fake_ensure_schema,
    )

    ingestion_service = (
        await main_module._get_or_build_knowledge_item_ingestion_service()
    )
    search_service = await main_module._get_or_build_knowledge_item_search_service()

    assert ingestion_service == "ingestion-service"
    assert search_service == "search-service"
    assert recorded["ingestion"] == (main_module.settings, provider)
    assert recorded["search"] == (main_module.settings, provider)
    assert recorded["ensured"] == [provider, provider]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolver_name", "runtime_builder_name", "expected_service"),
    [
        (
            "_get_or_build_metadata_search_service",
            "build_metadata_search_service",
            "metadata-search-service",
        ),
        (
            "_get_or_build_file_metadata_query_service",
            "build_file_metadata_query_service",
            "file-metadata-query-service",
        ),
    ],
)
async def test_metadata_services_also_ensure_schema_initialized_for_direct_calls(
    monkeypatch, resolver_name, runtime_builder_name, expected_service
):
    """Metadata service helpers should trigger lazy schema bootstrap."""
    recorded = {}

    async def fake_builder(settings):
        recorded["builder_settings"] = settings
        return expected_service

    fake_runtime = SimpleNamespace(**{runtime_builder_name: fake_builder})

    def fake_import(name, global_vars=None, local_vars=None, fromlist=(), level=0):
        if name == "by_qa.knowledge_base.infrastructure.runtime":
            return fake_runtime
        return original_import(name, global_vars, local_vars, fromlist, level)

    original_import = __import__
    monkeypatch.setattr(__import__("builtins"), "__import__", fake_import)

    async def fake_ensure_schema(provider=None):
        recorded.setdefault("ensured", []).append(provider)

    monkeypatch.setattr(
        main_module,
        "_ensure_knowledge_base_schema_initialized",
        fake_ensure_schema,
    )

    resolver = getattr(main_module, resolver_name)
    service = await resolver()

    assert service == expected_service
    assert recorded["builder_settings"] is main_module.settings
    assert recorded["ensured"] == [None]


@pytest.mark.asyncio
async def test_document_chunking_service_uses_model_config_provider(monkeypatch):
    """Document chunking should use provider embedding config in API runtime wiring."""
    recorded = {}

    async def fake_get_config(model_type):
        recorded["model_type"] = model_type
        return SimpleNamespace(
            model_name="custom-embedding",
            base_url="https://embedding.example.com/v1",
            api_key="secret",
            dimension=1024,
            batch_max_texts=32,
        )

    provider = SimpleNamespace(get_config=fake_get_config)

    def fake_build_document_chunking_service(settings, embedding_config=None):
        recorded["build"] = (settings, embedding_config)
        return "chunking-service"

    fake_runtime = SimpleNamespace(
        build_document_chunking_service=fake_build_document_chunking_service
    )

    def fake_import(name, global_vars=None, local_vars=None, fromlist=(), level=0):
        if name == "by_qa.knowledge_build.runtime":
            return fake_runtime
        return original_import(name, global_vars, local_vars, fromlist, level)

    original_import = __import__
    monkeypatch.setattr(main_module, "load_model_config_provider", lambda: provider)
    monkeypatch.setattr(__import__("builtins"), "__import__", fake_import)
    monkeypatch.setattr(main_module, "_document_chunking_service", None)

    service = await main_module.resolve_document_chunking_service()

    assert service == "chunking-service"
    assert recorded["model_type"] == "embedding"
    settings, embedding_config = recorded["build"]
    assert settings is main_module.settings
    assert embedding_config.model_name == "custom-embedding"


@pytest.mark.asyncio
async def test_knowledge_base_schema_initializes_lazily_with_provider_config(
    monkeypatch,
):
    """KB schema bootstrap should run lazily with provider embedding config."""
    recorded = {}

    async def fake_get_config(model_type):
        recorded["model_type"] = model_type
        return SimpleNamespace(model_name="custom-embedding", dimension=1024)

    provider = SimpleNamespace(get_config=fake_get_config)
    fake_settings = SimpleNamespace(
        resolved_kb_opengauss_dsn="postgresql://gaussdb:secret@127.0.0.1/postgres",
        embedding_model_name="",
    )

    class FakeConnection:
        async def close(self):
            recorded["closed"] = True

    def fake_connection_factory(settings):
        recorded["connection_settings"] = settings

        async def build_connection():
            return FakeConnection()

        return build_connection

    class FakeBootstrap:
        async def apply(self, connection):
            recorded["bootstrap_connection"] = connection

    async def fake_build_bootstrap_service(settings, provider=None):
        from by_qa.core.model_config import LLMModelProfile

        await provider.get_config(LLMModelProfile.EMBEDDING)
        recorded["bootstrap"] = (settings, provider)
        return FakeBootstrap()

    class FakeCleanup:
        async def start(self):
            recorded["cleanup_started"] = True

    async def fake_build_cleanup_service():
        return FakeCleanup()

    monkeypatch.setattr(main_module, "settings", fake_settings)
    monkeypatch.setattr(main_module, "load_model_config_provider", lambda: provider)
    monkeypatch.setattr(
        "by_qa.knowledge_base.infrastructure.database.build_connection_factory",
        fake_connection_factory,
    )
    monkeypatch.setattr(
        "by_qa.knowledge_base.infrastructure.runtime.build_bootstrap_service",
        fake_build_bootstrap_service,
    )
    monkeypatch.setattr(
        main_module,
        "_get_or_build_knowledge_fetch_cache_cleanup_service",
        fake_build_cleanup_service,
    )

    await main_module._ensure_knowledge_base_schema_initialized(provider=provider)
    await main_module._ensure_knowledge_base_schema_initialized(provider=provider)

    assert recorded["model_type"] == "embedding"
    assert recorded["connection_settings"] is fake_settings
    assert recorded["bootstrap"] == (fake_settings, provider)
    assert recorded["cleanup_started"] is True
    assert recorded["closed"] is True


def test_api_requests_receive_distinct_model_config_provider_instances(monkeypatch):
    """API request handling should create a fresh model provider per request."""
    providers = []
    service_providers = []
    service_settings = []

    def fake_load_model_config_provider():
        provider = SimpleNamespace(sequence=len(providers))
        providers.append(provider)
        return provider

    async def fake_build_search_service(settings, provider=None):
        service_settings.append(settings)
        service_providers.append(provider)
        return SimpleNamespace(provider_sequence=provider.sequence)

    monkeypatch.setattr(
        main_module, "load_model_config_provider", fake_load_model_config_provider
    )
    monkeypatch.setattr(
        "by_qa.knowledge_base.infrastructure.runtime.build_knowledge_item_search_service",
        fake_build_search_service,
    )
    monkeypatch.setattr(
        main_module, "_register_api_modules", lambda application: ([], {})
    )

    async def fake_ensure_schema(provider=None):
        del provider
        return None

    monkeypatch.setattr(
        main_module,
        "_ensure_knowledge_base_schema_initialized",
        fake_ensure_schema,
    )

    application = main_module.create_app()

    @application.get("/api/v1/provider-sequence")
    async def provider_sequence():
        service = await main_module.resolve_knowledge_item_search_service()
        return {"provider_sequence": service.provider_sequence}

    client = TestClient(application)

    first_response = client.get("/api/v1/provider-sequence")
    second_response = client.get("/api/v1/provider-sequence")

    assert first_response.json() == {"provider_sequence": 0}
    assert second_response.json() == {"provider_sequence": 1}
    assert service_providers == providers
    assert service_settings == [main_module.settings, main_module.settings]
    assert service_providers[0] is not service_providers[1]


def test_api_request_reuses_model_config_provider_within_request(monkeypatch):
    """All provider-aware resolvers in one API request should share one provider."""
    providers = []
    service_providers = []
    service_settings = []

    def fake_load_model_config_provider():
        provider = SimpleNamespace(sequence=len(providers))
        providers.append(provider)
        return provider

    async def fake_build_search_service(settings, provider=None):
        service_settings.append(settings)
        service_providers.append(provider)
        return SimpleNamespace(provider_sequence=provider.sequence)

    monkeypatch.setattr(
        main_module, "load_model_config_provider", fake_load_model_config_provider
    )
    monkeypatch.setattr(
        "by_qa.knowledge_base.infrastructure.runtime.build_knowledge_item_search_service",
        fake_build_search_service,
    )
    monkeypatch.setattr(
        main_module, "_register_api_modules", lambda application: ([], {})
    )

    async def fake_ensure_schema(provider=None):
        del provider
        return None

    monkeypatch.setattr(
        main_module,
        "_ensure_knowledge_base_schema_initialized",
        fake_ensure_schema,
    )

    application = main_module.create_app()

    @application.get("/api/v1/provider-sequences")
    async def provider_sequences():
        first_service = await main_module.resolve_knowledge_item_search_service()
        second_service = await main_module.resolve_knowledge_item_search_service()
        return {
            "provider_sequences": [
                first_service.provider_sequence,
                second_service.provider_sequence,
            ]
        }

    client = TestClient(application)
    response = client.get("/api/v1/provider-sequences")

    assert response.json() == {"provider_sequences": [0, 0]}
    assert len(providers) == 1
    assert service_settings == [main_module.settings, main_module.settings]
    assert service_providers[0] is providers[0]
    assert service_providers[1] is providers[0]


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
        checkpointer_backend="opengauss",
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
    assert "initialized" not in recorded
    assert recorded["shutdown"] == []
    assert (
        "application startup configuration: service_name=%s, host=%s, port=%s, host_machine=%s, checkpointer_backend=%s, agent_data_path=%s, knowledge_base_configured=%s, document_chunking_configured=%s, qa_llm_configured=%s",
        "by-qa-manager",
        "0.0.0.0",
        8000,
        "192.168.1.10",
        "opengauss",
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
