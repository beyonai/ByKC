"""Tests for knowledge-base ingestion settings."""

import os
from pathlib import Path
from unittest.mock import patch

from by_qa.config import Settings, load_project_env_file
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError


def test_settings_accept_kb_ingestion_env_vars():
    """Settings should expose KB ingestion infrastructure configuration."""
    settings = Settings(
        MINIO_ENDPOINT="127.0.0.1:19000",
        MINIO_ACCESS_KEY="minioadmin",
        MINIO_SECRET_KEY="minioadmin",
        KB_MINIO_BUCKET="knowledge-base",
        KB_MINIO_MARKDOWN_BUCKET="knowledge-base-markdown",
        EMBEDDING_MODEL_NAME="bge-m3",
        EMBEDDING_BASE_URL="https://embedding.example.com",
        EMBEDDING_API_KEY="secret",
        EMBEDDING_DIMENSION=1024,
        EMBEDDING_DISTANCE_METRIC="cosine",
        EMBEDDING_BATCH_MAX_TEXTS=12,
    )

    assert settings.kb_minio_endpoint == "127.0.0.1:19000"
    assert settings.kb_minio_bucket == "knowledge-base"
    assert settings.kb_minio_markdown_bucket == "knowledge-base-markdown"
    assert settings.embedding_model_name == "bge-m3"
    assert settings.embedding_dimension == 1024
    assert settings.embedding_batch_max_texts == 12


def test_settings_accept_shared_db_env_vars():
    """Settings should expose shared database connection parts."""
    settings = Settings(
        DB_HOST="10.10.168.204",
        DB_PORT=5432,
        DB_DATABASE="byqa",
        DB_SCHEMA="byai",
        DB_USER="gaussdb",
        DB_PASS="Admin@123",
    )

    assert settings.db_host == "10.10.168.204"
    assert settings.db_port == 5432
    assert settings.db_database == "byqa"
    assert settings.db_schema == "byai"
    assert settings.db_user == "gaussdb"
    assert settings.db_pass == "Admin@123"
    assert settings.resolved_kb_opengauss_dsn == settings.build_opengauss_dsn()
    assert (
        settings.resolved_checkpointer_opengauss_dsn == settings.build_opengauss_dsn()
    )


def test_settings_ignore_removed_database_env_vars():
    """Legacy DSN and backend env vars should no longer configure the app."""
    settings = Settings(
        KB_OPENGAUSS_DSN="postgresql://legacy:secret@127.0.0.1:5432/postgres",
        CHECKPOINTER_BACKEND="opengauss",
        CHECKPOINTER_OPENGAUSS_DSN="postgresql://legacy:secret@127.0.0.1:5432/postgres",
        CHECKPOINTER_SQLITE_PATH="/tmp/legacy-checkpoints.db",
        DB_HOST="",
        DB_USER="",
        DB_PASS="",
    )

    assert settings.resolved_kb_opengauss_dsn == ""
    assert settings.resolved_checkpointer_opengauss_dsn == ""
    assert settings.checkpointer_backend == "opengauss"
    assert not hasattr(settings, "checkpointer_sqlite_path")


def test_settings_ignore_removed_minio_env_vars():
    """Legacy KB_MINIO_* env vars should no longer configure object storage."""
    settings = Settings(
        KB_MINIO_ENDPOINT="legacy-endpoint",
        KB_MINIO_ACCESS_KEY="legacy-access",
        KB_MINIO_SECRET_KEY="legacy-secret",
        KB_MINIO_SECURE=True,
        MINIO_ENDPOINT="new-endpoint",
        MINIO_ACCESS_KEY="new-access",
        MINIO_SECRET_KEY="new-secret",
        MINIO_SECURE=False,
    )

    assert settings.kb_minio_endpoint == "new-endpoint"
    assert settings.kb_minio_access_key == "new-access"
    assert settings.kb_minio_secret_key == "new-secret"
    assert settings.kb_minio_secure is False


def test_settings_expose_embedding_batch_max_texts_default():
    """Embedding batch size should default to a conservative multi-input request size."""
    settings = Settings()

    assert settings.embedding_batch_max_texts == 10


def test_settings_accept_minus_one_embedding_batch_max_texts():
    """Embedding batch size should accept -1 as the non-batching sentinel."""
    settings = Settings(EMBEDDING_BATCH_MAX_TEXTS=-1)

    assert settings.embedding_batch_max_texts == -1


