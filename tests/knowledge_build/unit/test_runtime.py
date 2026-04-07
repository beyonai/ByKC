"""Tests for knowledge-build runtime wiring."""

from by_qa.config import Settings
from by_qa.knowledge_build.runtime import validate_knowledge_build_settings


def test_validate_knowledge_build_settings_only_requires_embedding_config():
    """Knowledge-build should require only embedding-related configuration."""
    settings = Settings(
        EMBEDDING_MODEL_NAME="bge-m3",
        EMBEDDING_BASE_URL="https://embedding.example.com",
        EMBEDDING_DIMENSION=1024,
    )

    validate_knowledge_build_settings(settings)
