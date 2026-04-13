"""Tests for knowledge-base ingestion settings."""

from pathlib import Path
from unittest.mock import patch

from by_qa.config import PROJECT_ROOT, Settings
from by_qa.knowledge_base.services.errors import KnowledgeBaseConfigurationError


def test_settings_accept_kb_ingestion_env_vars():
    """Settings should expose KB ingestion infrastructure configuration."""
    settings = Settings(
        KB_OPENGAUSS_DSN="postgresql://gaussdb:pass@127.0.0.1:15432/postgres?sslmode=disable",
        KB_MINIO_ENDPOINT="127.0.0.1:19000",
        KB_MINIO_ACCESS_KEY="minioadmin",
        KB_MINIO_SECRET_KEY="minioadmin",
        KB_MINIO_BUCKET="knowledge-base",
        KB_MINIO_MARKDOWN_BUCKET="knowledge-base-markdown",
        EMBEDDING_MODEL_NAME="bge-m3",
        EMBEDDING_BASE_URL="https://embedding.example.com",
        EMBEDDING_API_KEY="secret",
        EMBEDDING_DIMENSION=1024,
        EMBEDDING_DISTANCE_METRIC="cosine",
        EMBEDDING_BATCH_MAX_TEXTS=12,
    )

    assert settings.kb_opengauss_dsn.startswith("postgresql://")
    assert settings.kb_minio_endpoint == "127.0.0.1:19000"
    assert settings.kb_minio_bucket == "knowledge-base"
    assert settings.kb_minio_markdown_bucket == "knowledge-base-markdown"
    assert settings.embedding_model_name == "bge-m3"
    assert settings.embedding_dimension == 1024
    assert settings.embedding_batch_max_texts == 12


def test_settings_expose_embedding_batch_max_texts_default():
    """Embedding batch size should default to a conservative multi-input request size."""
    settings = Settings()

    assert settings.embedding_batch_max_texts == 10


def test_settings_accept_minus_one_embedding_batch_max_texts():
    """Embedding batch size should accept -1 as the non-batching sentinel."""
    settings = Settings(EMBEDDING_BATCH_MAX_TEXTS=-1)

    assert settings.embedding_batch_max_texts == -1


def test_validate_knowledge_base_settings_rejects_missing_runtime_config():
    """Knowledge-base runtime should fail fast when required settings are missing."""
    from by_qa.knowledge_base.infrastructure.runtime import (
        validate_knowledge_base_settings,
    )

    settings = Settings(
        KB_OPENGAUSS_DSN="",
        KB_MINIO_ENDPOINT="",
        KB_MINIO_ACCESS_KEY="",
        KB_MINIO_SECRET_KEY="",
        KB_MINIO_BUCKET="",
        KB_MINIO_MARKDOWN_BUCKET="",
        EMBEDDING_MODEL_NAME="",
        EMBEDDING_DIMENSION=0,
    )

    try:
        validate_knowledge_base_settings(settings)
    except KnowledgeBaseConfigurationError as exc:
        message = str(exc)
        assert "KB_OPENGAUSS_DSN" in message
        assert "EMBEDDING_MODEL_NAME" in message
    else:
        raise AssertionError("expected KnowledgeBaseConfigurationError")


def test_settings_env_file_is_pinned_to_project_root():
    """Settings should read the project .env regardless of process cwd."""
    env_file = Settings.model_config["env_file"]

    assert isinstance(env_file, Path)
    assert env_file.is_absolute()
    assert env_file.name == ".env"


def test_project_root_points_to_repository_root():
    """PROJECT_ROOT should resolve to the repository root after the package move."""
    assert PROJECT_ROOT.is_absolute()
    assert (PROJECT_ROOT / "pyproject.toml").exists()
    assert PROJECT_ROOT.name == "by-qa"


def test_settings_expose_kb_fetch_cache_defaults():
    """Fetch cache settings should default to the kb cache directory and 24h TTL."""
    settings = Settings(
        KB_FETCH_CACHE_TTL_SECONDS=24 * 60 * 60,
        AGENT_DATA_PATH="agent_data",
    )

    assert settings.kb_fetch_cache_ttl_seconds == 24 * 60 * 60
    assert settings.kb_cache_path == settings.agent_data_path / "kb_cache"


def test_settings_expose_service_registry_defaults():
    """Service registry settings should expose the default service identity."""
    settings = Settings()

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