def test_settings_accept_strict_json_llm_extra_body_strings():
    """LLM extra_body should accept strict JSON strings without env predecode."""
    settings = Settings(
        LLM_STANDARD_EXTRA_BODY='{"thinking": {"type": "enabled"}}',
        LLM_LIGHTWEIGHT_EXTRA_BODY='{"reasoning_split": false}',
    )

    assert settings.llm_standard_extra_body == {"thinking": {"type": "enabled"}}
    assert settings.llm_lightweight_extra_body == {"reasoning_split": False}


def test_validate_knowledge_base_settings_rejects_missing_runtime_config():
    """Knowledge-base runtime should fail fast when required settings are missing."""
    from by_qa.knowledge_base.infrastructure.runtime import (
        validate_knowledge_base_settings,
    )

    settings = Settings(
        DB_HOST="",
        DB_USER="",
        DB_PASS="",
        MINIO_ENDPOINT="",
        MINIO_ACCESS_KEY="",
        MINIO_SECRET_KEY="",
        KB_MINIO_BUCKET="",
        KB_MINIO_MARKDOWN_BUCKET="",
        EMBEDDING_MODEL_NAME="",
        EMBEDDING_DIMENSION=0,
    )

    try:
        validate_knowledge_base_settings(settings)
    except KnowledgeBaseConfigurationError as exc:
        message = str(exc)
        assert "DB_HOST/DB_USER/DB_PASS" in message
        assert "EMBEDDING_MODEL_NAME" in message
    else:
        raise AssertionError("expected KnowledgeBaseConfigurationError")


def test_validate_knowledge_base_settings_accepts_provider_embedding_config():
    """Knowledge-base validation should allow embedding settings from a provider."""
    from by_qa.core.model_config import ModelConfig
    from by_qa.knowledge_base.infrastructure.runtime import (
        validate_knowledge_base_settings,
    )

    settings = Settings(
        DB_HOST="127.0.0.1",
        DB_USER="gaussdb",
        DB_PASS="secret",
        MINIO_ENDPOINT="127.0.0.1:19000",
        MINIO_ACCESS_KEY="minio",
        MINIO_SECRET_KEY="secret",
        KB_MINIO_BUCKET="knowledge-base",
        KB_MINIO_MARKDOWN_BUCKET="knowledge-base-markdown",
        EMBEDDING_MODEL_NAME="",
        EMBEDDING_DIMENSION=0,
    )
    embedding_config = ModelConfig(
        model_name="custom-embedding",
        temperature=0.0,
        base_url="https://embedding.example.com/v1",
        api_key="secret",
        dimension=1024,
    )

    validate_knowledge_base_settings(settings, embedding_config=embedding_config)


def test_settings_env_file_is_pinned_to_project_root():
    """Settings should read the project .env regardless of process cwd."""
    env_file = Settings.model_config["env_file"]

    assert isinstance(env_file, Path)
    assert env_file.is_absolute()
    assert env_file.name == ".env"


def test_settings_expose_kb_fetch_cache_defaults():
    """Fetch cache settings should default to the kb cache directory and 24h TTL."""
    settings = Settings(
        KB_FETCH_CACHE_TTL_SECONDS=24 * 60 * 60,
        AGENT_DATA_PATH="agent_data",
    )

    assert settings.kb_fetch_cache_ttl_seconds == 24 * 60 * 60
    assert settings.kb_cache_path == settings.agent_data_path / "kb_cache"


def test_settings_expose_service_registry_defaults(monkeypatch):
    """Service registry settings should expose the default service identity."""
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)
    monkeypatch.delenv("REDIS_USERNAME", raising=False)
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.delenv("REDIS_DATABASE", raising=False)
    settings = Settings(_env_file=None)

    assert settings.service_name == "by-qa-manager"
    assert settings.redis_host == "localhost"
    assert settings.redis_port == 6379
    assert settings.redis_username == ""
    assert settings.redis_password == ""
    assert settings.redis_database == 0


def test_settings_accept_service_registry_redis_env_vars():
    """Settings should expose explicit Redis connection configuration."""
    settings = Settings(
        REDIS_HOST="10.10.168.204",
        REDIS_PORT=6379,
        REDIS_USERNAME="",
        REDIS_PASSWORD="admin123",
        REDIS_DATABASE=0,
    )

    assert settings.redis_host == "10.10.168.204"
    assert settings.redis_port == 6379
    assert settings.redis_username == ""
    assert settings.redis_password == "admin123"
    assert settings.redis_database == 0


