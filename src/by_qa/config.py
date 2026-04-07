"""Configuration for the open-source knowledge-base service."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    agent_data_path: Path = Field(default=Path("agent_data"), alias="AGENT_DATA_PATH")

    kb_opengauss_dsn: str = Field(default="", alias="KB_OPENGAUSS_DSN")
    kb_minio_endpoint: str = Field(default="127.0.0.1:19000", alias="KB_MINIO_ENDPOINT")
    kb_minio_access_key: str = Field(default="", alias="KB_MINIO_ACCESS_KEY")
    kb_minio_secret_key: str = Field(default="", alias="KB_MINIO_SECRET_KEY")
    kb_minio_bucket: str = Field(default="knowledge-base", alias="KB_MINIO_BUCKET")
    kb_minio_markdown_bucket: str = Field(
        default="knowledge-base-markdown", alias="KB_MINIO_MARKDOWN_BUCKET"
    )
    kb_minio_secure: bool = Field(default=False, alias="KB_MINIO_SECURE")

    embedding_model_name: str = Field(default="", alias="EMBEDDING_MODEL_NAME")
    embedding_base_url: str = Field(default="", alias="EMBEDDING_BASE_URL")
    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    embedding_dimension: int = Field(default=0, alias="EMBEDDING_DIMENSION")
    embedding_distance_metric: str = Field(
        default="cosine", alias="EMBEDDING_DISTANCE_METRIC"
    )

    kb_search_top_k: int = Field(default=10, alias="KB_SEARCH_TOP_K")
    kb_search_score_threshold: float = Field(
        default=0.6, alias="KB_SEARCH_SCORE_THRESHOLD"
    )

    kb_import_max_part_size: int = Field(
        default=8 * 1024 * 1024, alias="KB_IMPORT_MAX_PART_SIZE"
    )
    kb_fetch_cache_ttl_seconds: int = Field(
        default=24 * 60 * 60, alias="KB_FETCH_CACHE_TTL_SECONDS"
    )
    kb_fetch_cache_cleanup_interval_seconds: int = Field(
        default=10 * 60, alias="KB_FETCH_CACHE_CLEANUP_INTERVAL_SECONDS"
    )

    @property
    def logs_path(self) -> Path:
        """Get logs storage path."""
        return self.agent_data_path / "logs"

    @property
    def kb_cache_path(self) -> Path:
        """Get fetched-file cache path."""
        return self.agent_data_path / "kb_cache"

    def ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        self.agent_data_path.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True, exist_ok=True)
        self.kb_cache_path.mkdir(parents=True, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()
    settings.ensure_directories()
    return settings
