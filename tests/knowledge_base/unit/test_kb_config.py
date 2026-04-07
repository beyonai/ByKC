"""Tests for knowledge-base ingestion settings."""

from pathlib import Path

from by_qa.config import PROJECT_ROOT, Settings
from by_qa.knowledge_base.infrastructure.runtime import validate_knowledge_base_settings
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
    )

    assert settings.kb_opengauss_dsn.startswith("postgresql://")
    assert settings.kb_minio_endpoint == "127.0.0.1:19000"
    assert settings.kb_minio_bucket == "knowledge-base"
    assert settings.kb_minio_markdown_bucket == "knowledge-base-markdown"
    assert settings.embedding_model_name == "bge-m3"
    assert settings.embedding_dimension == 1024


def test_validate_knowledge_base_settings_rejects_missing_runtime_config():
    """Knowledge-base runtime should fail fast when required settings are missing."""
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