def test_load_project_env_file_exports_redis_cluster_vars_without_overrides(
    tmp_path,
    monkeypatch,
):
    """Project .env values should be visible to RedisConfig.from_env."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "REDIS_CLUSTER_HOST=10.10.168.204:6379,10.10.168.205:6379",
                "REDIS_KEY_SCHEMA_VERSION=v2",
                "REDIS_PASSWORD=from-file",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("REDIS_CLUSTER_HOST", raising=False)
    monkeypatch.delenv("REDIS_KEY_SCHEMA_VERSION", raising=False)
    monkeypatch.setenv("REDIS_PASSWORD", "from-env")

    load_project_env_file(env_file)

    assert os.environ["REDIS_CLUSTER_HOST"] == "10.10.168.204:6379,10.10.168.205:6379"
    assert os.environ["REDIS_KEY_SCHEMA_VERSION"] == "v2"
    assert os.environ["REDIS_PASSWORD"] == "from-env"


def test_settings_use_configured_host_machine():
    """HOST_MACHINE should override host auto-detection when configured."""
    settings = Settings(HOST_MACHINE="10.0.0.8")

    assert settings.host_machine == "10.0.0.8"


def test_settings_detect_host_machine_when_env_is_missing():
    """HOST_MACHINE should fall back to the detected local machine IP."""

    class _FailingSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def connect(self, address):
            del address
            raise OSError("network unavailable")

    with (
        patch("by_qa.config.socket.socket", return_value=_FailingSocket()),
        patch("by_qa.config.socket.getaddrinfo") as fake_getaddrinfo,
    ):
        fake_getaddrinfo.return_value = [
            (
                2,
                2,
                17,
                "",
                ("127.0.0.1", 0),
            ),
            (
                2,
                2,
                17,
                "",
                ("192.168.1.10", 0),
            ),
        ]
        settings = Settings()

    assert settings.host_machine == "192.168.1.10"


def test_settings_prefer_default_outbound_ip_over_hostname_resolution():
    """HOST_MACHINE should prefer the default outbound LAN address when available."""

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def connect(self, address):
            self.address = address

        def getsockname(self):
            return ("10.0.0.10", 43210)

    with (
        patch("by_qa.config.socket.socket", return_value=FakeSocket()),
        patch("by_qa.config.socket.getaddrinfo") as fake_getaddrinfo,
    ):
        fake_getaddrinfo.return_value = [
            (
                2,
                2,
                17,
                "",
                ("192.168.56.10", 0),
            ),
        ]
        settings = Settings()

    assert settings.host_machine == "10.0.0.10"


def test_settings_detect_host_machine_when_env_is_blank():
    """Blank HOST_MACHINE should fall back to the detected local machine IP."""

    class _FailingSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def connect(self, address):
            del address
            raise OSError("network unavailable")

    with (
        patch("by_qa.config.socket.socket", return_value=_FailingSocket()),
        patch("by_qa.config.socket.getaddrinfo") as fake_getaddrinfo,
    ):
        fake_getaddrinfo.return_value = [
            (
                2,
                2,
                17,
                "",
                ("192.168.1.11", 0),
            ),
        ]
        settings = Settings(HOST_MACHINE="")

    assert settings.host_machine == "192.168.1.11"


def test_settings_fallback_to_loopback_when_only_loopback_is_available():
    """HOST_MACHINE should fall back to loopback when no non-loopback IPv4 is found."""

    class _FailingSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def connect(self, address):
            del address
            raise OSError("network unavailable")

    with (
        patch("by_qa.config.socket.socket", return_value=_FailingSocket()),
        patch("by_qa.config.socket.getaddrinfo", return_value=[]),
    ):
        settings = Settings()

    assert settings.host_machine == "127.0.0.1"


def test_validate_knowledge_base_settings_skips_minio_checks_for_custom_provider(
    monkeypatch,
):
    """When BY_QA_STORAGE_PROVIDER is set, MinIO fields must not be required."""
    from by_qa.knowledge_base.infrastructure.runtime import (
        validate_knowledge_base_settings,
    )

    monkeypatch.setenv(
        "BY_QA_STORAGE_PROVIDER", "tests_custom_kb_storage_provider:FakeProvider"
    )

    settings = Settings(
        DB_HOST="db",
        DB_USER="u",
        DB_PASS="p",
        MINIO_ENDPOINT="",
        MINIO_ACCESS_KEY="",
        MINIO_SECRET_KEY="",
        KB_MINIO_BUCKET="",
        KB_MINIO_MARKDOWN_BUCKET="",
        EMBEDDING_MODEL_NAME="m",
        EMBEDDING_DIMENSION=8,
    )
    validate_knowledge_base_settings(settings)
